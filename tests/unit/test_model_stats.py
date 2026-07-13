"""OpenRouter model-health stats: endpoint parsing, unhealthy detection, the
cached/guarded get_model_health, and the factory ranking hook that sinks models
that are down right now."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.config import load_settings
from app.providers.base import LLMProvider
from app.providers.factory import ProviderManager
from app.services import model_stats as ms


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    ms._cache.clear()


def _endpoints(*eps: dict[str, Any]) -> dict[str, Any]:
    return {"data": {"endpoints": list(eps)}}


# ── parsing ──────────────────────────────────────────────────────────────────


def test_parse_picks_healthiest_and_uses_p50() -> None:
    payload = _endpoints(
        {
            "provider_name": "Down",
            "status": -2,
            "uptime_last_30m": 100.0,
            "latency_last_30m": {"p50": 300},
        },
        {
            "provider_name": "Good",
            "status": 0,
            "uptime_last_5m": 100.0,
            "uptime_last_30m": 99.5,
            "latency_last_30m": {"p50": 550, "p90": 1200},
            "throughput_last_30m": {"p50": 74},
            "context_length": 131072,
            "max_completion_tokens": 32768,
        },
    )
    s = ms._parse_endpoints(payload)
    assert s is not None
    # status-0 endpoint wins even though the down one advertises higher uptime.
    assert s["provider_name"] == "Good"
    assert s["lat_ms"] == 550.0
    assert s["tput"] == 74.0
    assert s["ctx"] == 131072
    assert s["maxc"] == 32768


def test_parse_no_endpoints_returns_none() -> None:
    assert ms._parse_endpoints({"data": {"endpoints": []}}) is None
    assert ms._parse_endpoints({}) is None


def test_p50_tolerates_scalar_and_junk() -> None:
    assert ms._p50({"p50": 12}) == 12.0
    assert ms._p50(9) == 9.0
    assert ms._p50(None) is None
    assert ms._p50("nope") is None


# ── unhealthy detection ──────────────────────────────────────────────────────


def test_unhealthy_ids_flags_status_and_low_uptime() -> None:
    health = {
        "up": {"status": 0, "up5m": 100.0},
        "bad-status": {"status": -2, "up5m": 100.0},
        "low-uptime": {"status": 0, "up5m": 10.0},
        "no-signal": {"status": None, "up5m": None},  # unknown -> treated healthy
    }
    assert ms.unhealthy_ids(health) == {"bad-status", "low-uptime"}


# ── get_model_health (guards + cache) ────────────────────────────────────────

_OR = SimpleNamespace(name="openrouter", api_key="k", base_url="https://openrouter.ai/api/v1")


def test_health_non_openrouter_returns_empty() -> None:
    groq = SimpleNamespace(name="groq", api_key="k", base_url="https://x")
    assert ms.get_model_health(groq, ["a"]) == {}  # type: ignore[arg-type]


def test_health_fetch_error_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ms, "_fetch_one", lambda *a: None)  # every fetch "fails"
    assert ms.get_model_health(_OR, ["a", "b"]) == {}  # type: ignore[arg-type]


def test_health_happy_and_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def fake(base: str, key: str, mid: str) -> dict[str, Any]:
        calls["n"] += 1
        return {"status": 0, "up5m": 100.0, "lat_ms": 200.0}

    monkeypatch.setattr(ms, "_fetch_one", fake)
    h1 = ms.get_model_health(_OR, ["a", "b"])  # type: ignore[arg-type]
    h2 = ms.get_model_health(_OR, ["a", "b"])  # type: ignore[arg-type]
    assert set(h1) == {"a", "b"}
    assert h1 == h2
    assert calls["n"] == 2  # fetched once each; second call is a cache hit


# ── factory ranking hook ─────────────────────────────────────────────────────


class _ORProvider(LLMProvider):
    name = "openrouter"

    def __init__(self, models: list[str]) -> None:
        self._models = models
        self.key_invalid = False
        self.api_key = "k"
        self.base_url = "https://openrouter.ai/api/v1"

    def is_available(self) -> bool:
        return True

    def list_models(self) -> list[str]:
        return self._models

    def select_model(self, preferred_model: str | None = None) -> str:
        return self._models[0]

    def complete_text(self, prompt: str, model: str | None = None, max_tokens: int = 700) -> str:
        return ""

    def chat(
        self, messages: list[dict[str, str]], model: str | None = None, max_tokens: int = 700
    ) -> str:
        return ""

    def complete_json(
        self, prompt: str, model: str | None = None, max_tokens: int = 700
    ) -> dict[str, Any]:
        return {}


def _mgr(tmp_path: Any, prov: _ORProvider) -> ProviderManager:
    settings = load_settings(tmp_path)
    settings.llm_provider_order = ["openrouter"]
    mgr = ProviderManager(settings)
    mgr.providers = {"openrouter": prov}  # type: ignore[assignment]
    return mgr


def test_ranked_models_for_sinks_down_models(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    prov = _ORProvider(["a-120b-instruct", "b-70b-instruct"])
    mgr = _mgr(tmp_path, prov)

    # Baseline: no health signal -> the larger model ranks first (name heuristic).
    monkeypatch.setattr(ms, "get_model_health", lambda p, ids: {})
    assert mgr._ranked_models_for(prov, 5)[0] == "a-120b-instruct"

    # Mark the 120b endpoint down -> it must sink below the healthy 70b.
    monkeypatch.setattr(
        ms, "get_model_health", lambda p, ids: {"a-120b-instruct": {"status": -2, "up5m": 0.0}}
    )
    ranked = mgr._ranked_models_for(prov, 5)
    assert ranked[0] == "b-70b-instruct"
    assert ranked[-1] == "a-120b-instruct"
