"""Phase 2 scan behaviour: concurrent scoring, cancellation, robust is_new,
and the single-scan coordinator."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from app.config import load_settings
from app.db import Database
from app.models import ScanRequest
from app.services import scanner_service as ss
from app.services.scan_control import ScanControl

_COLS = [
    "title",
    "company",
    "description",
    "location",
    "site",
    "job_url",
    "min_amount",
    "max_amount",
]


def _fake_df(n: int) -> pd.DataFrame:
    rows = [
        {
            "title": f"React Developer {i}",
            "company": f"Co{i}",
            "description": "React and TypeScript role",
            "location": "Torino",
            "site": "linkedin",
            "job_url": f"http://x/{i}",
            "min_amount": None,
            "max_amount": None,
        }
        for i in range(n)
    ]
    return pd.DataFrame(rows, columns=_COLS)


class _FakePM:
    def preview_scoring_model(self, _policy: Any) -> str:
        return "fake/model:free"


def _settings(tmp_path: Path, concurrency: int = 3):
    s = load_settings(tmp_path)
    s.scan_concurrency = concurrency
    s.delay_tra_ricerche = 0.0
    s.scan_batch_size = 1  # exercise the per-job path here; batching has its own suite
    return s


def _payload() -> ScanRequest:
    return ScanRequest(search_terms=["react developer"], sites=["linkedin"], location="Torino")


def test_run_scan_scores_all_jobs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ss, "scrape_jobs", lambda **k: _fake_df(5))
    monkeypatch.setattr(ss, "analyze_offer", lambda **k: {"punteggio": 7})
    db = Database(tmp_path / "s.db")
    try:
        events = list(ss.run_scan(db, _settings(tmp_path), _FakePM(), _payload()))
    finally:
        db.close()
    analyzed = [e for e in events if e.get("status") == "analyzed"]
    complete = next(e for e in events if e.get("status") == "complete")
    assert len(analyzed) == 5
    assert complete["totale_analizzati"] == 5
    assert complete["cancelled"] is False


def test_run_scan_cancel_stops_early(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ss, "scrape_jobs", lambda **k: _fake_df(10))
    monkeypatch.setattr(ss, "analyze_offer", lambda **k: {"punteggio": 7})
    db = Database(tmp_path / "s.db")
    try:
        events = list(
            ss.run_scan(db, _settings(tmp_path), _FakePM(), _payload(), cancel_check=lambda: True)
        )
    finally:
        db.close()
    complete = next(e for e in events if e.get("status") == "complete")
    assert complete["cancelled"] is True
    assert complete["totale_analizzati"] == 0  # cancelled before scoring


def test_failed_scrape_preserves_new_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = Database(tmp_path / "s.db")
    try:
        # A prior run left a "new" badge on this job.
        db.upsert_job({"titolo": "Old new job", "azienda": "Acme", "link": "l1"})
        assert len(db.list_jobs(only_new=True)) == 1

        # This scan scrapes nothing (e.g. upstream selector regression).
        monkeypatch.setattr(ss, "scrape_jobs", lambda **k: _fake_df(0))
        monkeypatch.setattr(ss, "analyze_offer", lambda **k: {"punteggio": 7})
        list(ss.run_scan(db, _settings(tmp_path), _FakePM(), _payload()))

        # Badge survives — a failed scrape must not wipe it.
        assert len(db.list_jobs(only_new=True)) == 1
    finally:
        db.close()


# ── ScanControl ─────────────────────────────────────────────────────────────
def test_scan_control_single_slot_and_cancel() -> None:
    sc = ScanControl()
    assert sc.try_begin() is True
    assert sc.running is True
    assert sc.try_begin() is False  # already running
    assert sc.is_cancelled() is False
    sc.cancel()
    assert sc.is_cancelled() is True
    sc.end()
    assert sc.running is False
    assert sc.is_cancelled() is False  # cleared on end
    assert sc.try_begin() is True  # slot free again
    sc.end()
