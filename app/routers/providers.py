from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException

from app.config import SUPPORTED_PROVIDERS, save_local_provider_keys
from app.models import ProviderKeysRequest
from app.providers.model_selector import rank_models
from app.services.model_probe import penalty_reason, probe_models

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
            deepseek_api_key=payload.deepseek_api_key,
            xai_api_key=payload.xai_api_key,
            glm_api_key=payload.glm_api_key,
            mistral_api_key=payload.mistral_api_key,
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
        if provider is None:
            raise HTTPException(status_code=400, detail="key_missing")
        # Distinguish a revoked/wrong key (present but 401'd) from a missing one
        # so the UI can say "check your key" instead of "add a key".
        if getattr(provider, "key_invalid", False):
            raise HTTPException(status_code=400, detail="key_invalid")
        if not provider.is_available():
            raise HTTPException(status_code=400, detail="key_missing")
        result = container.providers.get_models(name, force_refresh=bool(force_refresh))
        return {"ok": True, "provider": name, **result}

    @router.post("/api/providers/{name}/probe")
    def provider_probe(name: str, limit: int = 12) -> dict[str, Any]:
        """Benchmark this provider's models with a tiny JSON prompt: which ones
        actually respond fast and return valid JSON. Seeds the factory penalty
        map for the dead/empty/gated ones and caches the ranking so selection and
        the UI can use real signals, not just the name heuristic."""
        if name not in SUPPORTED_PROVIDERS:
            raise HTTPException(status_code=404, detail="unknown_provider")
        provider = container.providers.providers.get(name)
        if provider is None:
            raise HTTPException(status_code=400, detail="key_missing")
        if getattr(provider, "key_invalid", False):
            raise HTTPException(status_code=400, detail="key_invalid")
        if not provider.is_available():
            raise HTTPException(status_code=400, detail="key_missing")

        models = container.providers.get_models(name).get("models") or []
        if not models:
            raise HTTPException(status_code=400, detail="no_models")
        # Probe the most promising candidates first (free + fast bias), bounded.
        ranked = rank_models(models, policy={"prefer_free": True, "prefer_fast": True})
        candidates = ranked[: max(1, min(limit, 20))]

        results = probe_models(provider, candidates)

        # Feed empirical signals back into auto-selection immediately.
        for res in results:
            reason = penalty_reason(res)
            if reason:
                container.providers.record_model_penalty(name, res["model"], reason)

        best = next((r["model"] for r in results if r["json_ok"]), None)
        payload = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "results": results,
            "best": best,
        }
        with contextlib.suppress(Exception):  # persistence is best-effort
            container.db.set_preference(f"model_probe_{name}", json.dumps(payload))
        return {"ok": True, "provider": name, **payload}

    return router
