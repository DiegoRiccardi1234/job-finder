import pytest

from app.config import load_settings
from app.providers.factory import ProviderManager


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
        "LLM_PROVIDER",
        "LLM_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)


def test_provider_manager_initializes_with_no_keys(tmp_path) -> None:
    settings = load_settings(tmp_path)
    mgr = ProviderManager(settings)
    mgr.initialize()

    assert mgr.active_provider is None
    assert mgr.active_provider_name == "none"
    meta = mgr.metadata()
    assert meta["available"] is False
    assert "openrouter" in meta["providers"]


def test_provider_manager_chat_raises_when_no_provider(tmp_path) -> None:
    settings = load_settings(tmp_path)
    mgr = ProviderManager(settings)
    mgr.initialize()

    with pytest.raises(RuntimeError, match="No LLM provider"):
        mgr.chat(messages=[{"role": "user", "content": "hi"}])


def test_metadata_is_cached(tmp_path) -> None:
    """Two consecutive metadata() calls must return the same cached payload.

    Regression for the Cerebras 401 spam: every health-poll used to re-call
    list_models() on every provider with a key, even when the key was bad.
    """
    settings = load_settings(tmp_path)
    mgr = ProviderManager(settings)
    mgr.initialize()

    first = mgr.metadata()
    second = mgr.metadata()
    assert first is second  # identity == cached


def test_metadata_force_refresh_bypasses_cache(tmp_path) -> None:
    settings = load_settings(tmp_path)
    mgr = ProviderManager(settings)
    mgr.initialize()

    first = mgr.metadata()
    refreshed = mgr.metadata(force_refresh=True)
    assert first is not refreshed


def test_invalidate_caches_clears_metadata_and_resets_key_invalid(tmp_path) -> None:
    settings = load_settings(tmp_path)
    mgr = ProviderManager(settings)
    mgr.initialize()

    # Simulate a 401-flagged provider — would normally come from a real list_models call.
    mgr.providers["cerebras"].key_invalid = True
    mgr.metadata()  # populates cache

    mgr.invalidate_caches()

    assert mgr._metadata_cache is None
    assert mgr.providers["cerebras"].key_invalid is False


def test_metadata_skips_list_models_when_key_invalid(tmp_path, monkeypatch) -> None:
    """Once key_invalid is set, metadata() must NOT call provider.list_models()."""
    settings = load_settings(tmp_path)
    mgr = ProviderManager(settings)
    mgr.initialize()

    provider = mgr.providers["cerebras"]
    provider.key_invalid = True
    call_count = {"n": 0}

    def _spy() -> list[str]:
        call_count["n"] += 1
        return []

    monkeypatch.setattr(provider, "list_models", _spy)

    mgr.metadata(force_refresh=True)
    assert call_count["n"] == 0
