"""An MCP capability can never approve its own human-gated proposals."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db import Database
from app.routers.agent import build_router, get_service
from app.services.scan_control import ScanControl


@pytest.fixture
def client(tmp_path: Path):
    db = Database(tmp_path / "isolated.db")
    profile = db.save_candidate_profile("synthetic", "Synthetic curriculum Python", {"skills": ["Python"]})
    db.set_active_profile(profile)
    container = SimpleNamespace(
        db=db, settings=SimpleNamespace(data_dir=tmp_path), providers=SimpleNamespace(),
        scan_control=ScanControl(), feature_enabled=lambda name, default=True: default,
    )
    app = FastAPI()
    app.include_router(build_router(container))
    with TestClient(app, base_url="http://127.0.0.1:8123") as tc:
        yield tc, container
    if getattr(container, "agent_tools", None):
        container.agent_tools.shutdown()
    db.close()


def session(tc):
    response = tc.post("/api/agent/ui/session", headers={
        "Origin": "http://127.0.0.1:8123", "X-JobFinder-UI": "1",
    })
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    return {"Origin": "http://127.0.0.1:8123", "X-JobFinder-CSRF": response.json()["csrf_token"]}


def test_setup_is_never_a_public_token_get_and_requires_session_csrf_origin(client):
    tc, container = client
    assert tc.get("/api/agent/setup").status_code == 405
    assert tc.post("/api/agent/setup").status_code == 403
    assert tc.post("/api/agent/ui/session", headers={
        "Origin": "https://evil.example", "X-JobFinder-UI": "1",
    }).status_code == 403
    headers = session(tc)
    assert tc.post("/api/agent/setup", headers={"Origin": "http://127.0.0.1:8123"}).status_code == 403
    response = tc.post("/api/agent/setup", headers=headers)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert Path(response.json()["token_file"]).is_file()
    token = get_service(container).token()
    assert token not in response.text
    assert "--token-file" in response.json()["configs"]["codex"]
    assert tc.get("/api/agent/status").json()["pending_proposals"] == 0


def test_mcp_bearer_does_not_grant_confirmation_or_ui_session(client):
    tc, container = client
    service = get_service(container)
    proposal = service.propose_change("profile", {"skills": ["Python", "SQL"]}, "proposal")
    headers = {"Authorization": f"Bearer {service.token()}", "Origin": "http://127.0.0.1:8123"}
    assert tc.post(f"/api/agent/proposals/{proposal['proposal_id']}/confirm", headers=headers).status_code == 403
    assert tc.get("/api/agent/proposals", headers=headers).status_code == 403
    assert tc.post("/api/agent/tools/jobfinder_get_context", json={"arguments": {}}, headers=headers).status_code == 200
    assert tc.post("/api/agent/tools/jobfinder_decide_proposal", json={"arguments": {}}, headers=headers).status_code == 404
    ui_headers = session(tc)
    assert tc.post(f"/api/agent/proposals/{proposal['proposal_id']}/confirm", headers=ui_headers).status_code == 200
    assert container.db.get_active_candidate_profile()["summary_json"]["skills"] == ["Python", "SQL"]


def test_foreign_origin_host_or_csrf_are_rejected_even_with_valid_credentials(client):
    tc, container = client
    headers = session(tc)
    assert tc.post("/api/agent/setup", headers={**headers, "Origin": "http://localhost:8123"}).status_code == 403
    assert tc.post("/api/agent/setup", headers={**headers, "Host": "evil.example"}).status_code == 403
    assert tc.post("/api/agent/setup", headers={**headers, "X-JobFinder-CSRF": "wrong"}).status_code == 403
    assert tc.post("/api/agent/tools/jobfinder_get_context", json={"arguments": {}}).status_code == 401
