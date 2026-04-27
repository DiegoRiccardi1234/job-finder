"""Unit tests for the scraper canary helper."""

from __future__ import annotations

from app.services.scanner_service import _COMMON_CANARY_TERMS, _is_common_term


def test_common_terms_recognised() -> None:
    for term in ("python", "Python", "  SQL ", "data analyst"):
        assert _is_common_term(term) is True


def test_uncommon_terms_not_flagged() -> None:
    assert _is_common_term("rare niche role") is False
    assert _is_common_term("") is False


def test_canary_set_covers_main_stacks() -> None:
    expected = {"python", "java", "sql", "react", "devops"}
    assert expected.issubset(_COMMON_CANARY_TERMS)
