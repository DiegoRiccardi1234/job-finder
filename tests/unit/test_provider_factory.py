import pytest

from app.config import load_settings
from app.providers.base import LLMProvider
from app.providers.factory import ProviderManager


class _FakeProvider(LLMProvider):
    """Minimal LLMProvider double for factory selection tests.

    ``invalid_on_list`` mimics a revoked key: ``list_models`` flips
    ``key_invalid`` and returns ``[]`` (as the real providers do on 401),
    while ``select_model`` still returns a fallback string WITHOUT raising —
    the exact masking behaviour that used to keep a dead provider "active".
    """

    def __init__(
        self,
        name: str,
        available: bool = True,
        invalid_on_list: bool = False,
        models: list[str] | None = None,
    ) -> None:
        self.name = name
        self._available = available
        self._invalid_on_list = invalid_on_list
        self._models = models or []
        self.key_invalid = False

    def is_available(self) -> bool:
        return self._available and not self.key_invalid

    def list_models(self) -> list[str]:
        if self._invalid_on_list:
            self.key_invalid = True
            return []
        return list(self._models)

    def select_model(self, preferred_model: str | None = None) -> str:
        models = self.list_models()
        return models[0] if models else (preferred_model or "fallback-model")

    def complete_text(self, prompt: str, model: str | None = None, max_tokens: int = 700) -> str:
        return ""

    def chat(
        self, messages: list[dict[str, str]], model: str | None = None, max_tokens: int = 700
    ) -> str:
        return ""

    def complete_json(self, prompt: str, model: str | None = None, max_tokens: int = 700) -> dict:
        return {}


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


def test_initialize_skips_provider_with_invalid_key(tmp_path) -> None:
    """A dead first provider (401 on list_models) must NOT become active.

    Regression: the first provider's ``select_model`` returns a fallback string
    without raising, so ``initialize`` used to commit to it even after the key
    was flagged invalid — bricking the LLM until the user changed the primary
    by hand. The factory must skip it and select the next valid provider.
    """
    settings = load_settings(tmp_path)
    mgr = ProviderManager(settings)
    mgr.providers = {
        "cerebras": _FakeProvider("cerebras", invalid_on_list=True),
        "openrouter": _FakeProvider("openrouter", models=["good-model"]),
    }
    settings.llm_provider_order = ["cerebras", "openrouter"]

    mgr.initialize()

    assert mgr.active_provider_name == "openrouter"
    assert mgr.active_model == "good-model"


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
