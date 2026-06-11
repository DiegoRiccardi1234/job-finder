"""Tests for the in-process auto-scan scheduler."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import Database
from app.services.autoscan import AutoScanScheduler


class FakeContainer:
    def __init__(self, db: Database, has_provider: bool = True) -> None:
        self.db = db
        self.settings = None
        self.providers = None
        self._has = has_provider

    def has_provider_configured(self) -> bool:
        return self._has


def _fake_run_scan(db, settings, provider_manager, payload):
    job_id, _, _ = db.upsert_job({"titolo": "Hot role", "azienda": "X", "link": "l1"})
    db.update_job_analysis(job_id, {"punteggio": 9})
    yield {"status": "complete"}


def test_defaults(tmp_path: Path) -> None:
    db = Database(tmp_path / "s.db")
    try:
        sched = AutoScanScheduler(FakeContainer(db))
        st = sched.status()
        assert st["enabled"] is False
        assert st["interval_hours"] == 12
        assert st["threshold"] == 7
        assert st["pending"] is None
    finally:
        db.close()


def test_config_reflected_in_status(tmp_path: Path) -> None:
    db = Database(tmp_path / "s.db")
    try:
        db.set_preference("autoscan_enabled", "1")
        db.set_preference("autoscan_interval_hours", "6")
        db.set_preference("autoscan_score_threshold", "8")
        sched = AutoScanScheduler(FakeContainer(db))
        st = sched.status()
        assert st["enabled"] is True
        assert st["interval_hours"] == 6
        assert st["threshold"] == 8
    finally:
        db.close()


def test_run_once_skips_without_provider(tmp_path: Path) -> None:
    db = Database(tmp_path / "s.db")
    try:
        sched = AutoScanScheduler(
            FakeContainer(db, has_provider=False), run_scan_fn=_fake_run_scan
        )
        assert sched.run_once() == {"status": "skipped", "reason": "no_provider"}
    finally:
        db.close()


def test_run_once_records_highlights(tmp_path: Path) -> None:
    db = Database(tmp_path / "s.db")
    try:
        db.set_preference("autoscan_score_threshold", "7")
        sched = AutoScanScheduler(
            FakeContainer(db), run_scan_fn=_fake_run_scan, clock=lambda: 1000.0
        )
        result = sched.run_once()
        assert result["status"] == "complete"
        assert result["count"] == 1
        pending = sched.pending()
        assert pending is not None and pending["count"] == 1
        assert sched.status()["last_run_ts"] == 1000.0
        sched.clear_pending()
        assert sched.pending() is None
    finally:
        db.close()


def test_maybe_run_respects_enabled_and_interval(tmp_path: Path) -> None:
    db = Database(tmp_path / "s.db")
    try:
        now = [100_000.0]
        sched = AutoScanScheduler(
            FakeContainer(db), run_scan_fn=_fake_run_scan, clock=lambda: now[0]
        )
        # Disabled -> no run.
        sched._maybe_run()
        assert sched.pending() is None

        # Enabled, never run before -> runs.
        db.set_preference("autoscan_enabled", "1")
        sched._maybe_run()
        assert sched.pending() is not None

        # Just ran -> within interval, should not run again (clear pending to detect).
        sched.clear_pending()
        sched._maybe_run()
        assert sched.pending() is None

        # Advance past the interval -> runs again.
        now[0] += sched.interval_hours() * 3600 + 1
        sched._maybe_run()
        assert sched.pending() is not None
    finally:
        db.close()


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


def test_scheduler_status_endpoint(client: TestClient) -> None:
    st = client.get("/api/scheduler/status").json()
    assert st["enabled"] is False
    assert "interval_hours" in st


def test_scheduler_config_endpoint(client: TestClient) -> None:
    resp = client.post(
        "/api/scheduler/config", json={"enabled": True, "interval_hours": 8, "threshold": 9}
    )
    assert resp.status_code == 200
    st = resp.json()["status"]
    assert st["enabled"] is True
    assert st["interval_hours"] == 8
    assert st["threshold"] == 9


def test_scheduler_config_validates_bounds(client: TestClient) -> None:
    # interval_hours out of [1,168] and threshold out of [0,10] -> 422.
    assert client.post("/api/scheduler/config", json={"interval_hours": 999}).status_code == 422
    assert client.post("/api/scheduler/config", json={"threshold": 99}).status_code == 422


def test_scheduler_run_now_and_dismiss(client: TestClient) -> None:
    # No provider configured in test env -> run_once skips, no network hit.
    assert client.post("/api/scheduler/run-now").status_code == 202
    assert client.post("/api/scheduler/dismiss").json()["ok"] is True
