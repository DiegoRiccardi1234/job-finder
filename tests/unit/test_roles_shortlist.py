"""Unit tests for the role shortlist persistence API.

Exercises the FastAPI route using TestClient so the helpers (currently inline
in ``app/main.py``; will be extracted in Fase 2) stay covered after refactor.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    # FastAPI mounts /web as static files; create a dummy dir to satisfy the mount.
    (tmp_path / "web").mkdir(exist_ok=True)
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    for key in ("OPENAI_API_KEY", "GROQ_API_KEY", "ANTHROPIC_API_KEY"):
        os.environ.pop(key, None)

    from app.main import create_app

    app = create_app(workspace_dir=tmp_path)
    with TestClient(app) as tc:
        yield tc


def test_shortlist_starts_empty(client: TestClient) -> None:
    res = client.get("/api/roles/shortlist")
    assert res.status_code == 200
    assert res.json() == {"roles": []}


def test_shortlist_adds_unique_case_insensitive(client: TestClient) -> None:
    client.post("/api/roles/shortlist", json={"roles": ["Python Developer"]})
    res = client.post(
        "/api/roles/shortlist",
        json={"roles": ["python developer", "Data Engineer"]},
    )
    assert res.status_code == 200
    roles = res.json()["roles"]
    # first case preserved, dup dropped, new one appended
    assert roles == ["Python Developer", "Data Engineer"]


def test_shortlist_strips_and_ignores_empty(client: TestClient) -> None:
    res = client.post("/api/roles/shortlist", json={"roles": ["  QA Tester  ", "", "   "]})
    assert res.json()["roles"] == ["QA Tester"]


def test_shortlist_delete_removes_role(client: TestClient) -> None:
    client.post(
        "/api/roles/shortlist",
        json={"roles": ["Python Developer", "Data Engineer"]},
    )
    res = client.delete("/api/roles/shortlist/python developer")
    assert res.status_code == 200
    assert res.json()["roles"] == ["Data Engineer"]


def test_shortlist_persists_between_requests(client: TestClient) -> None:
    client.post("/api/roles/shortlist", json={"roles": ["ML Engineer"]})
    res = client.get("/api/roles/shortlist")
    assert res.json()["roles"] == ["ML Engineer"]
