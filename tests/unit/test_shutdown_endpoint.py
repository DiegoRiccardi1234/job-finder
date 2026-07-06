"""Tests for POST /api/system/shutdown.

The windowless build has no terminal to close, so the app exposes an explicit
shutdown: it returns 202, then hard-exits (after a short Timer so the response
flushes), closing the DB / stopping autoscan via container.shutdown() first.
"""

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
    for key in ("CEREBRAS_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY"):
        os.environ.pop(key, None)

    from app.main import create_app

    app = create_app(workspace_dir=tmp_path)
    with TestClient(app) as tc:
        yield tc


def test_shutdown_returns_202_and_schedules_exit(client: TestClient, monkeypatch) -> None:
    import app.routers.system as sysmod

    captured: dict[str, object] = {}

    class FakeTimer:
        def __init__(self, delay: float, fn) -> None:
            captured["delay"] = delay
            captured["fn"] = fn

        def start(self) -> None:
            captured["started"] = True

    monkeypatch.setattr(sysmod.threading, "Timer", FakeTimer)

    res = client.post("/api/system/shutdown")
    assert res.status_code == 202
    assert res.json()["status"] == "shutting_down"
    assert captured.get("started") is True
    assert callable(captured.get("fn"))


def test_shutdown_callback_exits_zero(client: TestClient, monkeypatch) -> None:
    import app.routers.system as sysmod

    captured: dict[str, object] = {}

    class FakeTimer:
        def __init__(self, delay: float, fn) -> None:
            captured["fn"] = fn

        def start(self) -> None:
            pass

    monkeypatch.setattr(sysmod.threading, "Timer", FakeTimer)
    exits: list[int] = []
    monkeypatch.setattr(sysmod.os, "_exit", lambda code: exits.append(code))

    client.post("/api/system/shutdown")
    captured["fn"]()  # run the scheduled shutdown body
    assert exits == [0]
