"""Smart model-selection engine: empirical penalties + active probe."""

from __future__ import annotations

from typing import Any

import pytest

from app.config import load_settings
from app.providers import factory as _factory
from app.providers.factory import (
    ProviderManager,
    _classify_failure,
    _is_empty_result,
    _is_forbidden,
)
from app.services.model_probe import PROBE_PROMPT, penalty_reason, probe_models


# ── failure classification helpers ──────────────────────────────────────────
class _Http(Exception):
    def __init__(self, status: int, msg: str = "") -> None:
        super().__init__(msg or str(status))
        self.status_code = status


def test_is_forbidden_detects_403_and_key_limit() -> None:
    assert _is_forbidden(_Http(403))
    assert _is_forbidden(Exception("403 Key limit exceeded (total limit)"))
    assert not _is_forbidden(_Http(429))


def test_classify_failure_maps_reasons() -> None:
    assert _classify_failure(_Http(429)) == "rate_limit"
    assert _classify_failure(_Http(403)) == "forbidden"
    assert _classify_failure(ValueError("Nessun JSON trovato")) == "json_fail"
    # A transient 5xx / network blip should NOT de-rank the model.
    assert _classify_failure(Exception("503 service unavailable")) is None


def test_is_empty_result() -> None:
    assert _is_empty_result("")
    assert _is_empty_result("   ")
    assert _is_empty_result({})
    assert _is_empty_result(None)
    assert not _is_empty_result("ok")
    assert not _is_empty_result({"a": 1})


# ── penalty map (composite key + TTL) ───────────────────────────────────────
def test_penalty_records_and_expires(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"t": 1000.0}
    monkeypatch.setattr(_factory._time, "time", lambda: clock["t"])
    mgr = ProviderManager(load_settings(tmp_path))

    mgr.record_model_penalty("openrouter", "gpt-x:free", "rate_limit")
    assert "openrouter::gpt-x:free" in mgr._model_penalty
    assert "gpt-x:free" in mgr._penalized_model_ids("openrouter")
    # provider-scoped: a different provider doesn't see it
    assert "gpt-x:free" not in mgr._penalized_model_ids("groq")

    # advance past the cooldown → pruned
    clock["t"] += _factory._MODEL_429_COOLDOWN_SECONDS + 1
    assert mgr._penalized_model_ids("openrouter") == set()
    assert mgr._model_penalty == {}


# ── active probe ────────────────────────────────────────────────────────────
class _ProbeProvider:
    name = "openrouter"

    def __init__(self, behavior: dict[str, Any]) -> None:
        self.behavior = behavior

    def complete_json(self, prompt: str, model: str | None = None, max_tokens: int = 700) -> Any:
        val = self.behavior[model]
        if isinstance(val, Exception):
            raise val
        return val


def test_probe_ranks_healthy_first_and_maps_penalties() -> None:
    provider = _ProbeProvider(
        {
            "good:free": {"ok": True, "n": 7},
            "empty:free": {},
            "gated": _Http(403, "Key limit exceeded"),
            "prose:free": ValueError("Nessun JSON trovato"),
        }
    )
    results = probe_models(
        provider,  # type: ignore[arg-type]
        ["empty:free", "gated", "good:free", "prose:free"],
        concurrency=4,
    )
    # healthy JSON model ranks first
    assert results[0]["model"] == "good:free"
    assert results[0]["json_ok"] is True

    by_model = {r["model"]: r for r in results}
    assert penalty_reason(by_model["good:free"]) is None
    assert penalty_reason(by_model["empty:free"]) == "empty"
    assert penalty_reason(by_model["gated"]) == "forbidden"
    assert penalty_reason(by_model["prose:free"]) == "json_fail"


def test_probe_empty_list_and_prompt_constant() -> None:
    assert probe_models(_ProbeProvider({}), []) == []  # type: ignore[arg-type]
    assert "JSON" in PROBE_PROMPT


# ── no-catalog fallback must respect penalties ───────────────────────────────
class _NoCatalogProvider:
    """Provider without a live catalog: only select_model works."""

    def __init__(self, name: str, model: str) -> None:
        self.name = name
        self._model = model
        self.key_invalid = False

    def is_available(self) -> bool:
        return True

    def list_models(self) -> list[str]:
        return []

    def select_model(self, preferred_model: str | None = None) -> str:
        return self._model


