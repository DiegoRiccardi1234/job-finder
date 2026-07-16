"""complete_json error paths on OpenAI-compatible providers.

Transport/HTTP errors (429/401/timeout) must PROPAGATE to the factory — it
owns retry/penalty/failover. The old catch-all swallowed them and fired a
second network call via complete_text (a second 429 on a rate-limited host).
JSON wrapped in prose/markdown fences is salvaged locally with zero extra
calls; only prose with no JSON at all is worth one complete_text retry.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.providers.openrouter_provider import OpenRouterProvider


class _Http429(Exception):
    def __init__(self) -> None:
        super().__init__("429 rate limited upstream")
        self.status_code = 429


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str, finish_reason: str) -> None:
        self.message = _FakeMessage(content)
        self.finish_reason = finish_reason


class _FakeResponse:
    def __init__(self, content: str, finish_reason: str = "stop") -> None:
        self.choices = [_FakeChoice(content, finish_reason)]
        self.usage = None


class _FakeCompletions:
    def __init__(self, outcome: Any) -> None:
        self._outcome = outcome

    def create(self, **_: Any) -> _FakeResponse:
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


class _FakeClient:
    def __init__(self, outcome: Any) -> None:
        self.chat = type("_Chat", (), {"completions": _FakeCompletions(outcome)})()


def _provider(outcome: Any) -> tuple[OpenRouterProvider, dict[str, int]]:
    p = OpenRouterProvider(api_key="test-key")
    p.client = _FakeClient(outcome)  # type: ignore[assignment]
    p._selected_model = "some-model:free"
    calls = {"complete_text": 0}
    original = p.complete_text

    def _spy(prompt: str, model: str | None = None, max_tokens: int = 700) -> str:
        calls["complete_text"] += 1
        return '{"punteggio": 5}'

    p.complete_text = _spy  # type: ignore[method-assign]
    del original
    return p, calls


def test_http_error_propagates_without_second_call() -> None:
    p, calls = _provider(_Http429())
    with pytest.raises(_Http429):
        p.complete_json("score this")
    assert calls["complete_text"] == 0, "a transport error must not trigger a second network call"


def test_fenced_json_salvaged_locally_without_second_call() -> None:
    p, calls = _provider(_FakeResponse('```json\n{"punteggio": 7}\n```'))
    assert p.complete_json("score this") == {"punteggio": 7}
    assert calls["complete_text"] == 0


def test_prose_with_no_json_uses_single_fallback_call() -> None:
    p, calls = _provider(_FakeResponse("Non posso rispondere in JSON, ecco una spiegazione."))
    assert p.complete_json("score this") == {"punteggio": 5}
    assert calls["complete_text"] == 1
