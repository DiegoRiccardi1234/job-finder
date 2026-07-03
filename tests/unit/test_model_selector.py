"""Tests for model_selector heuristic, especially v1.2.0 new patterns."""

from __future__ import annotations

from app.providers.model_selector import (
    choose_best_model,
    pick_default_model,
    score_model_name,
)


def test_pick_default_returns_none_for_empty_list() -> None:
    assert pick_default_model("openai", []) is None


def test_pick_default_excludes_embedding_only() -> None:
    result = pick_default_model("openai", ["text-embedding-3-large", "gpt-4o-2024-11"])
    assert result == "gpt-4o-2024-11"


def test_pick_default_excludes_tts_and_whisper() -> None:
    result = pick_default_model("openai", ["whisper-1", "tts-1", "gpt-4o-mini"])
    assert result == "gpt-4o-mini"


def test_pick_default_returns_none_when_all_avoided() -> None:
    assert pick_default_model("any", ["dall-e-3", "tts-1", "whisper-1"]) is None


def test_free_tier_wins_among_equivalents() -> None:
    free = "openai/gpt-oss-120b:free"
    paid = "openai/gpt-oss-120b"
    result = pick_default_model("openrouter", [paid, free])
    assert result == free


def test_preview_models_lose_to_stable() -> None:
    result = pick_default_model("openai", ["gpt-5-preview", "gpt-5-chat"])
    assert result == "gpt-5-chat"


def test_score_function_penalizes_embedding() -> None:
    embed = score_model_name("text-embedding-3-large")
    chat = score_model_name("gpt-4o-mini")
    assert chat > embed
    assert embed < -500


def test_choose_best_model_prefers_recent_llama() -> None:
    models = ["llama-3-8b-instruct", "llama-3.3-70b-instruct"]
    assert choose_best_model(models) == "llama-3.3-70b-instruct"


def test_new_provider_families_get_family_bonus() -> None:
    """DeepSeek/xAI/GLM/Mistral models must score above an unknown model, so the
    ⭐ recommended pick is sensible for the newly-added providers."""
    baseline = score_model_name("mystery-model-xyz")
    for m in ("deepseek-chat", "grok-3-mini", "glm-4.6", "mistral-large-latest", "kimi-k2"):
        assert score_model_name(m) > baseline, m


def test_choose_best_prefers_known_new_family_over_unknown() -> None:
    assert choose_best_model(["mystery-xyz", "deepseek-chat"]) == "deepseek-chat"
    assert choose_best_model(["some-random-id", "mistral-large-latest"]) == "mistral-large-latest"


def test_new_family_models_are_default_pickable() -> None:
    assert pick_default_model("deepseek", ["deepseek-chat", "deepseek-coder"]) is not None
    assert pick_default_model("xai", ["grok-3", "grok-3-mini"]) is not None
