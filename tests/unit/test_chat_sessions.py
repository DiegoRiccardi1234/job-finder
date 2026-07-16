"""Chat session endpoints: lazy sessions must be renamable.

Sessions are created lazily (touch_chat_session, INSERT OR IGNORE): renaming
"default" before any message existed used to 404 even though the UI shows it
as a perfectly valid session.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    (tmp_path / "web").mkdir(exist_ok=True)
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GROQ_API_KEY", "test-key")

    from app.main import create_app

    app = create_app(workspace_dir=tmp_path)
    with TestClient(app) as tc:
        yield tc


def test_rename_lazy_session_succeeds(client: TestClient) -> None:
    resp = client.patch("/api/chat/sessions/default", json={"title": "Ricerca AI QA"})
    assert resp.status_code == 200
    sessions = client.get("/api/chat/sessions").json()["sessions"]
    by_id = {s["id"]: s for s in sessions}
    assert by_id["default"]["title"] == "Ricerca AI QA"


def test_rename_existing_session_still_works(client: TestClient) -> None:
    created = client.post("/api/chat/sessions", json={"title": "vecchio"}).json()["session"]
    sid = created["id"]
    resp = client.patch(f"/api/chat/sessions/{sid}", json={"title": "nuovo"})
    assert resp.status_code == 200
    sessions = client.get("/api/chat/sessions").json()["sessions"]
    assert {s["id"]: s["title"] for s in sessions}[sid] == "nuovo"
