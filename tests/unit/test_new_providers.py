"""New OpenAI-compatible providers (DeepSeek, xAI, GLM, Mistral) + shared base.

Pins provider identity, offline model fallback, SDK-retry suppression, and the
config/factory wiring so a valid key round-trips end to end.
"""

from __future__ import annotations

import pytest

from app.config import SUPPORTED_PROVIDERS, load_settings, save_local_provider_keys
from app.providers.factory import ProviderManager
from app.providers.openai_compat import (
    DeepSeekProvider,
    GLMProvider,
    MistralProvider,
    OpenAICompatibleProvider,
    XAIProvider,
)

_NEW = ("deepseek", "xai", "glm", "mistral")


@pytest.fixture(autouse=True)
def _clear_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "CEREBRAS_API_KEY",
        "GROQ_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "OPENROUTER_API_KEY",
        "DEEPSEEK_API_KEY",
        "XAI_API_KEY",
        "GLM_API_KEY",
        "MISTRAL_API_KEY",
        "LLM_PROVIDER",
        "LLM_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)


def test_supported_providers_includes_new() -> None:
    for name in _NEW:
        assert name in SUPPORTED_PROVIDERS


def test_new_providers_are_openai_compatible_subclasses() -> None:
    for cls in (DeepSeekProvider, XAIProvider, GLMProvider, MistralProvider):
        assert issubclass(cls, OpenAICompatibleProvider)


def test_provider_identity_and_offline_default_model() -> None:
    """With no key the client is None; select_model falls back to default_model
    (offline, no network) — so a provider still works even if /models 404s."""
    cases = {
        DeepSeekProvider: ("deepseek", "deepseek-chat", "https://api.deepseek.com"),
        XAIProvider: ("xai", "grok-3-mini", "https://api.x.ai/v1"),
        GLMProvider: ("glm", "glm-4.6", "https://api.z.ai/api/paas/v4"),
        MistralProvider: ("mistral", "mistral-large-latest", "https://api.mistral.ai/v1"),
    }
    for cls, (name, default_model, base_url) in cases.items():
        p = cls(api_key=None)
        assert p.name == name
        assert p.base_url == base_url
        assert p.is_available() is False
        assert p.select_model() == default_model


def test_client_disables_sdk_retries() -> None:
    """SDK-level retries are off; our factory _with_retry owns retries (kills the
    duplicated 429 'Retrying request' log spam)."""
    p = DeepSeekProvider(api_key="sk-test")
    assert p.client is not None
    assert p.client.max_retries == 0


def test_factory_instantiates_new_providers(tmp_path) -> None:
    settings = load_settings(tmp_path)
    mgr = ProviderManager(settings)
    for name in _NEW:
        assert name in mgr.providers
        assert mgr.providers[name].name == name


def test_new_provider_key_round_trips(tmp_path) -> None:
    save_local_provider_keys(tmp_path / "data", deepseek_api_key="sk-deep")
    settings = load_settings(tmp_path)
    assert settings.deepseek_api_key == "sk-deep"


def test_clearing_new_provider_key_removes_it(tmp_path) -> None:
    save_local_provider_keys(tmp_path / "data", mistral_api_key="sk-m")
    save_local_provider_keys(tmp_path / "data", mistral_api_key="")
    settings = load_settings(tmp_path)
    assert settings.mistral_api_key is None
