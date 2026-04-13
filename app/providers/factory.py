from typing import Any

from app.config import AppSettings
from app.providers.anthropic_provider import AnthropicProvider
from app.providers.base import LLMProvider
from app.providers.cerebras_provider import CerebrasProvider
from app.providers.google_provider import GoogleProvider
from app.providers.groq_provider import GroqProvider
from app.providers.openai_provider import OpenAIProvider
from app.providers.openrouter_provider import OpenRouterProvider


class ProviderManager:
    def __init__(self, settings: AppSettings):
        self.settings = settings
        self.providers: dict[str, LLMProvider] = {
            "cerebras": CerebrasProvider(api_key=settings.cerebras_api_key),
            "groq": GroqProvider(api_key=settings.groq_api_key),
            "openai": OpenAIProvider(api_key=settings.openai_api_key),
            "anthropic": AnthropicProvider(api_key=settings.anthropic_api_key),
            "google": GoogleProvider(api_key=settings.google_api_key),
            "openrouter": OpenRouterProvider(api_key=settings.openrouter_api_key),
        }
        self.active_provider: LLMProvider | None = None
        self.active_provider_name: str = "none"
        self.active_model: str = "none"

    def initialize(self) -> None:
        for provider_name in self.settings.llm_provider_order:
            provider = self.providers.get(provider_name)
            if not provider or not provider.is_available():
                continue

            # Provider sceglie il miglior modello disponibile; poi possiamo forzare via preferred_model.
            selected_model = provider.select_model(preferred_model=self.settings.preferred_model)
            # Per i provider che espongono list_models, applichiamo policy centralizzata.
            try:
                from app.providers.model_selector import choose_best_model

                models = provider.list_models()
                if models:
                    selected_model = choose_best_model(
                        models=models,
                        preferred_model=self.settings.preferred_model,
                        policy=self.settings.model_selection_policy,
                    )
            except Exception:
                pass

            self.active_provider = provider
            self.active_provider_name = provider.name
            self.active_model = selected_model
            return

        self.active_provider = None
        self.active_provider_name = "none"
        self.active_model = "none"

    def metadata(self) -> dict[str, Any]:
        providers_metadata: dict[str, Any] = {}
        for name, provider in self.providers.items():
            try:
                available = provider.is_available()
            except Exception:
                available = False

            models: list[str] = []
            if available:
                try:
                    models = provider.list_models()
                except Exception:
                    models = []

            providers_metadata[name] = {
                "available": available,
                "models": models,
            }

        return {
            "active_provider": self.active_provider_name,
            "active_model": self.active_model,
            "available": self.active_provider is not None,
            "providers": providers_metadata,
        }

    def complete_json(self, prompt: str, max_tokens: int = 700, provider_name: str | None = None, model_name: str | None = None) -> dict[str, Any]:
        provider = self.providers.get(provider_name) if provider_name else self.active_provider
        active_model = model_name if model_name else self.active_model
        if not provider:
            raise RuntimeError("Nessun provider LLM disponibile")
        return provider.complete_json(prompt=prompt, model=active_model, max_tokens=max_tokens)

    def chat(self, messages: list[dict[str, str]], max_tokens: int = 700, provider_name: str | None = None, model_name: str | None = None) -> str:
        provider = self.providers.get(provider_name) if provider_name else self.active_provider
        active_model = model_name if model_name else self.active_model
        if not provider:
            raise RuntimeError("Nessun provider LLM disponibile")
        return provider.chat(messages=messages, model=active_model, max_tokens=max_tokens)
