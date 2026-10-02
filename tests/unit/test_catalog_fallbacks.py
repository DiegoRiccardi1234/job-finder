"""Cached catalog failure must not cause new catalog I/O during failover."""

from __future__ import annotations

import pytest

from app.config import load_settings
from app.providers import factory
from app.providers.anthropic_provider import AnthropicProvider
from app.providers.cerebras_provider import CerebrasProvider
from app.providers.google_provider import GoogleProvider
from app.providers.groq_provider import GroqProvider
from app.providers.openai_compat import CustomOpenAIProvider
from app.providers.openai_provider import OpenAIProvider
from app.providers.openrouter_provider import OpenRouterProvider


@pytest.mark.parametrize(
    "provider_type",
    [GoogleProvider, GroqProvider, CerebrasProvider, OpenAIProvider, AnthropicProvider],
)
def test_cached_empty_catalog_is_not_refetched_for_initialization_or_candidates(
    tmp_path, monkeypatch, provider_type
):
    manager = factory.ProviderManager(load_settings(tmp_path))
    provider = provider_type(None)
    calls = []

    def unavailable_catalog():
        calls.append(True)
        return []

    monkeypatch.setattr(provider, "is_available", lambda: True)
    monkeypatch.setattr(provider, "list_models", unavailable_catalog)
    manager.providers = {provider.name: provider}
    manager.settings.llm_provider_order = [provider.name]
    manager.initialize()
    assert manager.active_model == provider.default_model
    assert manager._ranked_models_for(provider, 1) == [provider.default_model]
    assert manager._ranked_models_for(provider, 1) == [provider.default_model]
    assert calls == [True], "the five-minute cached empty catalog must cover fallback selection too"


def test_local_endpoint_without_loaded_or_selected_model_is_not_a_candidate(tmp_path, monkeypatch):
    manager = factory.ProviderManager(load_settings(tmp_path))
    provider = CustomOpenAIProvider(None)
    monkeypatch.setattr(provider, "is_available", lambda: True)
    monkeypatch.setattr(provider, "list_models", list)
    manager.providers = {provider.name: provider}
    manager.active_model = "gemini-3.5-flash-lite"
    assert manager._ranked_models_for(provider, 1) == []


def test_health_selection_reaches_a_live_model_outside_the_first_dead_batch(tmp_path, monkeypatch):
    manager = factory.ProviderManager(load_settings(tmp_path))
    provider = OpenRouterProvider(None)
    pool = [f"test/model-{n}-70b:free" for n in range(8)]
    healthy = pool[4]
    observed = []
    monkeypatch.setattr(provider, "is_available", lambda: True)
    monkeypatch.setattr(provider, "list_models", lambda: pool)
    manager.providers = {provider.name: provider}
    monkeypatch.setattr(factory, "rank_models", lambda models, **kwargs: models[: kwargs["limit"]])

    def fetch(_base_url, _key, model):
        observed.append(model)
        return {"status": 0 if model == healthy else -1, "up5m": 100}

    # Retain the real health cache: catalog recommendations and request
    # candidates share endpoint signals instead of performing a second fetch.
    monkeypatch.setattr(factory.model_stats, "_cache", {})
    monkeypatch.setattr(factory.model_stats, "requests", object())
    monkeypatch.setattr(factory.model_stats, "_fetch_one", fetch)
    assert manager.get_models(provider.name)["recommended"] == healthy
    assert manager._ranked_models_for(provider, 1) == [healthy]
    assert healthy in observed
    assert len(observed) <= 8, "health discovery must remain bounded"


@pytest.mark.parametrize(
    ("signal", "usable"),
    [
        ({"status": -1, "endpoint_count": 0}, False),
        ({"status": 0, "up5m": 40}, False),
        ({"status": 0, "up5m": 99}, True),
        (None, True),  # Public metadata unavailable must preserve the user's pin.
    ],
)
def test_explicit_openrouter_pin_checks_health_without_switching_model(
    tmp_path, monkeypatch, signal, usable
):
    manager = factory.ProviderManager(load_settings(tmp_path))
    provider = OpenRouterProvider(None)
    manager.providers = {provider.name: provider}
    pinned = "test/pinned-70b:free"
    checked = []

    def health(_provider, models):
        checked.extend(models)
        return {pinned: signal} if signal is not None else {}

    monkeypatch.setattr(factory.model_stats, "get_model_health", health)
    result = manager._failover_candidates(provider.name, pinned)
    assert result == ([(provider, pinned)] if usable else [])
    assert checked == [pinned]
