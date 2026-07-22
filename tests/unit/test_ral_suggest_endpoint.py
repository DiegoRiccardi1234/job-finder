"""/api/profile/ral-suggest — one capable-model call, parsed and cached.

The two figures ARE the deliverable (they prefill the form), so a reply the
parser can't read must fail loudly instead of silently returning prose.
"""

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
    for key in ("CEREBRAS_API_KEY", "GROQ_API_KEY"):
        os.environ.pop(key, None)
    # The endpoint requires a configured provider (like cv-review); generation
    # itself is stubbed in every test, so no call ever leaves the process.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-used")

    from app.main import create_app

    app = create_app(workspace_dir=tmp_path)
    with TestClient(app) as tc:
        yield tc


def _seed_profile(tmp_path: Path) -> int:
    from app.db import Database

    db = Database(tmp_path / "data" / "searcher.db")
    pid = db.save_candidate_profile(
        source_name="cv.pdf",
        markdown="# Diego\nLaurea triennale 95/110, 6 mesi di esperienza AI.",
        summary={"skills": ["python"]},
    )
    db.set_active_profile(pid)
    db.close()
    return pid


def _stub_generation(monkeypatch, reply: str) -> None:
    from app.routers import profile as profile_router

    monkeypatch.setattr(profile_router, "generate_with_profile", lambda *a, **k: reply)


def test_suggest_parses_and_caches(client: TestClient, tmp_path: Path, monkeypatch) -> None:
    _seed_profile(tmp_path)
    _stub_generation(monkeypatch, "MIN=30000 TARGET=38000\n\nSei junior ma con esperienza LLM.")

    res = client.post("/api/profile/ral-suggest")
    assert res.status_code == 200
    body = res.json()
    assert (body["min"], body["target"]) == (30000, 38000)
    assert body["rationale"].startswith("Sei junior")
    assert "MIN=" not in body["rationale"]  # the machine line is stripped

    # Second read comes from cache — no generation call at all.
    _stub_generation(monkeypatch, "MIN=1 TARGET=2")
    cached = client.get("/api/profile/ral-suggest").json()
    assert (cached["min"], cached["target"]) == (30000, 38000)


def test_thousands_shorthand_is_expanded(client: TestClient, tmp_path: Path, monkeypatch) -> None:
    _seed_profile(tmp_path)
    _stub_generation(monkeypatch, "MIN=30 TARGET=38\nMotivazione.")
    body = client.post("/api/profile/ral-suggest").json()
    assert (body["min"], body["target"]) == (30000, 38000)


def test_unparseable_reply_is_an_error(client: TestClient, tmp_path: Path, monkeypatch) -> None:
    _seed_profile(tmp_path)
    _stub_generation(monkeypatch, "Dipende da molti fattori, non posso dirlo.")
    assert client.post("/api/profile/ral-suggest").status_code == 502


def test_no_profile_is_404(client: TestClient) -> None:
    assert client.post("/api/profile/ral-suggest").status_code == 404


def test_cached_endpoint_empty_by_default(client: TestClient, tmp_path: Path) -> None:
    _seed_profile(tmp_path)
    body = client.get("/api/profile/ral-suggest").json()
    assert body == {"min": None, "target": None, "rationale": ""}


def test_market_context_uses_the_users_own_jobs(tmp_path: Path) -> None:
    """recent_ral_estimates feeds the prompt with this market, not an average."""
    from app.db import Database

    db = Database(tmp_path / "m.db")
    try:
        for i, ral in enumerate(["30.000€-45.000€", "Non stimabile", "30.000€-45.000€", "22k-25k"]):
            job_id, _, _ = db.upsert_job({"titolo": f"T{i}", "azienda": "Co", "link": f"l{i}"})
            db.update_job_analysis(job_id=job_id, analysis={"punteggio": 5, "ral_stimata": ral})
        estimates = db.recent_ral_estimates()
        assert "Non stimabile" not in estimates
        assert estimates.count("30.000€-45.000€") == 1  # deduplicated
        assert "22k-25k" in estimates
    finally:
        db.close()


def test_cache_payload_shape(client: TestClient, tmp_path: Path, monkeypatch) -> None:
    _seed_profile(tmp_path)
    _stub_generation(monkeypatch, "MIN=31000 TARGET=39000\nPerche' si.")
    client.post("/api/profile/ral-suggest")

    from app.db import Database

    db = Database(tmp_path / "data" / "searcher.db")
    try:
        cached = json.loads(db.get_preference("ral_suggestion_cache", "{}"))
    finally:
        db.close()
    assert cached["min"] == 31000
    assert cached["target"] == 39000
    assert "profile_id" in cached
