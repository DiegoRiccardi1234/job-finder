"""Malformed replies and timeouts must de-rank the model that produced them.

Measured on the 2026-07-21 scan: one model was attempted 28 times, raising a bare
``TypeError`` ("'NoneType' object is not subscriptable", from ``choices=None``)
on 11 of them and timing out on 8 more. ``_classify_failure`` returned None for
both, so no penalty was recorded and auto-selection kept re-picking it — 183s
burned per timed-out call.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.providers.base import EmptyCompletionError, first_choice
from app.providers.factory import _classify_failure, _with_retry
from app.providers.openai_compat import OpenAICompatibleProvider


class _Message:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str | None, finish_reason: str = "stop") -> None:
        self.message = _Message(content)
        self.finish_reason = finish_reason


class _Response:
    def __init__(self, choices: Any) -> None:
        self.choices = choices
        self.usage = None


class _FakeCompletions:
    def __init__(self, response: Any) -> None:
        self._response = response
        self.calls = 0

    def create(self, **_kwargs: Any) -> Any:
        self.calls += 1
        return self._response


class _FakeClient:
    def __init__(self, response: Any) -> None:
        self.chat = type("_Chat", (), {"completions": _FakeCompletions(response)})()


def _provider(response: Any) -> OpenAICompatibleProvider:
    provider = OpenAICompatibleProvider(api_key=None)
    provider.name = "stub"
    provider.client = _FakeClient(response)
    provider._selected_model = "stub/model"
    return provider


# ── the guard itself ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("choices", [None, []])
def test_first_choice_raises_instead_of_typeerror(choices: Any) -> None:
    with pytest.raises(EmptyCompletionError):
        first_choice(_Response(choices), "stub/model")


def test_complete_json_without_choices_raises_empty_completion() -> None:
    provider = _provider(_Response(None))
    with pytest.raises(EmptyCompletionError):
        provider.complete_json(prompt="x")
    # And it must NOT have burned a second network call on complete_text.
    assert provider.client.chat.completions.calls == 1


def test_complete_json_still_parses_a_normal_reply() -> None:
    provider = _provider(_Response([_Choice('{"punteggio": 7}')]))
    assert provider.complete_json(prompt="x") == {"punteggio": 7}


# ── classification ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (EmptyCompletionError("m"), "malformed"),
        (TypeError("'NoneType' object is not subscriptable"), "malformed"),
        (AttributeError("no attribute 'message'"), "malformed"),
        (KeyError("choices"), "malformed"),
        (TimeoutError("provider call timed out"), "timeout"),
        (ValueError("Nessun JSON trovato"), "json_fail"),
    ],
)
def test_classify_failure_maps_structural_errors(exc: Exception, expected: str) -> None:
    assert _classify_failure(exc) == expected


def test_classify_failure_still_ignores_transient_network_errors() -> None:
    assert _classify_failure(ConnectionResetError("connection reset")) is None


# ── retry behaviour ──────────────────────────────────────────────────────────


def test_timeout_is_not_retried_on_the_same_model(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.providers import factory as _mod

    monkeypatch.setattr(_mod._time, "sleep", lambda _: None)
    monkeypatch.setenv("LLM_MAX_RETRIES", "3")
    calls = {"n": 0}

    def _boom() -> str:
        calls["n"] += 1
        raise TimeoutError("provider call timed out after 60s")

    with pytest.raises(TimeoutError):
        _with_retry(_boom, "stub")
    assert calls["n"] == 1  # fail fast: rotate, don't burn 3 x timeout


def test_connection_reset_still_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.providers import factory as _mod

    monkeypatch.setattr(_mod._time, "sleep", lambda _: None)
    monkeypatch.setenv("LLM_MAX_RETRIES", "3")
    calls = {"n": 0}

    def _flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("connection reset by peer")
        return "ok"

    assert _with_retry(_flaky, "stub") == "ok"
    assert calls["n"] == 3
