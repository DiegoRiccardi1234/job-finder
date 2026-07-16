from pathlib import Path

from app.config import SUPPORTED_PROVIDERS, load_settings, save_local_provider_keys


def test_save_local_provider_keys_persists_and_returns_status(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    status = save_local_provider_keys(
        data_dir,
        openrouter_api_key="sk-router",
        groq_api_key="sk-groq",
        primary_provider="openrouter",
    )

    assert status["openrouter_configured"] is True
    assert status["groq_configured"] is True
    assert status["cerebras_configured"] is False
    assert status["primary_provider"] == "openrouter"
    assert (data_dir / "local_secrets.json").exists()


def test_save_local_provider_keys_clears_when_empty_string(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    save_local_provider_keys(data_dir, openrouter_api_key="sk-router")
    status = save_local_provider_keys(data_dir, openrouter_api_key="")

    assert status["openrouter_configured"] is False


def test_save_local_provider_keys_rejects_unknown_primary(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    status = save_local_provider_keys(data_dir, primary_provider="bogus-provider")
    assert status["primary_provider"] == ""


def test_load_settings_fills_provider_order_with_all_supported(tmp_path: Path) -> None:
    settings = load_settings(tmp_path)
    assert set(settings.llm_provider_order) == set(SUPPORTED_PROVIDERS)
    assert settings.db_path.parent == tmp_path / "data"


def test_load_settings_respects_primary_provider(tmp_path: Path) -> None:
    save_local_provider_keys(
        tmp_path / "data",
        openrouter_api_key="sk-router",
        primary_provider="openrouter",
    )
    settings = load_settings(tmp_path)
    assert settings.llm_provider_order[0] == "openrouter"
    assert settings.openrouter_api_key == "sk-router"


def test_load_optional_json_warns_on_corrupt_file(tmp_path: Path, caplog) -> None:
    """A corrupt local_secrets.json used to silently wipe every key; at least
    warn so the user knows why providers went unconfigured."""
    import logging

    from app.config import _load_optional_json

    p = tmp_path / "local_secrets.json"
    p.write_text("{not valid json", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        assert _load_optional_json(p) == {}
    assert any("local_secrets.json" in r.getMessage() for r in caplog.records)
