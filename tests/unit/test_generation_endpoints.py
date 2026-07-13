"""Tests for the interview-prep and tailored-resume generation endpoints."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    (tmp_path / "web").mkdir(exist_ok=True)
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    for key in ("CEREBRAS_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY"):
        os.environ.pop(key, None)

    from app.main import create_app

    app = create_app(workspace_dir=tmp_path)
    with TestClient(app) as tc:
        yield tc


def _seed_job_with_analysis(tmp_path: Path) -> int:
    from app.db import Database

    db = Database(tmp_path / "data" / "searcher.db")
    job_id, _, _ = db.upsert_job(
        {"titolo": "Backend Dev", "azienda": "Acme", "link": "https://x/1"}
    )
    db.update_job_analysis(job_id, {"punteggio": 8, "riassunto": "ok"})
    db.close()
    return job_id


def test_interview_prep_success(client: TestClient, tmp_path: Path, monkeypatch) -> None:
    job_id = _seed_job_with_analysis(tmp_path)
    monkeypatch.setattr(
        "app.routers.jobs.generate_with_profile",
        lambda *a, **k: "## Domande tecniche\n1. Q?",
    )
    resp = client.post(f"/api/jobs/{job_id}/interview-prep")
    assert resp.status_code == 200
    assert "Domande tecniche" in resp.json()["interview_prep"]

    # Persisted into analysis_json so the detail view can re-render it.
    detail = client.get(f"/api/jobs/{job_id}").json()
    assert detail["job"]["analysis"]["interview_prep"].startswith("## Domande")


def test_tailored_resume_success(client: TestClient, tmp_path: Path, monkeypatch) -> None:
    job_id = _seed_job_with_analysis(tmp_path)
    monkeypatch.setattr(
        "app.routers.jobs.generate_with_profile",
        lambda *a, **k: "# CV ottimizzato",
    )
    resp = client.post(f"/api/jobs/{job_id}/tailored-resume")
    assert resp.status_code == 200
    assert resp.json()["tailored_resume"] == "# CV ottimizzato"


def test_generation_404_when_job_missing(client: TestClient) -> None:
    assert client.post("/api/jobs/999999/interview-prep").status_code == 404


def test_generation_403_when_feature_disabled(client: TestClient, tmp_path: Path) -> None:
    client.post("/api/preferences", json={"key": "feature_interview_prep", "value": "0"})
    job_id = _seed_job_with_analysis(tmp_path)
    resp = client.post(f"/api/jobs/{job_id}/interview-prep")
    assert resp.status_code == 403
    assert resp.json()["detail"]["feature"] == "interview_prep"


def test_feature_toggle_round_trips_via_preferences(client: TestClient) -> None:
    client.post("/api/preferences", json={"key": "feature_resume_tailoring", "value": "0"})
    prefs = client.get("/api/preferences").json()["preferences"]
    assert prefs["feature_resume_tailoring"] == "0"
    # ``json`` import kept for parity with sibling tests / future payloads.
    assert isinstance(json.dumps(prefs), str)
