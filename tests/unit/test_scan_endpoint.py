"""Guards on the scan endpoints (SSE stream must match POST: provider + rate limit)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    (tmp_path / "web").mkdir(exist_ok=True)
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    for key in (
        "CEREBRAS_API_KEY",
        "GROQ_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "OPENROUTER_API_KEY",
        "DEEPSEEK_API_KEY",
        "XAI_API_KEY",
        "GLM_API_KEY",
        "MISTRAL_API_KEY",
    ):
        os.environ.pop(key, None)

    from app.main import create_app

    app = create_app(workspace_dir=tmp_path)
    with TestClient(app) as tc:
        yield tc


def test_scan_stream_requires_provider(client: TestClient, monkeypatch) -> None:
    """GET /api/scan/stream must reject with 412 when no LLM provider is
    configured — same guard the POST endpoint has (protects against direct hits
    and the pre-banner race). ``run_scan`` is stubbed so no real scrape occurs."""
    monkeypatch.setattr("app.routers.scan.run_scan", lambda **k: iter([{"status": "complete"}]))
    r = client.get("/api/scan/stream?search_terms=python")
    assert r.status_code == 412
    assert r.json()["detail"]["code"] == "no_provider_configured"
