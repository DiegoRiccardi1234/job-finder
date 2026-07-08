"""Endpoints added in v1.5.6: CV review + job import."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    (tmp_path / "web").mkdir(exist_ok=True)
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    for key in ("CEREBRAS_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    # A key so require_provider passes; no real call is made (helpers are patched).
    monkeypatch.setenv("GROQ_API_KEY", "test-key")

    from app.main import create_app

    app = create_app(workspace_dir=tmp_path)
    with TestClient(app) as tc:
        yield tc


def _seed_profile(tmp_path: Path) -> int:
    from app.db import Database

    db = Database(tmp_path / "data" / "searcher.db")
    pid = db.save_candidate_profile(
        source_name="cv.pdf",
        markdown="# Mario\nPython, SQL",
        summary={"skills": ["python"]},
        content_hash="hash1",
        name="Mario Rossi",
    )
    db.set_active_profile(pid)
    db.close()
    return pid


def test_cv_review_403_when_disabled(client: TestClient) -> None:
    client.post("/api/preferences", json={"key": "feature_cv_review", "value": "0"})
    assert client.post("/api/profile/cv-review").status_code == 403


def test_cv_review_404_without_profile(client: TestClient) -> None:
    assert client.post("/api/profile/cv-review").status_code == 404


def test_cv_review_success(client: TestClient, tmp_path: Path, monkeypatch) -> None:
    _seed_profile(tmp_path)
    monkeypatch.setattr(
        "app.routers.profile.generate_with_profile",
        lambda *a, **k: "## Punti di forza\n- chiaro",
    )
    resp = client.post("/api/profile/cv-review")
    assert resp.status_code == 200
    assert "Punti di forza" in resp.json()["cv_review"]


def test_import_job_from_text(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.jobs.extract_job_fields",
        lambda *a, **k: {"titolo": "Dev", "azienda": "Acme", "descrizione": "d"},
    )
    monkeypatch.setattr("app.routers.jobs.analyze_offer", lambda **k: {"punteggio": 7})
    resp = client.post("/api/jobs/import", json={"text": "raw posting text"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["fetch_ok"] is False and body["used_fallback"] is False
    assert body["analysis"]["punteggio"] == 7


def test_import_job_requires_input(client: TestClient) -> None:
    assert client.post("/api/jobs/import", json={}).status_code == 422
