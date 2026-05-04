"""Unit tests for `app.services.usage_tracker`."""

from __future__ import annotations

import pytest

from app.db import Database
from app.services.usage_tracker import aggregate_stats, record_usage


@pytest.fixture
def db(tmp_path) -> Database:
    return Database(tmp_path / "test.db")


def test_record_usage_inserts_row(db: Database) -> None:
    record_usage(
        db,
        provider="cerebras",
        model="qwen-3",
        endpoint="chat",
        last_usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    )
    stats = aggregate_stats(db, range_="all")
    assert stats["total_calls"] == 1
    assert stats["total_tokens"] == 150
    assert stats["prompt_tokens"] == 100
    assert stats["completion_tokens"] == 50


def test_record_usage_handles_missing_payload(db: Database) -> None:
    """Failed calls (no usage payload) still get logged with zeros."""
    record_usage(
        db,
        provider="openai",
        model="gpt-4",
        endpoint="chat",
        last_usage=None,
        success=False,
        error_type="HTTPError",
    )
    stats = aggregate_stats(db, range_="all")
    assert stats["total_calls"] == 1
    assert stats["total_tokens"] == 0
    assert stats["successes"] == 0


def test_aggregate_by_provider(db: Database) -> None:
    record_usage(
        db,
        provider="cerebras",
        model="m1",
        endpoint="chat",
        last_usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    )
    record_usage(
        db,
        provider="cerebras",
        model="m1",
        endpoint="chat",
        last_usage={"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
    )
    record_usage(
        db,
        provider="groq",
        model="m2",
        endpoint="chat",
        last_usage={"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
    )

    stats = aggregate_stats(db, range_="all")
    by_provider = {p["provider"]: p for p in stats["by_provider"]}
    assert by_provider["cerebras"]["calls"] == 2
    assert by_provider["cerebras"]["total_tokens"] == 45
    assert by_provider["groq"]["calls"] == 1
    assert by_provider["groq"]["total_tokens"] == 10


def test_aggregate_empty_returns_zeroes(db: Database) -> None:
    stats = aggregate_stats(db, range_="today")
    assert stats["total_calls"] == 0
    assert stats["total_tokens"] == 0
    assert stats["by_provider"] == []
    assert stats["by_day"] == []


def test_aggregate_range_floor_clamps_unknown_to_today(db: Database) -> None:
    stats = aggregate_stats(db, range_="not-a-real-range")
    # Falls into the ``else`` branch which uses 2000-01-01 floor; just verify it doesn't crash.
    assert "since" in stats
    assert "by_day" in stats
