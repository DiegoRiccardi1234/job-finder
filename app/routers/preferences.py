from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter

from app.models import PreferenceUpdateRequest

if TYPE_CHECKING:
    from app.container import AppContainer


def build_router(container: AppContainer) -> APIRouter:
    router = APIRouter()

    @router.post("/api/preferences")
    def update_preference(payload: PreferenceUpdateRequest) -> dict[str, Any]:
        container.db.set_preference(payload.key, payload.value)
        return {"ok": True, "preferences": container.db.list_preferences()}

    @router.get("/api/preferences")
    def get_preferences() -> dict[str, Any]:
        return {"preferences": container.db.list_preferences()}

    return router
