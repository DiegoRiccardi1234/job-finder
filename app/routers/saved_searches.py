from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException

from app.models import SavedSearchCreate

if TYPE_CHECKING:
    from app.container import AppContainer


def build_router(container: AppContainer) -> APIRouter:
    router = APIRouter()

    @router.get("/api/saved-searches")
    def list_saved_searches() -> dict[str, Any]:
        return {"searches": container.db.list_saved_searches()}

    @router.post("/api/saved-searches", status_code=201)
    def create_saved_search(payload: SavedSearchCreate) -> dict[str, Any]:
        name = (payload.name or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="empty_name")
        search_id = container.db.create_saved_search(name, payload.config)
        return {"id": search_id, "name": name}

    @router.delete("/api/saved-searches/{search_id}")
    def delete_saved_search(search_id: int) -> dict[str, Any]:
        if not container.db.delete_saved_search(search_id):
            raise HTTPException(status_code=404, detail="not_found")
        return {"ok": True, "deleted_id": search_id}

    return router
