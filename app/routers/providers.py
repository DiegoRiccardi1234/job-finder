from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException

from app.config import SUPPORTED_PROVIDERS, save_local_provider_keys
from app.models import ProviderKeysRequest

if TYPE_CHECKING:
    from app.container import AppContainer


def build_router(container: AppContainer) -> APIRouter:
    router = APIRouter()

    @router.get("/api/providers/keys/status")
    def providers_keys_status() -> dict[str, Any]:
        return {
            "ok": True,
            "keys": container.keys_status(),
            "provider": container.providers.metadata(),
        }

    @router.post("/api/providers/keys")
    def save_provider_keys(payload: ProviderKeysRequest) -> dict[str, Any]:
        local_status = save_local_provider_keys(
            data_dir=container.settings.data_dir,
            cerebras_api_key=payload.cerebras_api_key,
            groq_api_key=payload.groq_api_key,
            openai_api_key=payload.openai_api_key,
            anthropic_api_key=payload.anthropic_api_key,
            google_api_key=payload.google_api_key,
            openrouter_api_key=payload.openrouter_api_key,
            primary_provider=payload.primary_provider,
            preferred_model=payload.preferred_model,
        )
        container.reload_providers()
        return {
            "ok": True,
            "keys": {**local_status, **container.keys_status()},
            "provider": container.providers.metadata(),
        }

    @router.get("/api/providers/{name}/models")
    def provider_models(name: str, force_refresh: int = 0) -> dict[str, Any]:
        if name not in SUPPORTED_PROVIDERS:
            raise HTTPException(status_code=404, detail="unknown_provider")
        provider = container.providers.providers.get(name)
        if not provider or not provider.is_available():
            raise HTTPException(status_code=400, detail="key_missing")
        result = container.providers.get_models(name, force_refresh=bool(force_refresh))
        return {"ok": True, "provider": name, **result}

    return router