def test_fallback_no_catalog_skips_penalized_model(tmp_path: Any) -> None:
    """With no live catalog the fallback used to return select_model() even if
    that exact model was just penalized — re-proposing a broken model."""
    mgr = ProviderManager(load_settings(tmp_path))
    p = _NoCatalogProvider("openrouter", "solo-model")
    mgr.providers = {"openrouter": p}  # type: ignore[dict-item]

    assert mgr._ranked_models_for(p, 3) == ["solo-model"]  # type: ignore[arg-type]
    mgr.record_model_penalty("openrouter", "solo-model", "truncated")
    assert mgr._ranked_models_for(p, 3) == []  # type: ignore[arg-type]


def test_penalized_fallback_provider_skipped_in_candidates(tmp_path: Any) -> None:
    """Active provider's only (penalized) fallback model must not outrank a
    healthy next provider."""
    settings = load_settings(tmp_path)
    settings.llm_provider_order = ["openrouter", "groq"]
    mgr = ProviderManager(settings)
    a = _NoCatalogProvider("openrouter", "solo-model")
    b = _NoCatalogProvider("groq", "healthy-model")
    mgr.providers = {"openrouter": a, "groq": b}  # type: ignore[dict-item]
    mgr.active_provider = a  # type: ignore[assignment]
    mgr.active_provider_name = "openrouter"

    mgr.record_model_penalty("openrouter", "solo-model", "truncated")
    candidates = mgr._failover_candidates(None, None)
    assert candidates, "healthy provider must remain a candidate"
    assert candidates[0][0].name == "groq"
    assert all(name != "openrouter" for name, _ in [(p.name, m) for p, m in candidates])


def test_all_penalized_anti_brick_returns_candidate(tmp_path: Any) -> None:
    """Single provider, no catalog, its only model penalized: better a
    penalized model than RuntimeError('No LLM provider available')."""
    settings = load_settings(tmp_path)
    settings.llm_provider_order = ["openrouter"]
    mgr = ProviderManager(settings)
    p = _NoCatalogProvider("openrouter", "solo-model")
    mgr.providers = {"openrouter": p}  # type: ignore[dict-item]
    mgr.active_provider = p  # type: ignore[assignment]
    mgr.active_provider_name = "openrouter"

    mgr.record_model_penalty("openrouter", "solo-model", "truncated")
    candidates = mgr._failover_candidates(None, None)
    assert candidates == [(p, "solo-model")]


# ── penalty map thread-safety (scan workers race) ────────────────────────────
def test_record_penalty_serialized_by_lock(tmp_path: Any) -> None:
    """record_model_penalty must block while another thread holds the penalty
    lock (scan workers mutate the map concurrently with pruning)."""
    import threading

    mgr = ProviderManager(load_settings(tmp_path))

    with mgr._penalty_lock:
        t = threading.Thread(
            target=lambda: mgr.record_model_penalty("openrouter", "x:free", "rate_limit")
        )
        t.start()
        t.join(0.2)
        assert t.is_alive(), "record_model_penalty should be blocked by the lock"
    t.join(2)
    assert not t.is_alive()
    assert "openrouter::x:free" in mgr._model_penalty


def test_penalty_recorded_during_prune_not_lost(tmp_path: Any) -> None:
    """A penalty recorded while _penalized_model_ids is pruning must not be
    silently dropped by the prune's dict reassignment (lost-update race)."""
    import threading

    mgr = ProviderManager(load_settings(tmp_path))
    mgr.record_model_penalty("openrouter", "a:free", "rate_limit")

    in_items = threading.Event()
    release = threading.Event()

    class _SlowItemsDict(dict):  # type: ignore[type-arg]
        def items(self):  # type: ignore[no-untyped-def]
            snapshot = list(super().items())
            in_items.set()
            release.wait(timeout=2)
            return snapshot

    mgr._model_penalty = _SlowItemsDict(mgr._model_penalty)

    pruner = threading.Thread(target=lambda: mgr._penalized_model_ids("openrouter"))
    pruner.start()
    assert in_items.wait(timeout=2)

    recorder = threading.Thread(
        target=lambda: mgr.record_model_penalty("openrouter", "b:free", "empty")
    )
    recorder.start()
    release.set()
    pruner.join(2)
    recorder.join(2)
    assert not pruner.is_alive() and not recorder.is_alive()
    # without locking, the recorder writes into the OLD dict which the pruner
    # then throws away -> the "b" penalty vanishes
    assert "openrouter::b:free" in mgr._model_penalty
