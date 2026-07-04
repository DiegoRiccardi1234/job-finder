"""OpenRouter provider — a thin OpenAI-compatible wrapper.

Shares all behaviour with ``OpenAICompatibleProvider``; only the endpoint and
fallback model differ.
"""

from app.providers.openai_compat import OpenAICompatibleProvider


class OpenRouterProvider(OpenAICompatibleProvider):
    name = "openrouter"
    base_url = "https://openrouter.ai/api/v1"
    default_model = "google/gemini-pro"
