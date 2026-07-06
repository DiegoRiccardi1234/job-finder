"""Reusable base for OpenAI-API-compatible providers + the concrete new ones.

Every provider that speaks the OpenAI Chat Completions API (DeepSeek, xAI/Grok,
Zhipu GLM, Mistral, OpenRouter, …) differs only by ``base_url`` and a fallback
``default_model``. ``OpenAICompatibleProvider`` captures the shared logic once;
adding a provider is now a ~4-line subclass. The client is built with
``max_retries=0`` so retries are owned solely by ``factory._with_retry`` (no
duplicated SDK-level 429 retry spam).
"""

import json
import re
from typing import Any, cast

from app.log import get_logger
from app.providers.base import LLMProvider, extract_usage, is_unauthorized
from app.providers.model_selector import choose_best_model

log = get_logger(__name__)

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None  # type: ignore[assignment,misc]


def _extract_json(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("Nessun JSON trovato")
    return cast(dict[str, Any], json.loads(match.group()))


class OpenAICompatibleProvider(LLMProvider):
    """Concrete provider for any OpenAI-compatible Chat Completions endpoint.

    Subclasses set ``name`` (class attr), ``base_url`` and ``default_model``.
    An empty ``base_url`` means the OpenAI default endpoint.
    """

    base_url: str = ""
    default_model: str = ""

    def __init__(self, api_key: str | None, base_url: str | None = None):
        self.api_key = api_key
        # An explicit override shadows the class default (e.g. GLM China console).
        self.base_url = base_url or type(self).base_url
        client_kwargs: dict[str, Any] = {"api_key": api_key, "max_retries": 0}
        if self.base_url:
            client_kwargs["base_url"] = self.base_url
        self.client = OpenAI(**client_kwargs) if (api_key and OpenAI is not None) else None
        self._selected_model: str | None = None

    def is_available(self) -> bool:
        return self.client is not None and not self.key_invalid

    def list_models(self) -> list[str]:
        if not self.client or self.key_invalid:
            return []
        try:
            models = self.client.models.list()
            output: list[str] = []
            for model in models.data:
                model_id = getattr(model, "id", "")
                if model_id:
                    output.append(str(model_id))
            return output
        except Exception as exc:
            if is_unauthorized(exc):
                self.key_invalid = True
                log.warning("%s key marked invalid (401); will skip until reload.", self.name)
            else:
                log.warning("%s list_models failed: %s", self.name, exc)
            return []

    def select_model(self, preferred_model: str | None = None) -> str:
        models = self.list_models()
        if not models:
            fallback = preferred_model or self.default_model
            self._selected_model = fallback
            return fallback
        selected = choose_best_model(models, preferred_model=preferred_model)
        self._selected_model = selected
        return selected

    def complete_text(self, prompt: str, model: str | None = None, max_tokens: int = 700) -> str:
        if not self.client:
            raise RuntimeError(f"{self.name} not configured")
        resolved_model = model or self._selected_model or self.select_model()
        response = self.client.chat.completions.create(
            model=resolved_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=max_tokens,
        )
        self.last_usage = extract_usage(response)
        return (response.choices[0].message.content or "").strip()

    def chat(
        self, messages: list[dict[str, str]], model: str | None = None, max_tokens: int = 700
    ) -> str:
        if not self.client:
            raise RuntimeError(f"{self.name} not configured")
        resolved_model = model or self._selected_model or self.select_model()
        response = self.client.chat.completions.create(
            model=resolved_model,
            messages=messages,  # type: ignore[arg-type]
            temperature=0.2,
            max_tokens=max_tokens,
        )
        self.last_usage = extract_usage(response)
        return (response.choices[0].message.content or "").strip()

    def complete_json(
        self, prompt: str, model: str | None = None, max_tokens: int = 700
    ) -> dict[str, Any]:
        if not self.client:
            raise RuntimeError(f"{self.name} not configured")
        resolved_model = model or self._selected_model or self.select_model()
        try:
            response = self.client.chat.completions.create(
                model=resolved_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=max_tokens,
                # Not all compatible models support json_object; rely on prompt + fallback.
            )
            self.last_usage = extract_usage(response)
            content = (response.choices[0].message.content or "").strip()
            return cast(dict[str, Any], json.loads(content))
        except (json.JSONDecodeError, Exception) as exc:
            log.info("%s complete_json fallback (model=%s): %s", self.name, resolved_model, exc)
            text = self.complete_text(prompt=prompt, model=resolved_model, max_tokens=max_tokens)
            return _extract_json(text)


class DeepSeekProvider(OpenAICompatibleProvider):
    name = "deepseek"
    base_url = "https://api.deepseek.com"
    default_model = "deepseek-chat"


class XAIProvider(OpenAICompatibleProvider):
    name = "xai"
    base_url = "https://api.x.ai/v1"
    default_model = "grok-3-mini"


class GLMProvider(OpenAICompatibleProvider):
    name = "glm"
    base_url = "https://api.z.ai/api/paas/v4"
    default_model = "glm-4.6"


class MistralProvider(OpenAICompatibleProvider):
    name = "mistral"
    base_url = "https://api.mistral.ai/v1"
    default_model = "mistral-large-latest"
