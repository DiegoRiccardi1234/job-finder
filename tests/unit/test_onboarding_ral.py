"""Salary expectations in the onboarding block.

The two figures travel to the scoring prompt through ``onboarding_context``, and
the deterministic salary check reads the minimum back out of that same rendered
block — so the label is a contract, not decoration.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.db import Database
from app.services import scanner_service as ss
from app.services.onboarding import (
    RAL_MIN_KEY,
    RAL_TARGET_KEY,
    onboarding_context,
    onboarding_ral,
    parse_ral_amount,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("35000", 35000),
        ("35.000", 35000),
        ("35.000 €", 35000),
        ("35k", 35000),
        ("35", 35000),  # a bare "35" means thousands, not 35 euros a year
        ("", None),
        ("boh", None),
    ],
)
def test_parse_ral_amount(raw: str, expected: int | None) -> None:
    assert parse_ral_amount(raw) == expected


def test_context_carries_normalised_amounts(tmp_path: Path) -> None:
    db = Database(tmp_path / "d.db")
    try:
        db.set_preference("onboarding_sector", "AI")
        db.set_preference(RAL_MIN_KEY, "35k")
        db.set_preference(RAL_TARGET_KEY, "42.000 €")
        context = onboarding_context(db)
        assert "Settore target: AI" in context
        assert "35000 EUR" in context
        assert "42000 EUR" in context
        assert onboarding_ral(db) == (35000, 42000)
    finally:
        db.close()


def test_scoring_reads_the_minimum_back_from_the_context(tmp_path: Path) -> None:
    """The round trip that matters: what onboarding renders, scoring parses."""
    db = Database(tmp_path / "d.db")
    try:
        db.set_preference(RAL_MIN_KEY, "35000")
        assert ss._ral_min_from_context(onboarding_context(db)) == 35000
    finally:
        db.close()


def test_missing_preferences_are_silent(tmp_path: Path) -> None:
    db = Database(tmp_path / "d.db")
    try:
        assert onboarding_ral(db) == (None, None)
        assert ss._ral_min_from_context(onboarding_context(db)) is None
    finally:
        db.close()
