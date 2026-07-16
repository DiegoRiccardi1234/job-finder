"""Truncation-aware scan scoring.

Reasoning-heavy free models burn the token budget on hidden thinking and cut off
the JSON (``finish_reason == "length"``). The provider layer now raises
``TruncatedCompletionError`` on that signal, the factory classifies it as a
``"truncated"`` penalty (sticky for the whole scan), and ``run_scan`` clears
those penalties between scans.
"""

from __future__ import annotations

import time as _time
from typing import Any

import pytest

from app.providers.base import TruncatedCompletionError, is_truncated
from app.providers.cerebras_provider import CerebrasProvider
from app.providers.google_provider import GoogleProvider
from app.providers.openai_provider import OpenAIProvider
from app.providers.openrouter_provider import OpenRouterProvider

# Every provider whose complete_json speaks the OpenAI response shape
# (choices[0].finish_reason). Anthropic (dict shape, stop_reason) has its own
# section below.
_OPENAI_SHAPED = [OpenRouterProvider, CerebrasProvider, GoogleProvider, OpenAIProvider]

# --- provider layer: complete_json reacts to finish_reason ------------------


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str, finish_reason: str) -> None:
        self.message = _FakeMessage(content)
        self.finish_reason = finish_reason


class _FakeResponse:
    def __init__(self, content: str, finish_reason: str) -> None:
        self.choices = [_FakeChoice(content, finish_reason)]
        self.usage = None


class _FakeCompletions:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    def create(self, **_: Any) -> _FakeResponse:
        return self._response


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self.chat = type("_Chat", (), {"completions": _FakeCompletions(response)})()


def _provider_returning(cls: Any, content: str, finish_reason: str) -> Any:
    p = cls(api_key="test-key")
    p.client = _FakeClient(_FakeResponse(content, finish_reason))
    p._selected_model = "some-model:free"
    return p


@pytest.mark.parametrize("cls", _OPENAI_SHAPED)
def test_complete_json_raises_on_truncation(cls: Any) -> None:
    p = _provider_returning(cls, '{"punteggio": 8', "length")  # cut-off JSON
    with pytest.raises(TruncatedCompletionError):
        p.complete_json("score this")


@pytest.mark.parametrize("cls", _OPENAI_SHAPED)
def test_truncation_does_not_degrade_to_complete_text(
    cls: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On finish_reason=length we must NOT retry via complete_text (it truncates
    the same way) — raise so the factory fails over to a leaner model."""
    p = _provider_returning(cls, '{"punteggio": 8', "length")
    called = {"n": 0}

    def _boom(*_a: Any, **_k: Any) -> str:
        called["n"] += 1
        return "{}"

    monkeypatch.setattr(p, "complete_text", _boom)
    with pytest.raises(TruncatedCompletionError):
        p.complete_json("score this")
    assert called["n"] == 0


@pytest.mark.parametrize("cls", _OPENAI_SHAPED)
def test_complete_json_stop_parses_normally(cls: Any) -> None:
    p = _provider_returning(cls, '{"punteggio": 8}', "stop")
    assert p.complete_json("score this") == {"punteggio": 8}


def test_complete_json_stop_with_prose_is_salvaged() -> None:
    """A non-truncated reply that wraps JSON in prose still gets rescued by the
    complete_text + _extract_json fallback (no regression)."""
    p = _provider_returning(OpenRouterProvider, 'Ecco: {"punteggio": 7} ok', "stop")
    assert p.complete_json("score this") == {"punteggio": 7}


def test_is_truncated_recognises_both_shapes() -> None:
    # OpenAI-compatible object shape
    assert is_truncated(_FakeResponse("x", "length")) is True
    assert is_truncated(_FakeResponse("x", "stop")) is False
    # Anthropic-style dict shape
    assert is_truncated({"stop_reason": "max_tokens"}) is True
    assert is_truncated({"stop_reason": "end_turn"}) is False
    # unknown / empty shapes never flag truncation
    assert is_truncated(object()) is False
    assert is_truncated(None) is False


# --- Anthropic (dict shape: stop_reason == "max_tokens") ---------------------


def _anthropic_with_payload(monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]) -> Any:
    import json as _json

    from app.providers import anthropic_provider as ap

    class _Resp:
        def __init__(self, data: bytes) -> None:
            self._data = data

        def read(self) -> bytes:
            return self._data

        def __enter__(self) -> Any:
            return self

        def __exit__(self, *a: Any) -> bool:
            return False

    monkeypatch.setattr(
        ap.request,
        "urlopen",
        lambda req, timeout=30: _Resp(_json.dumps(payload).encode("utf-8")),
    )
    p = ap.AnthropicProvider(api_key="test-key")
    p._selected_model = "claude-x"
    return p


def _anthropic_payload(text: str, stop_reason: str) -> dict[str, Any]:
    return {
        "stop_reason": stop_reason,
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


def test_anthropic_complete_json_raises_on_max_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    p = _anthropic_with_payload(monkeypatch, _anthropic_payload('{"punteggio": 8', "max_tokens"))
    with pytest.raises(TruncatedCompletionError):
        p.complete_json("score this")


def test_anthropic_complete_json_end_turn_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    p = _anthropic_with_payload(monkeypatch, _anthropic_payload('{"punteggio": 8}', "end_turn"))
    assert p.complete_json("score this") == {"punteggio": 8}


def test_anthropic_chat_and_text_never_raise_on_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Truncation only poisons JSON; chat/complete_text return the partial text."""
    p = _anthropic_with_payload(monkeypatch, _anthropic_payload("partial ans", "max_tokens"))
    assert p.chat([{"role": "user", "content": "hi"}]) == "partial ans"
    assert p.complete_text("hi") == "partial ans"


# --- factory layer: classification, penalty, stickiness, reset --------------


def test_classify_failure_maps_truncation() -> None:
    from app.providers.factory import _classify_failure

    assert _classify_failure(TruncatedCompletionError("m")) == "truncated"
    # a plain ValueError is still the softer json_fail
    assert _classify_failure(ValueError("Nessun JSON trovato")) == "json_fail"


def _mgr(tmp_path: Any) -> Any:
    from app.config import load_settings
    from app.providers.factory import ProviderManager

    return ProviderManager(load_settings(tmp_path))


def test_truncated_penalty_is_sticky_beyond_json_fail_ttl(tmp_path: Any) -> None:
    """json_fail self-heals in 180s; a truncation penalty must outlast a whole
    scan (cooldown ~1h) so a cut-off model stays de-ranked for the run."""
    mgr = _mgr(tmp_path)
    # record as if it happened 200s ago (past the 180s json_fail TTL)
    mgr._model_penalty["openrouter::big-550b:free"] = (_time.time() - 200.0, "truncated")
    assert "big-550b:free" in mgr._penalized_model_ids("openrouter")


def test_clear_model_penalties_by_reason(tmp_path: Any) -> None:
    mgr = _mgr(tmp_path)
    mgr.record_model_penalty("openrouter", "trunc:free", "truncated")
    mgr.record_model_penalty("openrouter", "throttled:free", "rate_limit")

    mgr.clear_model_penalties("truncated")

    penalized = mgr._penalized_model_ids("openrouter")
    assert "trunc:free" not in penalized  # cleared
    assert "throttled:free" in penalized  # untouched


def test_clear_model_penalties_all(tmp_path: Any) -> None:
    mgr = _mgr(tmp_path)
    mgr.record_model_penalty("openrouter", "a:free", "truncated")
    mgr.record_model_penalty("openrouter", "b:free", "rate_limit")

    mgr.clear_model_penalties()

    assert mgr._model_penalty == {}
