import re
from typing import Any


def _weight(policy: dict[str, Any] | None, key: str, default: int) -> int:
    if not policy:
        return default
    weights = policy.get("weights", {})
    if not isinstance(weights, dict):
        return default
    value = weights.get(key, default)
    try:
        return int(value)
    except Exception:
        return default


def score_model_name(model_name: str, policy: dict[str, Any] | None = None) -> int:
    name = model_name.lower()
    score = 0

    if "instruct" in name:
        score += _weight(policy, "instruct", 30)
    if "chat" in name:
        score += _weight(policy, "chat", 15)

    if "llama-4" in name or "llama4" in name:
        score += _weight(policy, "family", 40)
    elif "llama-3.3" in name or "llama3.3" in name:
        score += _weight(policy, "family", 40) - 5
    elif "llama-3" in name or "llama3" in name:
        score += _weight(policy, "family", 40) - 15
    elif "qwen" in name:
        score += _weight(policy, "family", 40) - 2
    elif "gpt-4.1" in name or "gpt-4o" in name:
        score += _weight(policy, "family", 40) + 6
    elif "gpt" in name or "deepseek" in name or "grok" in name:
        score += _weight(policy, "family", 40)
    elif "claude" in name:
        score += _weight(policy, "family", 40) + 4
    elif "gemini" in name:
        score += _weight(policy, "family", 40) + 2
    elif (
        "mistral" in name
        or "mixtral" in name
        or "codestral" in name
        or "magistral" in name
        or "glm" in name
        or "chatglm" in name
    ):
        score += _weight(policy, "family", 40) - 2
    elif "kimi" in name:
        score += _weight(policy, "family", 40) - 4
    elif "command-r" in name or "command" in name:
        score += _weight(policy, "family", 40) - 6

    parsed_sizes = [int(x) for x in re.findall(r"(\d{1,4})b", name)]
    size_b = max(parsed_sizes) if parsed_sizes else 0
    base_size_weight = _weight(policy, "size", 20)
    if size_b >= 200:
        score += base_size_weight + 8
    elif size_b >= 70:
        score += base_size_weight
    elif size_b >= 30:
        score += base_size_weight - 6
    elif size_b >= 8:
        score += base_size_weight - 12

    if "reason" in name:
        score += _weight(policy, "reasoning", 6)
    if "sonnet" in name:
        score += 4
    if "opus" in name:
        score += 5
    if "pro" in name:
        score += 2
    if "json" in name or "tool" in name:
        score += _weight(policy, "json", 12)
    if "turbo" in name or "instant" in name or "flash" in name:
        score += _weight(policy, "speed", 8)

    if "vision" in name:
        score += _weight(policy, "vision_penalty", -8)

    # Hard-avoid patterns: models that are not general-purpose chat completers.
    avoid_patterns = (
        "embed",
        "embedding",
        "whisper",
        "tts",
        "dall-e",
        "moderation",
        "audio",
        "image-",
    )
    if any(p in name for p in avoid_patterns):
        score += _weight(policy, "non_chat_penalty", -1000)

    # Soft-avoid patterns: experimental/preview/deprecated builds.
    soft_avoid = ("preview", "deprecated", "experimental", "alpha")
    if any(p in name for p in soft_avoid):
        score += _weight(policy, "preview_penalty", -50)

    # OpenRouter free tier suffix: prefer ":free" so users can start without
    # paying, but only as a tie-breaker among equivalent models — a +25 bonus
    # used to hoist a rate-limited free model over a clearly better different
    # one, feeding the 429 storms. +5 still wins genuine ties (same base model,
    # free vs paid) without overriding a meaningfully stronger option; true
    # de-ranking of a model after repeated 429s is handled by request failover.
    if name.endswith(":free"):
        score += _weight(policy, "free_bonus", 5)

    max_cost_tier = str((policy or {}).get("max_cost_tier", "high")).lower()
    if max_cost_tier in {"low", "medium"} and size_b >= 70:
        score -= 15
    if max_cost_tier == "low" and size_b >= 30:
        score -= 8

    prefer_fast = bool((policy or {}).get("prefer_fast", True))
    prefer_quality = bool((policy or {}).get("prefer_quality", True))
    if prefer_fast and ("instant" in name or "turbo" in name):
        score += 6
    if prefer_quality and (size_b >= 70 or "reason" in name):
        score += 6
    if prefer_fast and size_b >= 200:
        score -= 5

    return score


def _penalized_key(x: str, policy: dict[str, Any] | None, penalized: set[str] | None) -> int:
    penalty = 10_000 if penalized and x in penalized else 0
    return score_model_name(x, policy=policy) - penalty


def rank_models(
    models: list[str],
    preferred_model: str | None = None,
    policy: dict[str, Any] | None = None,
    *,
    penalized: set[str] | None = None,
    limit: int | None = None,
) -> list[str]:
    """Return chat-capable models ordered best-first.

    Hard-avoid models (embeddings/TTS/… score <= -500) are excluded. ``penalized``
    (e.g. models seen 429ing recently) are pushed to the bottom via a large
    penalty but kept in the list. An explicit ``preferred_model`` (a user choice)
    is hoisted to the front. ``limit`` truncates the result. Used both for the
    "recommended" default and for per-request intra-provider failover.
    """
    viable = [m for m in models if score_model_name(m, policy=policy) > -500]
    ranked = sorted(viable, key=lambda x: _penalized_key(x, policy, penalized), reverse=True)
    if preferred_model:
        pref = next((m for m in models if m.lower() == preferred_model.lower()), None)
        if pref is not None:
            ranked = [pref] + [m for m in ranked if m != pref]
    if limit is not None:
        ranked = ranked[: max(0, limit)]
    return ranked


def choose_best_model(
    models: list[str],
    preferred_model: str | None = None,
    policy: dict[str, Any] | None = None,
    *,
    penalized: set[str] | None = None,
) -> str:
    """Pick the highest-scoring model. ``penalized`` (e.g. models seen 429ing
    recently) get a large score penalty so auto-selection avoids them, but they
    are never excluded — if every model is penalized, one is still returned. An
    explicit ``preferred_model`` always wins (a user choice overrides de-ranking)."""
    if preferred_model:
        for model in models:
            if model.lower() == preferred_model.lower():
                return model

    if not models:
        raise RuntimeError("Nessun modello disponibile")

    ranked = rank_models(models, policy=policy, penalized=penalized)
    if ranked:
        return ranked[0]
    # Every model is hard-avoid: never exclude everything — return the best of them.
    return sorted(models, key=lambda x: _penalized_key(x, policy, penalized), reverse=True)[0]


def pick_default_model(
    provider_name: str, models: list[str], policy: dict[str, Any] | None = None
) -> str | None:
    """Return the best default model for a provider, or None if none viable.

    Used by the factory when the user selects "Auto (provider default)".
    Filters out models with hard-avoid patterns (embeddings, TTS, etc.) before
    ranking, so an embedding-only key never matches as a chat default.
    """
    if not models:
        return None
    viable = [
        m
        for m in models
        if score_model_name(m, policy=policy) > -500  # excludes hard-avoid
    ]
    if not viable:
        return None
    return choose_best_model(viable, policy=policy)
