from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from starlette.concurrency import run_in_threadpool

from app import rate_limit
from app.cv_ingest import (
    InvalidCVContent,
    extract_candidate_name,
    extract_markdown_from_upload,
    summarize_profile,
    summarize_profile_with_llm,
    validate_cv_content,
)
from app.models import (
    LinkedinSaveRequest,
    ProfileFromTextRequest,
    ProfileUpdate,
    RoleShortlistRequest,
)
from app.services import roles_shortlist as roles_shortlist_svc
from app.services.generation import CV_POLICY, generate_with_profile
from app.services.onboarding import onboarding_context

if TYPE_CHECKING:
    from app.container import AppContainer


def build_router(container: AppContainer) -> APIRouter:
    router = APIRouter()

    @router.post("/api/upload-cv")
    async def upload_cv(
        request: Request, file: UploadFile = File(...), lang: str | None = None
    ) -> dict[str, Any]:
        rate_limit.check(request, bucket="upload_cv", limit=10, window_seconds=60)
        MAX_CV_BYTES = 5 * 1024 * 1024  # 5 MB
        ALLOWED_EXTS = {
            ".pdf",
            ".docx",
            ".md",
            ".markdown",
            ".txt",
            # Image formats handled via OCR (Tesseract).
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
            ".avif",
            ".tiff",
            ".tif",
            ".bmp",
            ".svg",
        }

        filename = file.filename or "cv"
        ext = Path(filename).suffix.lower()
        if ext and ext not in ALLOWED_EXTS:
            raise HTTPException(status_code=415, detail=f"Unsupported CV type: {ext}")

        if file.size is not None and file.size > MAX_CV_BYTES:
            raise HTTPException(status_code=413, detail="CV file too large (max 5 MB)")

        data = await file.read()
        if len(data) > MAX_CV_BYTES:
            raise HTTPException(status_code=413, detail="CV file too large (max 5 MB)")
        if not data:
            raise HTTPException(status_code=400, detail="Empty CV file")
        # OCR / PDF parsing is blocking (CPU + subprocess); run it off the event
        # loop so a heavy upload can't freeze every other request.
        markdown = await run_in_threadpool(extract_markdown_from_upload, filename, data)
        try:
            validate_cv_content(markdown)
        except InvalidCVContent as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        content_hash = hashlib.sha256(data).hexdigest()
        existing_id = container.db.find_candidate_profile_by_hash(content_hash)
        if existing_id is not None:
            container.db.set_active_profile(existing_id)
            existing = container.db.get_candidate_profile(existing_id)
            existing_summary = (existing or {}).get("summary_json") or {}
            return {
                "profile_id": existing_id,
                "source": file.filename,
                "summary": existing_summary,
                "deduplicated": True,
            }

        summary_method = "heuristic"
        retry_count = 0

        def _track_retry(attempt: int, wait: float, exc: Exception) -> None:
            nonlocal retry_count
            retry_count = attempt

        try:
            # Blocking too (retry sleeps + sync HTTP to the LLM); offload it.
            summary = await run_in_threadpool(
                summarize_profile_with_llm,
                markdown,
                container.providers,
                on_retry=_track_retry,
                language=lang,
                privacy=container.feature_enabled("privacy_mode", True),
            )
            # If the result is structurally richer than the heuristic, mark as llm.
            if any(k in summary for k in ("strengths", "industries", "summary")):
                summary_method = "llm"
        except Exception as exc:
            container.log.warning("LLM CV summarization failed, using heuristic: %s", exc)
            summary = await run_in_threadpool(summarize_profile, markdown)
        candidate_name = summary.get("name") if isinstance(summary, dict) else None
        if not candidate_name:
            candidate_name = extract_candidate_name(markdown)
        profile_id = container.db.save_candidate_profile(
            source_name=file.filename or "cv_upload",
            markdown=markdown,
            summary=summary,
            content_hash=content_hash,
            name=candidate_name,
        )
        container.db.set_active_profile(profile_id)

        preferred_roles = summary.get("preferred_roles")
        if isinstance(preferred_roles, list) and preferred_roles:
            container.db.set_preference(
                "preferred_roles", ",".join(str(r) for r in preferred_roles)
            )

        return {
            "profile_id": profile_id,
            "source": file.filename,
            "summary": summary,
            "summary_method": summary_method,
            "retries": retry_count,
        }

    @router.get("/api/profile")
    def get_profile() -> dict[str, Any]:
        profile = container.db.get_active_candidate_profile()
        active = container.db.get_preference("active_profile_id", "")
        return {"profile": profile, "active_profile_id": active}

    def _resolve_lang(lang: str) -> str | None:
        return (lang or container.db.get_preference("ui_language", "") or "").lower() or None

    @router.get("/api/profile/cv-review")
    def cv_review_cached() -> dict[str, Any]:
        """Return the last CV review for the active profile (cached), so the panel
        rehydrates on open instead of re-generating (and re-spending tokens)."""
        profile = container.db.get_active_candidate_profile()
        cached = container.db.get_preference("cv_review_cache", "")
        if profile and cached:
            try:
                data = json.loads(cached)
                if data.get("profile_id") == profile.get("id"):
                    return {"cv_review": data.get("text", "")}
            except (json.JSONDecodeError, TypeError):
                pass
        return {"cv_review": ""}

    @router.post("/api/profile/cv-review")
    def cv_review(lang: str = Query(default="")) -> dict[str, Any]:
        """AI review of the active CV with actionable improvement advice.

        Uses the onboarding answers (target sector/goal) so the advice is aimed
        at what the user is looking for. Runs on a capable model (CV_POLICY) and
        follows the UI language. Honors Privacy Mode. Result is cached per profile.
        """
        container.require_feature("cv_review")
        profile = container.db.get_active_candidate_profile()
        if not profile:
            raise HTTPException(status_code=404, detail="no_profile")
        container.require_provider()
        try:
            content = generate_with_profile(
                container.providers,
                "cv_review",
                profile["markdown"],
                {},
                extra_block=onboarding_context(container.db),
                redact=container.feature_enabled("privacy_mode", True),
                candidate_name=profile.get("name"),
                language=_resolve_lang(lang),
                **container.providers.pin_kwargs(container.settings.cv_model, CV_POLICY),
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"CV review failed: {e}") from e
        container.db.set_preference(
            "cv_review_cache", json.dumps({"profile_id": profile.get("id"), "text": content})
        )
        return {"cv_review": content}

    @router.post("/api/profile/cv-improve")
    def cv_improve(lang: str = Query(default="")) -> dict[str, Any]:
        """Generate an improved, goal-aligned rewrite of the active CV (markdown).

        Runs on the capable CV model; restores real contacts (it's a document the
        user will send). Honors Privacy Mode + onboarding goals + UI language.
        """
        container.require_feature("cv_review")
        profile = container.db.get_active_candidate_profile()
        if not profile:
            raise HTTPException(status_code=404, detail="no_profile")
        container.require_provider()
        try:
            content = generate_with_profile(
                container.providers,
                "cv_improve",
                profile["markdown"],
                {},
                extra_block=onboarding_context(container.db),
                redact=container.feature_enabled("privacy_mode", True),
                candidate_name=profile.get("name"),
                language=_resolve_lang(lang),
                **container.providers.pin_kwargs(container.settings.cv_model, CV_POLICY),
                restore_contact_info=True,
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"CV improve failed: {e}") from e
        return {"cv_improved": content}

    @router.post("/api/profile/from-text")
    def profile_from_text(payload: ProfileFromTextRequest) -> dict[str, Any]:
        """Create a new CV profile from raw markdown (e.g. the AI-improved CV) and
        make it active. Summary is heuristic (no LLM) — fast; scoring uses the
        markdown text directly anyway."""
        markdown = (payload.markdown or "").strip()
        if len(markdown) < 50:
            raise HTTPException(status_code=400, detail="too_short")
        summary = summarize_profile(markdown)
        name = summary.get("name") or extract_candidate_name(markdown)
        profile_id = container.db.save_candidate_profile(
            source_name=payload.source_name or "CV (AI)",
            markdown=markdown,
            summary=summary,
            name=name,
        )
        container.db.set_active_profile(profile_id)
        return {"ok": True, "profile_id": profile_id, "active_profile_id": str(profile_id)}

    @router.patch("/api/profile")
    def update_profile(payload: ProfileUpdate) -> dict[str, Any]:
        profile = container.db.get_active_candidate_profile()
        if not profile:
            raise HTTPException(status_code=404, detail="no_profile")
        summary = dict(profile.get("summary_json") or {})
        if payload.preferred_roles is not None:
            cleaned = [r.strip() for r in payload.preferred_roles if r and r.strip()]
            summary["preferred_roles"] = cleaned
            container.db.set_preference("preferred_roles", json.dumps(cleaned, ensure_ascii=False))
        if payload.skills is not None:
            summary["skills"] = [s.strip() for s in payload.skills if s and s.strip()]
        if payload.languages is not None:
            summary["languages"] = [
                lang.strip() for lang in payload.languages if lang and lang.strip()
            ]
        if payload.name is not None and payload.name.strip():
            summary["name"] = payload.name.strip()
        container.db.update_candidate_profile_summary(int(profile["id"]), summary)
        # Manual edits to the display name / raw CV text (the latter feeds scoring).
        if (payload.name is not None and payload.name.strip()) or payload.markdown is not None:
            container.db.update_candidate_profile_fields(
                int(profile["id"]),
                markdown=payload.markdown.strip()
                if (payload.markdown is not None and payload.markdown.strip())
                else None,
                name=payload.name.strip()
                if (payload.name is not None and payload.name.strip())
                else None,
            )
        updated = container.db.get_active_candidate_profile()
        return {"ok": True, "profile": updated}

    @router.get("/api/profiles")
    def get_profiles() -> dict[str, Any]:
        profiles = container.db.list_candidate_profiles()
        active = container.db.get_preference("active_profile_id", "")
        return {"profiles": profiles, "active_profile_id": active}

    @router.post("/api/profiles/{profile_id}/activate")
    def activate_profile(profile_id: int) -> dict[str, Any]:
        profile = container.db.get_candidate_profile(profile_id)
        if not profile:
            raise HTTPException(status_code=404, detail="Profile not found")
        container.db.set_active_profile(profile_id)
        return {"ok": True, "active_profile_id": profile_id}

    @router.delete("/api/profiles/{profile_id}")
    def delete_profile(profile_id: int) -> dict[str, Any]:
        deleted = container.db.delete_candidate_profile(profile_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Profile not found")
        active_raw = container.db.get_preference("active_profile_id", "")
        return {"ok": True, "deleted_id": profile_id, "active_profile_id": active_raw}

    @router.post("/api/profile/linkedin")
    def save_linkedin(payload: LinkedinSaveRequest) -> dict[str, Any]:
        """Save the LinkedIn URL and, best-effort, the profile text for AI context.

        Pasted ``text`` wins (LinkedIn blocks most server-side fetches, like job
        import). Otherwise we try ``fetch_page_text(url)``; if it yields enough
        content we store it, else we keep only the URL and report ``fetched=False``
        so the UI can prompt the user to paste the text.
        """
        url = (payload.url or "").strip()
        text = (payload.text or "").strip()
        container.db.set_preference("linkedin_url", url)

        profile_text = ""
        fetched = False
        if text:
            profile_text = text
        elif url:
            from app.services.job_import import fetch_page_text

            page = fetch_page_text(url)
            if page and len(page) >= 400:
                profile_text = page
                fetched = True
        container.db.set_preference("linkedin_profile_text", profile_text)
        return {"ok": True, "fetched": fetched, "chars": len(profile_text)}

    @router.get("/api/roles/shortlist")
    def get_role_shortlist() -> dict[str, Any]:
        return {"roles": roles_shortlist_svc.load(container.db)}

    @router.post("/api/roles/shortlist")
    def add_role_shortlist(payload: RoleShortlistRequest) -> dict[str, Any]:
        return {"roles": roles_shortlist_svc.add(container.db, payload.roles or [])}

    @router.delete("/api/roles/shortlist/{role}")
    def remove_role_shortlist(role: str) -> dict[str, Any]:
        return {"roles": roles_shortlist_svc.remove(container.db, role)}

    return router
