from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app import rate_limit
from app.models import ScanRequest
from app.services.scanner_service import run_scan

if TYPE_CHECKING:
    from app.container import AppContainer


def build_router(container: AppContainer) -> APIRouter:
    router = APIRouter()

    @router.get("/api/scan/stream")
    def scan_stream(
        request: Request,
        search_terms: str = Query(default=""),
        location: str | None = Query(default=None),
        is_remote: bool = Query(default=False),
        sites: str = Query(default="linkedin,indeed"),
        experience_levels: str = Query(default=""),
        job_types: str = Query(default=""),
        work_types: str = Query(default=""),
        min_salary: int = Query(default=0),
    ) -> StreamingResponse:
        # Same guards as POST /api/scan: a direct hit or the pre-banner race
        # must not kick off an unauthenticated, provider-less scrape loop.
        rate_limit.check(request, bucket="scan", limit=5, window_seconds=60)
        container.require_provider()
        term_list = (
            [t.strip() for t in search_terms.split(",") if t.strip()] if search_terms else []
        )
        site_list = [s.strip() for s in sites.split(",") if s.strip()]

        def _split(csv: str) -> list[str]:
            return [s.strip() for s in csv.split(",") if s.strip()] if csv else []

        payload = ScanRequest(
            search_terms=term_list,
            location=location,
            is_remote=is_remote,
            sites=site_list,
            experience_levels=_split(experience_levels),
            job_types=_split(job_types),
            work_types=_split(work_types),
            min_salary=min_salary or None,
        )

        def event_generator() -> Iterator[str]:
            import json

            try:
                for event in run_scan(
                    db=container.db,
                    settings=container.settings,
                    provider_manager=container.providers,
                    payload=payload,
                ):
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    @router.post("/api/scan")
    def scan(request: Request, payload: ScanRequest) -> dict[str, Any]:
        rate_limit.check(request, bucket="scan", limit=5, window_seconds=60)
        container.require_provider()
        result = {}
        for event in run_scan(
            db=container.db,
            settings=container.settings,
            provider_manager=container.providers,
            payload=payload,
        ):
            if "status" in event and event["status"] == "complete":
                result = event
            elif "error" in event:
                raise HTTPException(status_code=500, detail=event["error"])
        return result

    return router
