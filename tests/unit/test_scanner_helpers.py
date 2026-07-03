"""Unit tests for `app.services.scanner_service` pure helpers.

jobspy is not exercised here (it hits the network). Only the local helpers
(blacklist pre-filter, analysis prompt shape) are verified.
"""

from __future__ import annotations

from app.services.scanner_service import (
    BLACKLIST,
    _analysis_prompt,
    _resolve_jobspy_job_type,
    pre_filtro,
)


def test_pre_filtro_flags_blacklisted_title() -> None:
    blocked, reason = pre_filtro("Senior Developer Python", "remote work")
    assert blocked is True
    assert reason in BLACKLIST


def test_pre_filtro_flags_blacklisted_description() -> None:
    blocked, reason = pre_filtro("Dev", "richiediamo P.Iva e 5+ anni di esperienza")
    assert blocked is True
    assert reason  # non-empty


def test_pre_filtro_clean_listing_passes() -> None:
    blocked, reason = pre_filtro("Junior Python Developer", "Neolaureato, remoto")
    assert blocked is False
    assert reason == ""


def test_pre_filtro_is_case_insensitive() -> None:
    blocked, _ = pre_filtro("SENIOR ENGINEER", "")
    assert blocked is True


def test_analysis_prompt_contains_offer_fields() -> None:
    prompt = _analysis_prompt(
        profile_markdown="Python dev CV",
        titolo="Junior Dev",
        azienda="ACME",
        descrizione="Python SQL remote",
    )
    assert "Junior Dev" in prompt
    assert "ACME" in prompt
    assert "Python SQL remote" in prompt
    # must ask for JSON output
    assert "JSON" in prompt or "json" in prompt


def test_blacklist_contains_core_phrases() -> None:
    assert "senior developer" in BLACKLIST
    assert "partita iva" in BLACKLIST
    assert "freelance" in BLACKLIST


def test_single_job_type_is_passed_through() -> None:
    assert _resolve_jobspy_job_type(["fulltime"]) == "fulltime"


def test_multiple_job_types_do_not_silently_drop_to_first() -> None:
    """JobSpy takes one job_type; selecting fulltime+contract must NOT return
    only 'fulltime' — pass None so all types come back (superset), not a subset."""
    assert _resolve_jobspy_job_type(["fulltime", "contract"]) is None


def test_empty_job_types_returns_none() -> None:
    assert _resolve_jobspy_job_type([]) is None


def test_unknown_job_type_returns_none() -> None:
    assert _resolve_jobspy_job_type(["freelance"]) is None


def test_one_valid_among_unknown_is_used() -> None:
    assert _resolve_jobspy_job_type(["fulltime", "freelance"]) == "fulltime"
