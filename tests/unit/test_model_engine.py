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
