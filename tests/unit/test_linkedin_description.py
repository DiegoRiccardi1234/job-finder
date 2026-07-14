"""LinkedIn descriptions are the fulcrum of scoring.

jobspy's LinkedIn search returns only job cards (no description) unless
``linkedin_fetch_description=True``; without it the AI scored LinkedIn jobs
blind (title only). These tests cover: the flag is passed, jobspy's ``NaN``
descriptions are cleaned, a missing description is retried, and a job that stays
description-less gets an honest capped estimate (never a blind "9").
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from app.config import load_settings
from app.db import Database
from app.models import ScanRequest
from app.services import recruiter_scrape as rs
from app.services import scanner_service as ss

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


class _NoopPM:
    """Provider stub that must NOT be asked to score anything."""

    def preview_scoring_model(self, _policy: Any) -> str:
        return "fake/model:free"

    def clear_model_penalties(self, reason: str | None = None) -> None:
        pass

    def complete_json(self, *a: Any, **k: Any) -> Any:
        raise AssertionError("LLM must not be called for a description-less job")


# --- _clean_text ------------------------------------------------------------


@pytest.mark.parametrize(
    "val, expected",
    [
        (None, ""),
        (float("nan"), ""),
        ("nan", ""),
        ("None", ""),
        ("   ", ""),
        ("  Real text  ", "Real text"),
        (42, "42"),
    ],
)
def test_clean_text(val: Any, expected: str) -> None:
    assert ss._clean_text(val) == expected


# --- Change 1: the flag reaches jobspy --------------------------------------


def _run_capturing_scrape(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sites: list[str]
) -> dict:
    captured: dict[str, Any] = {}

    def _fake_scrape(**k: Any) -> pd.DataFrame:
        captured.update(k)
        return pd.DataFrame([], columns=_COLS)  # empty → no scoring

    monkeypatch.setattr(ss, "scrape_jobs", _fake_scrape)
    settings = load_settings(tmp_path)
    settings.delay_tra_ricerche = 0.0
    db = Database(tmp_path / "s.db")
    try:
        payload = ScanRequest(search_terms=["x"], sites=sites, location="Torino")
        list(ss.run_scan(db, settings, _NoopPM(), payload))
    finally:
        db.close()
    return captured


def test_linkedin_fetch_description_passed_when_linkedin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _run_capturing_scrape(monkeypatch, tmp_path, ["linkedin", "indeed"])
    assert captured.get("linkedin_fetch_description") is True


def test_linkedin_fetch_description_absent_for_indeed_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _run_capturing_scrape(monkeypatch, tmp_path, ["indeed"])
    assert "linkedin_fetch_description" not in captured


# --- Change 4: description-less job → honest capped estimate -----------------


def test_analyze_offer_empty_description_is_honest_and_capped() -> None:
    res = ss.analyze_offer(_NoopPM(), "CV Diego", "Senior AI Engineer", "BigCo", "")
    assert res["punteggio"] <= 6
    assert res["consiglio"] != "Candidati subito"
    assert "non disponibile" in res["riassunto"].lower()


def test_batch_overrides_description_less_offer() -> None:
    class _PM:
        def preview_scoring_model(self, _p: Any) -> str:
            return "m"

        def clear_model_penalties(self, reason: str | None = None) -> None:
            pass

        def complete_json(self, *a: Any, **k: Any) -> Any:
            # batch would blindly score everything 9 — the empty one must be overridden
            return {"valutazioni": [{"punteggio": 9}, {"punteggio": 9}]}

    offers = [
        {"titolo": "AI Engineer", "azienda": "Co", "descrizione": "Full JD with React"},
        {"titolo": "AI Engineer", "azienda": "Co", "descrizione": ""},
    ]
    out = ss.analyze_offers_batch(_PM(), "CV", offers)
    assert out[0]["punteggio"] == 9  # scored normally
    assert out[1]["punteggio"] <= 6  # description-less → capped, not a blind 9
    assert out[1]["consiglio"] != "Candidati subito"


def test_run_scan_nan_description_job_is_not_blindly_scored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A LinkedIn row with a NaN description whose url isn't a real linkedin.com
    # page (so the re-fetch no-ops) must flow through without an LLM call and
    # end up capped, not a fabricated high score.
    df = pd.DataFrame(
        [
            {
                "title": "Senior ML Engineer",
                "company": "Co",
                "description": float("nan"),
                "location": "Torino",
                "site": "linkedin",
                "job_url": "http://x/1",  # not linkedin.com → retry no-ops
                "min_amount": None,
                "max_amount": None,
            }
        ],
        columns=_COLS,
    )
    monkeypatch.setattr(ss, "scrape_jobs", lambda **k: df)
    settings = load_settings(tmp_path)
    settings.delay_tra_ricerche = 0.0
    db = Database(tmp_path / "s.db")
    try:
        events = list(
            ss.run_scan(
                db, settings, _NoopPM(), ScanRequest(search_terms=["x"], sites=["linkedin"])
            )
        )
    finally:
        db.close()
    analyzed = [e for e in events if e.get("status") == "analyzed"]
    assert len(analyzed) == 1
    assert analyzed[0]["job"]["score"] <= 6


# --- Change 3: fetch_linkedin_description -----------------------------------


class _Resp:
    def __init__(
        self, text: str, status: int = 200, url: str = "https://www.linkedin.com/jobs/view/1"
    ):
        self.text = text
        self.status_code = status
        self.url = url


class _Client:
    def __init__(self, resp: _Resp) -> None:
        self._resp = resp

    def __enter__(self) -> _Client:
        return self

    def __exit__(self, *a: Any) -> bool:
        return False

    def get(self, url: str) -> _Resp:
        return self._resp


class _FakeHttpx:
    def __init__(self, resp: _Resp) -> None:
        self._resp = resp

    def Client(self, **_k: Any) -> _Client:
        return _Client(self._resp)


def test_fetch_linkedin_description_parses_markup(monkeypatch: pytest.MonkeyPatch) -> None:
    html = (
        '<html><body><div class="show-more-less-html__markup">'
        "Requisiti: 5 anni di esperienza in Python.</div></body></html>"
    )
    monkeypatch.setattr(rs, "httpx", _FakeHttpx(_Resp(html)))
    out = rs.fetch_linkedin_description("https://www.linkedin.com/jobs/view/1")
    assert "5 anni di esperienza" in out


def test_fetch_linkedin_description_empty_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rs, "httpx", _FakeHttpx(_Resp("blocked", status=429)))
    assert rs.fetch_linkedin_description("https://www.linkedin.com/jobs/view/1") == ""


def test_fetch_linkedin_description_guards() -> None:
    assert rs.fetch_linkedin_description("") == ""
    assert rs.fetch_linkedin_description("https://example.com/job") == ""  # not linkedin
