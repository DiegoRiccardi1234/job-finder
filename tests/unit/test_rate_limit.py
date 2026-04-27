"""Unit tests for the in-process rate limiter."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app import rate_limit


class _FakeClient:
    def __init__(self, host: str) -> None:
        self.host = host


class _FakeRequest:
    def __init__(self, host: str = "1.2.3.4") -> None:
        self.client = _FakeClient(host)


@pytest.fixture(autouse=True)
def _reset_buckets(monkeypatch):
    rate_limit.reset()
    monkeypatch.setenv("ENABLE_RATE_LIMIT", "1")
    # force a fresh read of the flag
    monkeypatch.setattr(rate_limit, "_ENABLED", True)
    yield
    rate_limit.reset()


def test_under_limit_allows_requests() -> None:
    req = _FakeRequest()
    for _ in range(3):
        rate_limit.check(req, bucket="chat", limit=5, window_seconds=60)


def test_over_limit_raises_429() -> None:
    req = _FakeRequest()
    for _ in range(5):
        rate_limit.check(req, bucket="chat", limit=5, window_seconds=60)
    with pytest.raises(HTTPException) as exc_info:
        rate_limit.check(req, bucket="chat", limit=5, window_seconds=60)
    assert exc_info.value.status_code == 429
    assert "Retry-After" in exc_info.value.headers


def test_different_buckets_are_independent() -> None:
    req = _FakeRequest()
    for _ in range(5):
        rate_limit.check(req, bucket="chat", limit=5, window_seconds=60)
    # different bucket — should still pass
    rate_limit.check(req, bucket="scan", limit=5, window_seconds=60)


def test_different_ips_are_independent() -> None:
    for _ in range(5):
        rate_limit.check(_FakeRequest("1.1.1.1"), bucket="chat", limit=5)
    rate_limit.check(_FakeRequest("2.2.2.2"), bucket="chat", limit=5)


def test_window_slides(monkeypatch) -> None:
    req = _FakeRequest()
    fake_time = [0.0]

    def fake_monotonic():
        return fake_time[0]

    monkeypatch.setattr(rate_limit.time, "monotonic", fake_monotonic)

    for _ in range(5):
        rate_limit.check(req, bucket="chat", limit=5, window_seconds=60)
    with pytest.raises(HTTPException):
        rate_limit.check(req, bucket="chat", limit=5, window_seconds=60)

    # Advance time beyond window → old entries should be evicted.
    fake_time[0] = 61.0
    rate_limit.check(req, bucket="chat", limit=5, window_seconds=60)


def test_disabled_flag_skips_check(monkeypatch) -> None:
    monkeypatch.setattr(rate_limit, "_ENABLED", False)
    req = _FakeRequest()
    for _ in range(50):
        rate_limit.check(req, bucket="chat", limit=5, window_seconds=60)
