from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter

from app.models import SchedulerConfigRequest

if TYPE_CHECKING:
    from app.container import AppContainer


def build_router(container: AppContainer) -> APIRouter:
    router = APIRouter()

    @router.get("/api/scheduler/status")
    def scheduler_status() -> dict[str, Any]:
        return container.autoscan.status()

    @router.post("/api/scheduler/config")
    def scheduler_config(payload: SchedulerConfigRequest) -> dict[str, Any]:
        if payload.enabled is not None:
            container.db.set_preference("autoscan_enabled", "1" if payload.enabled else "0")
        if payload.interval_hours is not None:
            container.db.set_preference("autoscan_interval_hours", str(payload.interval_hours))
        if payload.threshold is not None:
            container.db.set_preference("autoscan_score_threshold", str(payload.threshold))
        return {"ok": True, "status": container.autoscan.status()}

    @router.post("/api/scheduler/run-now", status_code=202)
    def scheduler_run_now() -> dict[str, Any]:
        # Run off-thread so the request returns immediately; progress is
        # observable via /api/scheduler/status (running + pending).
        threading.Thread(
            target=container.autoscan.run_once, name="autoscan-manual", daemon=True
        ).start()
        return {"status": "started"}

    @router.post("/api/scheduler/dismiss")
    def scheduler_dismiss() -> dict[str, Any]:
        container.autoscan.clear_pending()
        return {"ok": True}

    return router
