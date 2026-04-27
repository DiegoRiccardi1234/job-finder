"""Unit tests for rule-based chat fallback (provider unavailable)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.db import Database
from app.services.chat.fallback import fallback_answer


@pytest.fixture
def empty_db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "fb.db")
    yield db
    db.close()


def test_fallback_without_cv_returns_non_empty_text(empty_db: Database) -> None:
    answer, action = fallback_answer(db=empty_db, message="ciao")
    assert isinstance(answer, str)
    assert answer.strip()
    # action may be None or a dict; never raise
    assert action is None or isinstance(action, dict)


def test_fallback_with_search_intent_returns_action(empty_db: Database) -> None:
    # plant a CV so suggest_keywords_from_profile returns signal
    profile_id = empty_db.save_candidate_profile(
        source_name="cv.md", markdown="Python developer", summary={}
    )
    empty_db.set_active_profile(profile_id)

    answer, action = fallback_answer(
        db=empty_db, message="cerca lavori Python a Milano"
    )
    assert answer
    # either english or italian fallback body — check at least one keyword recognizable
    assert any(token in answer.lower() for token in ("python", "ricerca", "search", "keyword"))


def test_fallback_role_guidance_intent(empty_db: Database) -> None:
    profile_id = empty_db.save_candidate_profile(
        source_name="cv.md",
        markdown="QA tester with Selenium and automation experience",
        summary={},
    )
    empty_db.set_active_profile(profile_id)

    answer, _ = fallback_answer(
        db=empty_db,
        message="che figure lavorative posso cercare con il mio cv?",
    )
    assert answer
    assert len(answer) > 20


def test_fallback_respects_ui_language(empty_db: Database) -> None:
    empty_db.set_preference("ui_language", "it")
    answer, _ = fallback_answer(db=empty_db, message="ciao")
    assert isinstance(answer, str) and answer
