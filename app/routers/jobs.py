from __future__ import annotations

import csv
from datetime import datetime
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.models import FavoriteRequest, JobActionRequest, ManualJobCreateRequest
from app.services.generation import generate_with_profile
from app.services.scanner_service import analyze_offer
from app.services.skill_gap import compute_skill_gap

if TYPE_CHECKING:
    from app.container import AppContainer


def build_router(container: AppContainer) -> APIRouter:
    router = APIRouter()

    @router.get("/api/jobs")
    def list_jobs(
        status: str | None = Query(default=None),
        only_favorites: bool = Query(default=False),
        only_new: bool = Query(default=False),
        remote_only: bool = Query(default=False),
        search_text: str | None = Query(default=None),
        min_score: int | None = Query(default=None, ge=0, le=10),
        max_age_days: int | None = Query(default=None, ge=1, le=365),
        limit: int = Query(default=200, ge=1, le=2000),
    ) -> dict[str, Any]:
        jobs = container.db.list_jobs(
            status=status,
            only_favorites=only_favorites,
            only_new=only_new,
            remote_only=remote_only,
            search_text=search_text,
            min_score=min_score,
            max_age_days=max_age_days,
            limit=limit,
        )
        return {"jobs": jobs}

    @router.get("/api/jobs/{job_id}")
    def get_job_detail(job_id: int) -> dict[str, Any]:
        job = container.db.get_job_with_analysis(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        recruiter = container.db.get_recruiter(job_id)
        return {"job": job, "recruiter": recruiter}

    @router.post("/api/jobs/{job_id}/cover-letter")
    def generate_cover_letter(job_id: int) -> dict[str, Any]:
        job = container.db.get_job_with_analysis(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        profile = container.db.get_active_candidate_profile()
        profile_markdown = profile["markdown"] if profile else "CV non disponibile."

        linkedin_url = container.db.get_preference("linkedin_url", "")
        if linkedin_url:
            profile_markdown += f"\n\nProfilo LinkedIn: {linkedin_url}"

        titolo = job.get("titolo", "N/A")
        azienda = job.get("azienda", "N/A")
        descrizione = job.get("descrizione", "")

        recruiter = container.db.get_recruiter(job_id)
        if not recruiter and job.get("link") and "linkedin.com" in job.get("link", ""):
            try:
                from app.services.recruiter_scrape import fetch_recruiter

                fetched = fetch_recruiter(job["link"], timeout=3.0)
                if fetched:
                    container.db.upsert_recruiter(job_id, fetched)
                    recruiter = container.db.get_recruiter(job_id)
            except Exception as exc:
                container.log.debug("on-demand recruiter scrape failed: %s", exc)

        recruiter_block = ""
        if recruiter and (recruiter.get("name") or recruiter.get("headline")):
            parts = []
            if recruiter.get("name"):
                parts.append(f"Nome: {recruiter['name']}")
            if recruiter.get("title"):
                parts.append(f"Ruolo: {recruiter['title']}")
            if recruiter.get("headline"):
                parts.append(f"Headline: {recruiter['headline']}")
            recruiter_block = (
                "\nDESTINATARIO (recruiter / hiring manager visibile nell'annuncio):\n"
                + "\n".join(parts)
                + "\nApri la lettera con un saluto nominale rivolto a questa persona "
                "(es. 'Gentile {nome},') e fai un breve riferimento al suo ruolo."
            )

        try:
            cover_letter = generate_with_profile(
                container.providers,
                "cover_letter",
                profile_markdown,
                {"titolo": titolo, "azienda": azienda, "descrizione": descrizione},
                extra_block=recruiter_block,
            )
            container.db.save_cover_letter(job_id, cover_letter)
        except Exception as e:
            cover_letter = f"Error generating cover letter: {e}"

        return {"cover_letter": cover_letter}

    def _job_generation_context(job_id: int) -> tuple[dict[str, Any], str]:
        """Resolve (job_info, profile_markdown) for a generation endpoint.

        Raises 404 if the job is missing. Shared by interview-prep and
        resume-tailoring.
        """
        job = container.db.get_job_with_analysis(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        profile = container.db.get_active_candidate_profile()
        profile_markdown = profile["markdown"] if profile else "CV non disponibile."
        job_info = {
            "titolo": job.get("titolo", "N/A"),
            "azienda": job.get("azienda", "N/A"),
            "descrizione": job.get("descrizione", ""),
        }
        return job_info, profile_markdown

    @router.post("/api/jobs/{job_id}/interview-prep")
    def generate_interview_prep(job_id: int) -> dict[str, Any]:
        container.require_feature("interview_prep")
        job_info, profile_markdown = _job_generation_context(job_id)
        try:
            content = generate_with_profile(
                container.providers, "interview_prep", profile_markdown, job_info
            )
            container.db.save_job_analysis_field(job_id, "interview_prep", content)
        except Exception as e:
            raise HTTPException(
                status_code=502, detail=f"Interview prep generation failed: {e}"
            ) from e
        return {"interview_prep": content}

    @router.post("/api/jobs/{job_id}/tailored-resume")
    def generate_tailored_resume(job_id: int) -> dict[str, Any]:
        container.require_feature("resume_tailoring")
        job_info, profile_markdown = _job_generation_context(job_id)
        try:
            content = generate_with_profile(
                container.providers, "resume_tailoring", profile_markdown, job_info
            )
            container.db.save_job_analysis_field(job_id, "tailored_resume", content)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Resume tailoring failed: {e}") from e
        return {"tailored_resume": content}

    @router.get("/api/analytics")
    def get_analytics() -> dict[str, Any]:
        return container.db.get_analytics()

    @router.get("/api/skill-gap")
    def skill_gap() -> dict[str, Any]:
        container.require_feature("skill_gap")
        return compute_skill_gap(container.db)

    @router.get("/api/recommendations")
    def recommendations(limit: int = Query(default=5, ge=1, le=20)) -> dict[str, Any]:
        jobs = container.db.get_recommended_jobs(limit=limit)
        return {
            "jobs": jobs,
            "message": "Ecco i lavori prioritari da valutare e candidare.",
        }

    @router.post("/api/jobs/manual")
    def add_manual_job(payload: ManualJobCreateRequest) -> dict[str, Any]:
        row = {
            "titolo": payload.titolo,
            "azienda": payload.azienda,
            "descrizione": payload.descrizione,
            "sede": payload.sede,
            "fonte": payload.fonte,
            "link": payload.link,
            "ricerca_usata": payload.ricerca_usata,
            "modalita": payload.modalita,
        }
        job_id = container.db.add_manual_job(row)

        profile = container.db.get_active_candidate_profile()
        profile_markdown = profile["markdown"] if profile else "Profile not loaded"

        linkedin_url = container.db.get_preference("linkedin_url", "")
        if linkedin_url:
            profile_markdown += f"\n\nProfilo LinkedIn: {linkedin_url}"

        analysis = analyze_offer(
            provider_manager=container.providers,
            profile_markdown=profile_markdown,
            titolo=payload.titolo,
            azienda=payload.azienda,
            descrizione=payload.descrizione,
        )
        container.db.update_job_analysis(job_id=job_id, analysis=analysis)
        return {"job_id": job_id, "analysis": analysis}

    @router.post("/api/jobs/{job_id}/action")
    def set_job_action(job_id: int, payload: JobActionRequest) -> dict[str, Any]:
        job = container.db.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        container.db.set_job_action(job_id=job_id, action=payload.action.value, notes=payload.notes)
        return {"ok": True}

    @router.post("/api/jobs/{job_id}/favorite")
    def set_favorite(job_id: int, payload: FavoriteRequest) -> dict[str, Any]:
        job = container.db.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        container.db.set_favorite(job_id=job_id, is_favorite=payload.is_favorite)
        return {"ok": True}

    @router.delete("/api/jobs/{job_id}")
    def delete_job(job_id: int) -> dict[str, Any]:
        deleted = container.db.delete_job(job_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Job not found")
        return {"ok": True, "deleted_id": job_id}

    @router.delete("/api/jobs")
    def delete_all_jobs() -> dict[str, Any]:
        count = container.db.delete_all_jobs()
        return {"ok": True, "deleted": count}

    @router.get("/api/applications/export")
    def export_applications(format: str = "csv") -> StreamingResponse:
        cur = container.db.conn.cursor()
        raw_rows = cur.execute(
            "SELECT titolo, azienda, sede, status, punteggio_ai, consiglio, link, "
            "updated_at, first_seen_at FROM jobs WHERE status IN (?, ?, ?) "
            "ORDER BY updated_at DESC",
            ("applied", "interviewing", "rejected"),
        ).fetchall()

        records = [
            {
                "title": r[0] or "",
                "company": r[1] or "",
                "location": r[2] or "",
                "status": r[3] or "",
                "ai_score": r[4] or 0,
                "advice": r[5] or "",
                "url": r[6] or "",
                "updated_at": r[7] or "",
                "first_seen_at": r[8] or "",
            }
            for r in raw_rows
        ]

        fmt = (format or "csv").lower()
        if fmt == "json":
            import json as _json_export

            body = _json_export.dumps(records, ensure_ascii=False, indent=2)
            filename = f"applications_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
            return StreamingResponse(
                iter([body.encode("utf-8")]),
                media_type="application/json",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )

        # CSV
        from io import StringIO

        buf = StringIO()
        if records:
            writer = csv.DictWriter(buf, fieldnames=list(records[0].keys()), delimiter=";")
            writer.writeheader()
            writer.writerows(records)
        else:
            buf.write("")
        filename = f"applications_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
        return StreamingResponse(
            iter([buf.getvalue().encode("utf-8-sig")]),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.post("/api/export/csv")
    def export_csv() -> dict[str, Any]:
        rows = container.db.export_jobs_for_csv()
        if not rows:
            raise HTTPException(status_code=400, detail="No jobs to export")

        output_name = f"lavori_webapp_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
        output_path = container.workspace_dir / output_name
        columns = list(rows[0].keys())

        with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=columns, delimiter=";")
            writer.writeheader()
            writer.writerows(rows)

        return {"ok": True, "file": str(output_path)}

    return router
