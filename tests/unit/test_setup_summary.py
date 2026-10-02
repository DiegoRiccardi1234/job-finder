from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.system import build_router


def test_setup_summary_preserves_operational_provenance() -> None:
    scan = {"id": 17, "started_at": "2026-08-26T20:00:00", "terms_json": '["old query"]'}
    profile = {"id": 8, "source_name": "updated.pdf", "created_at": "2026-10-02T19:00:00"}
    db = SimpleNamespace(
        get_active_candidate_profile=lambda: profile,
        get_last_scan=lambda: scan,
        get_preference=lambda key, default: "Junior automation and applied AI",
        count_pending_applications=lambda: 3,
    )
    container = SimpleNamespace(db=db, has_provider_configured=lambda: True)
    app = FastAPI()
    app.include_router(build_router(container))
    with TestClient(app) as client:
        response = client.get("/api/setup/status")
    assert response.status_code == 200
    data = response.json()
    assert data["last_scan"] == scan
    assert data["active_cv"] == profile
    assert data["applications_pending_count"] == 3
    assert data["search_goal"] == "Junior automation and applied AI"
    assert data["ready"] and data["cv_loaded"] and not data["first_run"]


def test_setup_summary_empty_installation_is_not_ready() -> None:
    db = SimpleNamespace(
        get_active_candidate_profile=lambda: None,
        get_last_scan=lambda: None,
        get_preference=lambda key, default: default,
        count_pending_applications=lambda: 0,
    )
    app = FastAPI()
    app.include_router(build_router(SimpleNamespace(db=db, has_provider_configured=lambda: False)))
    with TestClient(app) as client:
        data = client.get("/api/setup/status").json()
    assert data["first_run"] and not data["ready"]
    assert data["last_scan"] is None and data["active_cv"] is None
    assert data["applications_pending_count"] == 0
