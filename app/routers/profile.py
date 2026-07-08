from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
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
from app.models import ProfileUpdate, RoleShortlistRequest
from app.services import roles_shortlist as roles_shortlist_svc
from app.services.generation import generate_with_profile
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

    @router.post("/api/profile/cv-review")
    def cv_review() -> dict[str, Any]:
        """AI review of the active CV with actionable improvement advice.

        Uses the onboarding answers (target sector/goal) so the advice is aimed
        at what the user is looking for. Honors Privacy Mode: the CV is scrubbed
        before the LLM call, the name restored in the returned text.
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
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"CV review failed: {e}") from e
        return {"cv_review": content}

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
        container.db.update_candidate_profile_summary(int(profile["id"]), summary)
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
