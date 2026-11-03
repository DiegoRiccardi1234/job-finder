"""Settings and diagnostics for explicitly selected AI connections."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Literal, cast

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.providers.connection_routing import provider_for
from app.routers.agent import require_local_request, require_ui_session

if TYPE_CHECKING:
    from app.container import AppContainer


class ConnectionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, max_length=100)
    kind: str | None = None
    base_url: str | None = Field(default=None, max_length=2048)
    model: str | None = Field(default=None, max_length=200)
    enabled: bool | None = None
    api_key: str | None = Field(default=None, max_length=8192)


class RouteChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    primary: str | None = None
    fallbacks: list[str] = Field(default_factory=list, max_length=16)


def build_router(container: AppContainer) -> APIRouter:
    router = APIRouter(prefix="/api/ai", tags=["AI connections"])
    runtime_status: dict[str, Any] = {}

    @router.get("/connections")
    def connections(request: Request, response: Response) -> dict[str, Any]:
        require_local_request(request)
        response.headers["Cache-Control"] = "no-store"
        try:
            registry = container.providers.connections
            return {
                "connections": registry.public_connections(container.settings),
                "routing": registry.routing(),
                "runtime_status": runtime_status,
            }
        except ValueError as exc:
            raise HTTPException(409, detail=str(exc)) from None

    @router.put("/connections/{connection_id}")
    def save_connection(
        request: Request, connection_id: str, payload: ConnectionPatch
    ) -> dict[str, Any]:
        require_ui_session(request, container)
        try:
            row = container.providers.connections.save(
                connection_id, payload.model_dump(exclude_unset=True)
            )
        except ValueError as exc:
            raise HTTPException(400, detail=str(exc)) from None
        container.providers.invalidate_caches()
        public = next(
            c
            for c in container.providers.connections.public_connections(container.settings)
            if c["id"] == row["id"]
        )
        return {"ok": True, "connection": public}

    @router.delete("/connections/{connection_id}")
    def delete_connection(request: Request, connection_id: str) -> dict[str, Any]:
        require_ui_session(request, container)
        try:
            container.providers.connections.delete(connection_id)
        except ValueError as exc:
            raise HTTPException(409, detail=str(exc)) from None
        container.providers.invalidate_caches()
        return {"ok": True}

    @router.put("/routing")
    def routing(request: Request, payload: dict[str, RouteChoice]) -> dict[str, Any]:
        require_ui_session(request, container)
        try:
            saved = container.providers.connections.save_routing(
                {task: value.model_dump() for task, value in payload.items()}, container.settings
            )
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, detail=str(exc)) from None
        container.providers.invalidate_caches()
        return {"ok": True, "routing": saved}

    @router.get("/runtimes/{kind}/status")
    def runtime(request: Request, kind: str, response: Response) -> dict[str, Any]:
        require_ui_session(request, container)
        response.headers["Cache-Control"] = "no-store"
        if kind not in ("codex", "claude"):
            raise HTTPException(404, detail="unknown_runtime")
        from app.agent_runtime import get_runtime_status

        status = get_runtime_status(cast("Literal['codex', 'claude']", kind))
        runtime_status[kind] = status
        return status

    @router.post("/connections/{connection_id}/test")
    def test_connection(request: Request, connection_id: str) -> dict[str, Any]:
        require_ui_session(request, container)
        # This is deliberately the only diagnostics endpoint that performs an
        # inference. It requires an explicit user gesture and never fails over.
        started = time.monotonic()
        try:
            prompt = 'Return only the JSON object {"ok": true}. Do not use tools.'
            if connection_id.startswith("api:"):
                name = connection_id[4:]
                provider = container.providers.providers.get(name)
                if provider is None or not provider.is_available():
                    raise ValueError("connection_not_configured")
                value = container.providers.complete_json(
                    prompt, max_tokens=100, provider_name=name, task="cv"
                )
            else:
                row = container.providers.connections.get(connection_id)
                if row is None:
                    raise ValueError("unknown_connection")
                provider = provider_for(container.providers, connection_id)
                reset = getattr(provider, "reset_cancel", None)
                if callable(reset):
                    reset()
                value = provider.complete_json(
                    prompt, model=row.get("model") or None, max_tokens=100
                )
            if not isinstance(value, dict) or value.get("ok") is not True:
                raise ValueError("invalid_test_response")
        except Exception as exc:
            # Provider exception messages may contain endpoints, API keys or
            # response bodies. Only stable error categories cross the boundary.
            code = str(getattr(exc, "code", "connection_test_failed"))
            return {
                "ok": False,
                "error": code,
                "duration_ms": int((time.monotonic() - started) * 1000),
            }
        return {"ok": True, "duration_ms": int((time.monotonic() - started) * 1000)}

    return router
