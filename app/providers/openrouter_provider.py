"""OpenRouter provider — a thin OpenAI-compatible wrapper.

Shares all behaviour with ``OpenAICompatibleProvider``; only the endpoint and
fallback model differ.
"""

from app.providers.openai_compat import OpenAICompatibleProvider


class OpenRouterProvider(OpenAICompatibleProvider):
    name = "openrouter"
    base_url = "https://openrouter.ai/api/v1"
    # Only used when the catalog cannot be read. "google/gemini-pro" is gone
    # from OpenRouter (checked 2026-11-20); the free router always resolves to a
    # live free model.
    default_model = "openrouter/free"
