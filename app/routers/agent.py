"""Local agent API. Browser approval credentials are separate from MCP tokens."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.agent_tools import AgentService, AgentToolError

if TYPE_CHECKING:
    from app.container import AppContainer

_COOKIE = "jobfinder_agent_ui"
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def get_service(container: AppContainer) -> AgentService:
    service: AgentService | None = getattr(container, "agent_tools", None)
    if service is None:
        service = AgentService(container)
        container.agent_tools = service  # type: ignore[attr-defined]
    return service


def require_local_request(request: Request, *, origin_required: bool = False) -> None:
    """Block DNS rebinding and browser requests from a different origin."""
    if request.url.hostname not in _LOCAL_HOSTS:
        raise HTTPException(403, detail="local_host_required")
    origin = request.headers.get("origin")
    if origin:
        parsed = urlsplit(origin)
        if (
            parsed.scheme != request.url.scheme
            or parsed.netloc != request.url.netloc
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise HTTPException(403, detail="same_origin_required")
    elif origin_required:
        raise HTTPException(403, detail="origin_required")


def require_ui_session(request: Request, container: AppContainer) -> None:
    mutating = request.method not in {"GET", "HEAD"}
    require_local_request(request, origin_required=mutating)
    csrf = request.headers.get("x-jobfinder-csrf", "") if mutating else None
    if mutating and not csrf:
        raise HTTPException(403, detail="csrf_required")
    service = get_service(container)
    if not service.valid_ui_session(request.cookies.get(_COOKIE, ""), csrf):
        raise HTTPException(403, detail="ui_session_required")


def require_mcp_token(request: Request, container: AppContainer) -> None:
    require_local_request(request)
    authorization = request.headers.get("authorization", "")
    bearer = authorization[7:] if authorization.startswith("Bearer ") else ""
    if not get_service(container).authenticated(bearer):
        raise HTTPException(
            401, detail="agent_token_required", headers={"WWW-Authenticate": "Bearer"}
        )


class ToolRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)


def build_router(container: AppContainer) -> APIRouter:
    router = APIRouter(prefix="/api/agent", tags=["local agents"])

    @router.post("/ui/session")
    def ui_session(request: Request, response: Response) -> dict[str, Any]:
        require_local_request(request, origin_required=True)
        if request.headers.get("x-jobfinder-ui") != "1":
            raise HTTPException(403, detail="ui_header_required")
        try:
            session, csrf = get_service(container).ui_session()
        except AgentToolError as exc:
            raise HTTPException(exc.status_code, detail=exc.code) from None
        response.set_cookie(
            _COOKIE,
            session,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
            max_age=12 * 3600,
            path="/",
        )
        response.headers["Cache-Control"] = "no-store"
        return {"csrf_token": csrf}

    @router.get("/status")
    def status(request: Request) -> dict[str, Any]:
        require_ui_session(request, container)
        return {
            "enabled": True,
            "endpoint": "/mcp/",
            "pending_proposals": len(get_service(container).list_proposals()),
        }

    @router.post("/setup")
    def setup(request: Request, response: Response) -> dict[str, Any]:
        require_ui_session(request, container)
        response.headers["Cache-Control"] = "no-store"
        try:
            return get_service(container).setup(f"{request.url.scheme}://{request.url.netloc}")
        except AgentToolError as exc:
            raise HTTPException(exc.status_code, detail=exc.code) from None

    @router.get("/proposals")
    def proposals(request: Request) -> dict[str, Any]:
        require_ui_session(request, container)
        return {"proposals": get_service(container).list_proposals()}

    def decide(request: Request, proposal_id: str, accept: bool) -> dict[str, Any]:
        require_ui_session(request, container)
        try:
            return get_service(container).decide_proposal(proposal_id, accept)
        except AgentToolError as exc:
            raise HTTPException(exc.status_code, detail=exc.code) from None

    @router.post("/proposals/{proposal_id}/confirm")
    def confirm(request: Request, proposal_id: str) -> dict[str, Any]:
        return decide(request, proposal_id, True)

    @router.post("/proposals/{proposal_id}/reject")
    def reject(request: Request, proposal_id: str) -> dict[str, Any]:
        return decide(request, proposal_id, False)

    @router.post("/tools/{name}")
    def tool(request: Request, name: str, payload: ToolRequest) -> dict[str, Any]:
        require_mcp_token(request, container)
        try:
            return get_service(container).dispatch(name, payload.arguments)
        except AgentToolError as exc:
            raise HTTPException(exc.status_code, detail=exc.code) from None

    return router
