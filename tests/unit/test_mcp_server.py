"""Exercise the official SDK, including legacy desktop-client negotiation."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError

from app.db import Database
from app.mcp_server import RemoteDispatcher, create_mcp_http_app, create_mcp_server
from app.routers.agent import get_service
from app.services.scan_control import ScanControl


def test_sdk_exposes_typed_tools_without_human_approval_or_destructive_commands():
    calls = []

    def dispatch(name, arguments):
        calls.append((name, arguments))
        return {"jobs": [], "total": 0}

    async def run():
        async with Client(create_mcp_server(dispatch)) as client:
            tools = (await client.list_tools()).tools
            names = {tool.name for tool in tools}
            assert len(names) == 10
            assert "jobfinder_prepare_analysis" in names
            assert not any("confirm" in name or "delete" in name or "email" in name for name in names)
            result = await client.call_tool("jobfinder_list_jobs", {"limit": 2})
            assert not result.is_error
            assert result.structured_content == {"jobs": [], "total": 0}
            invalid = await client.call_tool("jobfinder_list_jobs", {"limit": 1000})
            assert invalid.is_error

    asyncio.run(run())
    assert len(calls) == 1
    assert calls[0][1]["limit"] == 2
    assert all(not callable(value) for value in calls[0][1].values())


def test_http_transport_requires_capability_and_serves_real_mcp(tmp_path: Path):
    db = Database(tmp_path / "isolated.db")
    container = SimpleNamespace(
        db=db, settings=SimpleNamespace(data_dir=tmp_path), providers=SimpleNamespace(),
        scan_control=ScanControl(), feature_enabled=lambda name, default=True: default,
    )
    server, mcp_app = create_mcp_http_app(container)

    @asynccontextmanager
    async def lifespan(app):
        async with server.session_manager.run():
            yield

    app = FastAPI(lifespan=lifespan)
    app.mount("/mcp", mcp_app)
    try:
        with TestClient(app, base_url="http://127.0.0.1:8123") as client:
            payload = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-11-25", "capabilities": {},
                "clientInfo": {"name": "pytest-desktop", "version": "1"},
            }}
            headers = {"Accept": "application/json, text/event-stream"}
            assert client.post("/mcp/", json=payload, headers=headers).status_code == 401
            headers["Authorization"] = f"Bearer {get_service(container).token()}"
            response = client.post("/mcp/", json=payload, headers=headers)
            assert response.status_code == 200
            assert response.json()["result"]["serverInfo"]["name"] == "Job Finder"
            headers["MCP-Protocol-Version"] = response.json()["result"]["protocolVersion"]
            tools = client.post("/mcp/", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers=headers)
            assert tools.status_code == 200
            assert len(tools.json()["result"]["tools"]) == 10
            malicious = client.post("/mcp/", json=payload, headers={**headers, "Origin": "https://evil.example"})
            assert malicious.status_code == 403
    finally:
        get_service(container).shutdown()
        db.close()


@pytest.mark.parametrize("url", [
    "https://evil.example", "http://127.0.0.1:8000/path", "http://user:secret@localhost:8000",
    "file:///tmp/data", "http://localhost:8000?redirect=evil",
])
def test_stdio_adapter_refuses_non_loopback_targets(tmp_path: Path, url: str):
    token_file = tmp_path / "token"
    token_file.write_text("a" * 64)
    with pytest.raises(ValueError):
        RemoteDispatcher(url, token_file)


def test_stdio_adapter_sends_capability_only_to_app_and_reports_safe_errors(tmp_path: Path):
    token_file = tmp_path / "token"
    token_file.write_text("a" * 64)
    dispatcher = RemoteDispatcher("http://127.0.0.1:8000", token_file)
    dispatcher.client.close()

    def handle(request):
        assert request.headers["Authorization"] == f"Bearer {'a' * 64}"
        assert request.url.path == "/api/agent/tools/jobfinder_get_context"
        return httpx.Response(409, json={"detail": "profile_changed_prepare_again"})

    dispatcher.client = httpx.Client(transport=httpx.MockTransport(handle), headers={"Authorization": "Bearer " + "a" * 64})
    try:
        with pytest.raises(ToolError, match="profile_changed"):
            dispatcher("jobfinder_get_context", {})
    finally:
        dispatcher.close()


@pytest.mark.parametrize("failure", ["unreachable", "stale_context", "private_detail"])
def test_sdk_preserves_expected_actionable_errors_without_leaking_details(tmp_path, failure):
    token = tmp_path / "token"
    token.write_text("a" * 64)
    dispatcher = RemoteDispatcher("http://127.0.0.1:8000", token)
    dispatcher.client.close()

    def respond(request):
        if failure == "unreachable":
            raise httpx.ConnectError("private-token-fixture", request=request)
        detail = "profile_changed_prepare_again" if failure == "stale_context" else "private-token-fixture"
        return httpx.Response(409, json={"detail": detail})

    dispatcher.client = httpx.Client(transport=httpx.MockTransport(respond))

    async def run():
        async with Client(create_mcp_server(dispatcher)) as client:
            result = await client.call_tool("jobfinder_list_jobs", {"limit": 1})
            assert result.is_error
            text = str(result.content)
            expected = {
                "unreachable": "jobfinder_unreachable_open_the_local_app",
                "stale_context": "profile_changed_prepare_again",
                "private_detail": "invalid_request",
            }[failure]
            assert expected in text
            assert "private-token-fixture" not in text
            assert "a" * 64 not in text

    try:
        asyncio.run(run())
    finally:
        dispatcher.close()
