from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import load_settings
from app.db import Database
from app.providers.factory import ProviderManager
from app.routers import agent, ai_connections


@pytest.fixture
def ai_client(tmp_path):
    settings = load_settings(tmp_path)
    db = Database(settings.db_path)
    manager = ProviderManager(settings)
    container = SimpleNamespace(settings=settings, providers=manager, db=db,
                                feature_enabled=lambda *_: True)
    app = FastAPI()
    app.include_router(agent.build_router(container))
    app.include_router(ai_connections.build_router(container))
    with TestClient(app, base_url="http://127.0.0.1:8123") as client:
        yield client, container
    tools = getattr(container, "agent_tools", None)
    if tools:
        tools.shutdown()
    db.close()


def _session(client: TestClient) -> dict[str, str]:
    headers = {"Origin": "http://127.0.0.1:8123", "X-JobFinder-UI": "1"}
    result = client.post("/api/agent/ui/session", headers=headers)
    assert result.status_code == 200, result.text
    return {"Origin": headers["Origin"], "X-JobFinder-CSRF": result.json()["csrf_token"]}


def test_mutations_require_browser_session_origin_and_csrf(ai_client) -> None:
    client, _ = ai_client
    assert client.put("/api/ai/connections/codex", json={"enabled": True}).status_code == 403
    headers = _session(client)
    assert client.put("/api/ai/connections/codex", headers=headers, json={"enabled": True}).status_code == 200
    wrong = {**headers, "Origin": "https://evil.example"}
    assert client.put("/api/ai/connections/codex", headers=wrong, json={"enabled": False}).status_code == 403
    assert client.put("/api/ai/routing", headers=headers, json={"chat": {"primary": "codex"}}).status_code == 200
    assert client.delete("/api/ai/connections/codex", headers=headers).status_code == 409


def test_endpoint_keys_never_leave_settings_api_and_omission_preserves_them(ai_client) -> None:
    client, container = ai_client
    headers = _session(client)
    cid = "endpoint-hermes"
    payload = {"name": "Hermes", "kind": "openai_compatible", "base_url": "http://127.0.0.1:8642/v1",
               "model": "hermes-agent", "enabled": True, "api_key": "private-never-return"}
    saved = client.put(f"/api/ai/connections/{cid}", headers=headers, json=payload)
    assert saved.status_code == 200
    assert "private-never-return" not in saved.text
    assert "private-never-return" not in client.get("/api/ai/connections").text
    assert client.put(f"/api/ai/connections/{cid}", headers=headers, json={"name": "Renamed"}).status_code == 200
    assert container.providers.connections.secret(cid) == "private-never-return"


def test_reading_connection_list_never_launches_or_tests_an_agent(ai_client, monkeypatch) -> None:
    client, _ = ai_client
    monkeypatch.setattr(ai_connections, "provider_for", lambda *_: pytest.fail("Unexpected inference"))
    result = client.get("/api/ai/connections")
    assert result.status_code == 200
    assert result.json()["routing"]["chat"]["primary"] is None
    assert "mcp-token" not in result.text


def test_synthetic_test_is_explicit_and_does_not_implicitly_enable_a_connection(ai_client, monkeypatch) -> None:
    client, container = ai_client
    calls: list[str] = []

    class Synthetic:
        def complete_json(self, prompt: str, **_: Any) -> dict[str, bool]:
            calls.append(prompt)
            return {"ok": True}

    monkeypatch.setattr(ai_connections, "provider_for", lambda *_: Synthetic())
    assert client.post("/api/ai/connections/codex/test").status_code == 403
    result = client.post("/api/ai/connections/codex/test", headers=_session(client))
    assert result.json()["ok"] is True
    assert len(calls) == 1 and "synthetic" not in str(container.providers.connections.routing())
    assert not container.providers.connections.get("codex")["enabled"]


def test_runtime_status_requires_session_and_preserves_unknown_quota(ai_client, monkeypatch) -> None:
    client, _ = ai_client
    monkeypatch.setattr("app.agent_runtime.get_runtime_status", lambda _: {
        "installed": True, "authenticated": True, "plan": "max", "quota": None})
    assert client.get("/api/ai/runtimes/claude/status").status_code == 403
    _session(client)
    result = client.get("/api/ai/runtimes/claude/status")
    assert result.status_code == 200
    assert result.json()["quota"] is None


def test_codex_only_routes_roundtrip_for_all_tasks_without_adding_fallbacks(ai_client) -> None:
    client, container = ai_client
    headers = _session(client)
    assert client.put("/api/ai/connections/codex", headers=headers,
                      json={"enabled": True}).status_code == 200
    routing = {task: {"primary": "codex", "fallbacks": []}
               for task in ("chat", "scoring", "cv")}
    response = client.put("/api/ai/routing", headers=headers, json=routing)
    assert response.status_code == 200
    assert response.json()["routing"] == routing
    assert client.get("/api/ai/connections").json()["routing"] == routing
    for task in routing:
        assert container.providers.connections.route(task) == ["codex"]
