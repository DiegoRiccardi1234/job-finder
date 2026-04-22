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
