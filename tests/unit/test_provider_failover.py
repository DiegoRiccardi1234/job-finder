"""Per-request provider failover.

When the active provider exhausts its retries (rate-limited / down) the manager
must try the other available providers in ``llm_provider_order`` before giving
up — otherwise chat drops straight to the canned fallback even though a working
key is configured. A 401 during a live call must also flag the provider invalid
so the UI stops showing it as healthy.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.config import load_settings
from app.providers.base import LLMProvider
from app.providers.factory import ProviderManager


class _StubProvider(LLMProvider):
    def __init__(
        self,
        name: str,
        *,
        answer: str | None = None,
        exc: Exception | None = None,
    ) -> None:
        self.name = name
        self._answer = answer
        self._exc = exc
        self.key_invalid = False
        self.calls = 0

    def is_available(self) -> bool:
        return not self.key_invalid

    def list_models(self) -> list[str]:
        return ["model-x"]

    def select_model(self, preferred_model: str | None = None) -> str:
        return "model-x"

    def complete_text(self, prompt: str, model: str | None = None, max_tokens: int = 700) -> str:
        return self._answer or ""

    def chat(
        self, messages: list[dict[str, str]], model: str | None = None, max_tokens: int = 700
    ) -> str:
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        return self._answer or ""

    def complete_json(
        self, prompt: str, model: str | None = None, max_tokens: int = 700
    ) -> dict[str, Any]:
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        return {"answer": self._answer}


class _Http401(Exception):
    def __init__(self) -> None:
        super().__init__("401 wrong api key")
        self.status_code = 401


@pytest.fixture(autouse=True)
def _fast_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.providers import factory as _mod

    monkeypatch.setattr(_mod._time, "sleep", lambda _: None)
    monkeypatch.setenv("LLM_MAX_RETRIES", "1")


def _mgr(tmp_path: Any, providers: dict[str, _StubProvider], order: list[str], active: str):
    settings = load_settings(tmp_path)
    settings.llm_provider_order = order
    mgr = ProviderManager(settings)
    mgr.providers = providers  # type: ignore[assignment]
    ap = providers[active]
    mgr.active_provider = ap
    mgr.active_provider_name = ap.name
    mgr.active_model = "model-x"
    return mgr


def test_chat_fails_over_when_active_provider_errors(tmp_path: Any) -> None:
    bad = _StubProvider("cerebras", exc=Exception("429 rate limit"))
    good = _StubProvider("openrouter", answer="from openrouter")
    mgr = _mgr(tmp_path, {"cerebras": bad, "openrouter": good}, ["cerebras", "openrouter"], "cerebras")

    assert mgr.chat(messages=[{"role": "user", "content": "hi"}]) == "from openrouter"
    assert good.calls == 1


def test_complete_json_fails_over(tmp_path: Any) -> None:
    bad = _StubProvider("cerebras", exc=Exception("503 service unavailable"))
    good = _StubProvider("openrouter", answer="ok")
    mgr = _mgr(tmp_path, {"cerebras": bad, "openrouter": good}, ["cerebras", "openrouter"], "cerebras")

    assert mgr.complete_json(prompt="x") == {"answer": "ok"}


def test_chat_flags_key_invalid_on_401_then_fails_over(tmp_path: Any) -> None:
    bad = _StubProvider("cerebras", exc=_Http401())
    good = _StubProvider("openrouter", answer="ok")
    mgr = _mgr(tmp_path, {"cerebras": bad, "openrouter": good}, ["cerebras", "openrouter"], "cerebras")

    assert mgr.chat(messages=[{"role": "user", "content": "hi"}]) == "ok"
    assert bad.key_invalid is True


def test_failover_skips_already_invalid_provider(tmp_path: Any) -> None:
    bad = _StubProvider("cerebras", exc=Exception("boom"))
    dead = _StubProvider("groq", answer="should-not-be-called")
    dead.key_invalid = True
    good = _StubProvider("openrouter", answer="ok")
    mgr = _mgr(
        tmp_path,
        {"cerebras": bad, "groq": dead, "openrouter": good},
        ["cerebras", "groq", "openrouter"],
        "cerebras",
    )

    assert mgr.chat(messages=[{"role": "user", "content": "hi"}]) == "ok"
    assert dead.calls == 0
    assert good.calls == 1


def test_single_failing_provider_raises(tmp_path: Any) -> None:
    """No other provider to fail over to → raise so the caller can fall back."""
    bad = _StubProvider("openrouter", exc=Exception("429 rate limit"))
    mgr = _mgr(tmp_path, {"openrouter": bad}, ["openrouter"], "openrouter")

    with pytest.raises(Exception):
        mgr.chat(messages=[{"role": "user", "content": "hi"}])


def test_key_invalid_cooldown_reprobes_after_window(tmp_path: Any, monkeypatch) -> None:
    """A key_invalid provider is excluded within the cooldown, then re-probed
    (flag cleared) once the cooldown window elapses — a transient 401 no longer
    disables it for the whole session."""
    from app.providers import factory as _mod

    bad = _StubProvider("cerebras", exc=Exception("boom"))
    good = _StubProvider("openrouter", answer="ok")
    bad.key_invalid = True
    mgr = _mgr(tmp_path, {"cerebras": bad, "openrouter": good}, ["cerebras", "openrouter"], "openrouter")

    names1 = [p.name for p, _m in mgr._failover_candidates(None, None)]
    assert "cerebras" not in names1  # within cooldown → excluded

    monkeypatch.setattr(_mod, "_KEY_INVALID_COOLDOWN_SECONDS", 0.0)
    names2 = [p.name for p, _m in mgr._failover_candidates(None, None)]
    assert "cerebras" in names2  # cooldown elapsed → re-probed
    assert bad.key_invalid is False


def test_429_records_model_for_derank(tmp_path: Any) -> None:
    """A persistent 429 records the model so auto-selection de-ranks it."""
    bad = _StubProvider("openrouter", exc=Exception("429 rate limit"))
    mgr = _mgr(tmp_path, {"openrouter": bad}, ["openrouter"], "openrouter")
    with pytest.raises(Exception):
        mgr.chat(messages=[{"role": "user", "content": "hi"}])
    assert "model-x" in mgr._model_429_at
    assert "model-x" in mgr._recent_429_models()


class _MultiModelProvider(LLMProvider):
    """Provider with several models; a chosen ``bad_model`` always 429s."""

    def __init__(self, name: str, models: list[str], bad_model: str) -> None:
        self.name = name
        self._models = models
        self._bad = bad_model
        self.key_invalid = False
        self.calls: list[str | None] = []

    def is_available(self) -> bool:
        return not self.key_invalid

    def list_models(self) -> list[str]:
        return list(self._models)

    def select_model(self, preferred_model: str | None = None) -> str:
        return self._models[0]

    def complete_text(self, prompt: str, model: str | None = None, max_tokens: int = 700) -> str:
        return ""

    def chat(
        self, messages: list[dict[str, str]], model: str | None = None, max_tokens: int = 700
    ) -> str:
        self.calls.append(model)
        if model == self._bad:
            raise Exception("429 rate limit")
        return f"ok:{model}"

    def complete_json(
        self, prompt: str, model: str | None = None, max_tokens: int = 700
    ) -> dict[str, Any]:
        self.calls.append(model)
        if model == self._bad:
            raise Exception("429 rate limit")
        return {"answer": f"ok:{model}"}


def test_intra_provider_model_failover(tmp_path: Any) -> None:
    """The top model 429s → rotate to another model of the SAME provider, even
    when no other provider is configured (the real single-OpenRouter case)."""
    p = _MultiModelProvider(
        "openrouter",
        ["gpt-oss-120b:free", "llama-3.3-70b:free"],
        bad_model="gpt-oss-120b:free",
    )
    mgr = _mgr(tmp_path, {"openrouter": p}, ["openrouter"], "openrouter")

    out = mgr.chat(messages=[{"role": "user", "content": "hi"}])
    assert out == "ok:llama-3.3-70b:free"  # rotated off the 429ing top model
    assert p.calls[0] == "gpt-oss-120b:free" and len(p.calls) >= 2


def test_explicit_provider_is_not_failed_over(tmp_path: Any) -> None:
    """An explicit provider request is honored — no silent switch to another."""
    bad = _StubProvider("cerebras", exc=Exception("boom"))
    good = _StubProvider("openrouter", answer="ok")
    mgr = _mgr(tmp_path, {"cerebras": bad, "openrouter": good}, ["cerebras", "openrouter"], "openrouter")

    with pytest.raises(Exception):
        mgr.chat(messages=[{"role": "user", "content": "hi"}], provider_name="cerebras")
    assert good.calls == 0
