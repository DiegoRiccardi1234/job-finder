import functools
import os as _os
import random as _random
import threading
import time as _time
from collections.abc import Callable
from typing import Any, TypeVar

from app.config import AppSettings
from app.log import get_logger
from app.providers.anthropic_provider import AnthropicProvider
from app.providers.base import LLMProvider, is_unauthorized
from app.providers.cerebras_provider import CerebrasProvider
from app.providers.google_provider import GoogleProvider
from app.providers.groq_provider import GroqProvider
from app.providers.model_selector import choose_best_model
from app.providers.openai_compat import (
    DeepSeekProvider,
    GLMProvider,
    MistralProvider,
    XAIProvider,
)
from app.providers.openai_provider import OpenAIProvider
from app.providers.openrouter_provider import OpenRouterProvider

_RetryT = TypeVar("_RetryT")

log = get_logger(__name__)

_MODELS_CACHE_TTL_SECONDS = 300.0
# Health endpoint hits this every poll; keep responses fast and avoid hammering
# providers that have invalid keys. Shorter than _MODELS_CACHE because settings
# changes invalidate this cache anyway.
_METADATA_CACHE_TTL_SECONDS = 60.0
# After a provider is flagged key_invalid (401), re-probe it once this many
# seconds have passed — a transient 401 shouldn't disable it for the whole
# session. Env-overridable like the retry knobs.
_KEY_INVALID_COOLDOWN_SECONDS = float(_os.environ.get("LLM_KEY_INVALID_COOLDOWN_SECONDS", "600"))
# After a model returns a persistent 429 (rate limit), avoid auto-picking it for
# this many seconds so selection rotates to another model.
_MODEL_429_COOLDOWN_SECONDS = float(_os.environ.get("LLM_MODEL_429_COOLDOWN_SECONDS", "300"))


class ProviderManager:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.providers: dict[str, LLMProvider] = {
            "cerebras": CerebrasProvider(api_key=settings.cerebras_api_key),
            "groq": GroqProvider(api_key=settings.groq_api_key),
            "openai": OpenAIProvider(api_key=settings.openai_api_key),
            "anthropic": AnthropicProvider(api_key=settings.anthropic_api_key),
            "google": GoogleProvider(api_key=settings.google_api_key),
            "openrouter": OpenRouterProvider(api_key=settings.openrouter_api_key),
            "deepseek": DeepSeekProvider(api_key=settings.deepseek_api_key),
            "xai": XAIProvider(api_key=settings.xai_api_key),
            "glm": GLMProvider(api_key=settings.glm_api_key, base_url=settings.glm_base_url),
            "mistral": MistralProvider(api_key=settings.mistral_api_key),
        }
        self.active_provider: LLMProvider | None = None
        self.active_provider_name: str = "none"
        self.active_model: str = "none"
        self._models_cache: dict[str, tuple[float, list[str]]] = {}
        self._metadata_cache: tuple[float, dict[str, Any]] | None = None
        # When each provider was first observed key_invalid (for the re-probe
        # cooldown). Reset for free on reload_providers (new manager instance).
        self._key_invalid_since: dict[str, float] = {}
        # When each model last returned a persistent 429 (for de-rank cooldown).
        self._model_429_at: dict[str, float] = {}
        # Set by AppContainer after the DB is open; ``_record_call`` uses it
        # to persist token usage. None = no-op (unit tests, isolated usage).
        self._db: Any = None

    def initialize(self) -> None:
        """Pick first available provider from configured order and select a model."""
        for provider_name in self.settings.llm_provider_order:
            provider = self.providers.get(provider_name)
            if not provider:
                log.debug("Unknown provider in order: %s", provider_name)
                continue

            try:
                available = provider.is_available()
            except Exception as exc:
                log.warning("Provider %s is_available() raised: %s", provider_name, exc)
                continue

            if not available:
                log.debug("Provider %s not available (no key)", provider_name)
                continue

            try:
                selected_model = provider.select_model(
                    preferred_model=self.settings.preferred_model
                )
            except Exception as exc:
                log.warning("Provider %s select_model failed: %s", provider_name, exc)
                continue

            try:
                models = provider.list_models()
                if models:
                    selected_model = choose_best_model(
                        models=models,
                        preferred_model=self.settings.preferred_model,
                        policy=self.settings.model_selection_policy,
                        penalized=self._recent_429_models(),
                    )
            except Exception as exc:
                log.info(
                    "Provider %s list_models failed, using select_model result: %s",
                    provider_name,
                    exc,
                )

            # ``select_model``/``list_models`` return a fallback string without
            # raising even when the key is revoked (they flip ``key_invalid`` on
            # a 401). Committing here would keep a dead provider "active" and
            # brick every LLM call until the user changed the primary by hand.
            # Skip it so the next configured provider gets a chance.
            if getattr(provider, "key_invalid", False):
                log.info(
                    "Provider %s key invalid (401); skipping to next provider.",
                    provider_name,
                )
                continue

            self.active_provider = provider
            self.active_provider_name = provider.name
            self.active_model = selected_model
            log.info("LLM provider active: %s (model=%s)", provider.name, selected_model)
            return

        self.active_provider = None
        self.active_provider_name = "none"
        self.active_model = "none"
        log.warning("No LLM provider available; chat/LLM features will fall back.")

    def metadata(self, force_refresh: bool = False) -> dict[str, Any]:
        """Aggregate provider availability + model lists. Cached for 60s.

        Skips ``list_models()`` on providers flagged ``key_invalid`` so a stale
        / revoked key (e.g. expired Cerebras free tier) doesn't trigger an HTTP
        401 on every health poll.
        """
        now = _time.time()
        if (
            not force_refresh
            and self._metadata_cache is not None
            and now - self._metadata_cache[0] < _METADATA_CACHE_TTL_SECONDS
        ):
            return self._metadata_cache[1]

        providers_metadata: dict[str, Any] = {}
        for name, provider in self.providers.items():
            try:
                available = provider.is_available()
            except Exception as exc:
                log.warning("Provider %s is_available error: %s", name, exc)
                available = False

            key_invalid = self._key_invalid_active(name, provider)
            models: list[str] = []
            # Skip the network call when we already know the key is bad — it
            # will only produce another 401 in the log.
            if available and not key_invalid:
                try:
                    models = provider.list_models()
                except Exception as exc:
                    log.info("Provider %s list_models error: %s", name, exc)
                    models = []
                # The provider may have flipped key_invalid during list_models.
                key_invalid = self._key_invalid_active(name, provider)

            providers_metadata[name] = {
                "available": available and not key_invalid,
                "models": models,
                "key_invalid": key_invalid,
            }

        result = {
            "active_provider": self.active_provider_name,
            "active_model": self.active_model,
            "available": self.active_provider is not None,
            "providers": providers_metadata,
        }
        self._metadata_cache = (now, result)
        return result

    def invalidate_caches(self) -> None:
        """Clear metadata + models caches and reset key_invalid flags.

        Call after the user saves provider keys so the next ``metadata()`` reflects
        the new configuration without waiting for the 60s TTL.
        """
        self._metadata_cache = None
        self._models_cache = {}
        self._key_invalid_since = {}
        for provider in self.providers.values():
            if hasattr(provider, "key_invalid"):
                provider.key_invalid = False

    def _key_invalid_active(self, name: str, provider: LLMProvider) -> bool:
        """True while a provider's key_invalid flag should still exclude it.

        Records when the flag was first seen; after
        ``_KEY_INVALID_COOLDOWN_SECONDS`` it clears the flag (one re-probe) so
        the next call/metadata tries the provider again. If the key is still
        bad, the live call 401s and it gets re-flagged — bounded, not permanent.
        """
        if not getattr(provider, "key_invalid", False):
            self._key_invalid_since.pop(name, None)
            return False
        now = _time.time()
        since = self._key_invalid_since.get(name)
        if since is None:
            self._key_invalid_since[name] = now
            return True
        if now - since >= _KEY_INVALID_COOLDOWN_SECONDS:
            provider.key_invalid = False
            self._key_invalid_since.pop(name, None)
            return False
        return True

    def _recent_429_models(self) -> set[str]:
        """Models seen persistently rate-limited within the cooldown window
        (stale entries pruned). Auto model-selection de-ranks these."""
        now = _time.time()
        fresh = {
            m for m, ts in self._model_429_at.items() if now - ts < _MODEL_429_COOLDOWN_SECONDS
        }
        self._model_429_at = {m: self._model_429_at[m] for m in fresh}
        return fresh

    def get_models(self, provider_name: str, force_refresh: bool = False) -> dict[str, Any]:
        """Return models + recommended for a single provider, cached for 5 min."""
        provider = self.providers.get(provider_name)
        if not provider:
            return {"models": [], "recommended": None, "cached": False, "fetched_at": 0.0}
        if not provider.is_available():
            return {"models": [], "recommended": None, "cached": False, "fetched_at": 0.0}

        now = _time.time()
        cached_entry = self._models_cache.get(provider_name)
        if (
            not force_refresh
            and cached_entry is not None
            and now - cached_entry[0] < _MODELS_CACHE_TTL_SECONDS
        ):
            models = cached_entry[1]
            cached = True
            fetched_at = cached_entry[0]
        else:
            try:
                models = provider.list_models()
            except Exception as exc:
                log.warning("Provider %s list_models() raised: %s", provider_name, exc)
                models = []
            self._models_cache[provider_name] = (now, models)
            cached = False
            fetched_at = now

        recommended = (
            choose_best_model(
                models=models,
                preferred_model=self.settings.preferred_model,
                policy=self.settings.model_selection_policy,
            )
            if models
            else None
        )
        return {
            "models": models,
            "recommended": recommended,
            "cached": cached,
            "fetched_at": fetched_at,
        }

    def _record_call(
        self,
        provider: LLMProvider,
        model: str,
        endpoint: str,
        success: bool,
        error_type: str | None = None,
    ) -> None:
        """Persist token usage to ``usage_log`` after a call. Best-effort.

        ``self._db`` is set externally by the AppContainer once the DB is open;
        when missing (unit tests), recording silently no-ops.
        """
        db = getattr(self, "_db", None)
        if db is None:
            return
        try:
            from app.services.usage_tracker import record_usage

            record_usage(
                db,
                provider=provider.name,
                model=model,
                endpoint=endpoint,
                last_usage=getattr(provider, "last_usage", None),
                success=success,
                error_type=error_type,
            )
        except Exception as exc:
            log.debug("usage record skipped: %s", exc)

    def _model_for(self, provider: LLMProvider) -> str:
        """A model to use on a failover provider (cheap, no commit to active_*)."""
        try:
            model = provider.select_model(preferred_model=self.settings.preferred_model)
        except Exception:
            return self.settings.preferred_model or self.active_model
        # If the picked model has been 429ing, re-pick among the provider's
        # models with it de-ranked (only when there's a real alternative).
        penalized = self._recent_429_models()
        if model in penalized:
            try:
                models = provider.list_models()
                if len(models) > 1:
                    model = choose_best_model(
                        models,
                        preferred_model=self.settings.preferred_model,
                        penalized=penalized,
                    )
            except Exception:
                pass
        return model

    def _failover_candidates(
        self, explicit_provider: str | None, explicit_model: str | None
    ) -> list[tuple[LLMProvider, str]]:
        """Ordered ``[(provider, model)]`` to attempt for one request.

        An explicit provider request is honored as-is (no failover). Otherwise
        the active provider goes first, then the remaining available,
        non-``key_invalid`` providers in ``llm_provider_order`` — so a
        rate-limited or down primary fails over to a working key instead of
        dropping straight to the canned fallback.
        """
        if explicit_provider:
            provider = self.providers.get(explicit_provider)
            if provider is None:
                return []
            return [(provider, explicit_model or self.active_model)]

        candidates: list[tuple[LLMProvider, str]] = []
        seen: set[str] = set()
        if self.active_provider is not None:
            candidates.append((self.active_provider, explicit_model or self.active_model))
            seen.add(self.active_provider.name)
        for name in self.settings.llm_provider_order:
            if name in seen:
                continue
            provider = self.providers.get(name)
            if provider is None:
                continue
            try:
                # Cooldown check first so it always runs (it may clear the flag
                # after the window); is_available then reflects the fresh state.
                usable = not self._key_invalid_active(name, provider) and provider.is_available()
            except Exception:
                usable = False
            if not usable:
                continue
            candidates.append((provider, self._model_for(provider)))
            seen.add(name)
        return candidates

    def _maybe_flag_key_invalid(self, provider: LLMProvider, exc: Exception) -> None:
        """Flag a provider whose key just 401'd during a live call.

        Previously only ``list_models`` set this, so a key that went bad after
        startup kept the provider "healthy" in the UI while every chat silently
        degraded. Flagging here makes the invalid-key warning surface and lets
        failover skip it next time.
        """
        if is_unauthorized(exc) and not getattr(provider, "key_invalid", False):
            provider.key_invalid = True
            self._key_invalid_since[provider.name] = _time.time()
            log.warning("Provider %s key marked invalid (401) during live call.", provider.name)

    def _run_with_failover(
        self,
        *,
        endpoint: str,
        explicit_provider: str | None,
        explicit_model: str | None,
        call: Callable[[LLMProvider, str], _RetryT],
    ) -> _RetryT:
        candidates = self._failover_candidates(explicit_provider, explicit_model)
        if not candidates:
            raise RuntimeError("No LLM provider available")
        last_exc: Exception | None = None
        for idx, (provider, model) in enumerate(candidates):
            try:
                result = _with_retry(
                    functools.partial(call, provider, model),
                    provider_label=provider.name,
                )
            except Exception as exc:
                last_exc = exc
                if _is_rate_limited(exc):
                    self._model_429_at[model] = _time.time()
                self._maybe_flag_key_invalid(provider, exc)
                self._record_call(provider, model, endpoint, False, type(exc).__name__)
                if idx < len(candidates) - 1:
                    log.warning(
                        "Provider %s failed (%s); failing over to next provider.",
                        provider.name,
                        exc.__class__.__name__,
                    )
                continue
            self._record_call(provider, model, endpoint, True)
            return result
        assert last_exc is not None
        raise last_exc

    def complete_json(
        self,
        prompt: str,
        max_tokens: int = 700,
        provider_name: str | None = None,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        return self._run_with_failover(
            endpoint="complete_json",
            explicit_provider=provider_name,
            explicit_model=model_name,
            call=lambda p, m: p.complete_json(prompt=prompt, model=m, max_tokens=max_tokens),
        )

    def chat(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 700,
        provider_name: str | None = None,
        model_name: str | None = None,
    ) -> str:
        return self._run_with_failover(
            endpoint="chat",
            explicit_provider=provider_name,
            explicit_model=model_name,
            call=lambda p, m: p.chat(messages=messages, model=m, max_tokens=max_tokens),
        )


# ─── Retry helper ──────────────────────────────────────────────

_RETRYABLE_MARKERS = (
    "429",
    "500",
    "502",
    "503",
    "504",
    "timeout",
    "connection reset",
    "rate limit",
    "too many requests",
    "queue_exceeded",
    "service unavailable",
    "temporarily unavailable",
)


def _is_retryable(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if isinstance(status, int) and status in (408, 409, 425, 429, 500, 502, 503, 504):
        return True
    text = str(exc).lower()
    return any(marker in text for marker in _RETRYABLE_MARKERS)


def _is_rate_limited(exc: Exception) -> bool:
    """True for a 429 specifically (not 5xx) — used to de-rank a model."""
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status == 429:
        return True
    text = str(exc).lower()
    return "429" in text or "rate limit" in text or "too many requests" in text


def _call_with_timeout(fn: Callable[[], _RetryT], timeout: float) -> _RetryT:
    """Run ``fn`` with a wall-clock timeout on a dedicated daemon thread.

    A per-call thread (rather than a bounded pool) means a hung provider call
    can't exhaust a fixed worker set and block other calls — the abandoned
    thread just lingers until the underlying call returns. On timeout, raises
    ``TimeoutError`` (which ``_is_retryable`` treats as retryable), so the
    caller — and any SSE stream — regains control immediately. ``timeout <= 0``
    disables the guard. Signal-based alarms aren't used (they don't work on
    Windows or in worker threads).
    """
    if timeout <= 0:
        return fn()
    box: list[_RetryT] = []
    err: list[Exception] = []
    done = threading.Event()

    def _runner() -> None:
        try:
            box.append(fn())
        except Exception as exc:
            err.append(exc)
        finally:
            done.set()

    threading.Thread(target=_runner, name="llm-call", daemon=True).start()
    if not done.wait(timeout):
        raise TimeoutError(f"LLM request exceeded timeout of {timeout:.0f}s")
    if err:
        raise err[0]
    return box[0]


def _with_retry(fn: Callable[[], _RetryT], provider_label: str) -> _RetryT:
    max_attempts = max(1, int(_os.environ.get("LLM_MAX_RETRIES", "3")))
    base = float(_os.environ.get("LLM_RETRY_BASE_SECONDS", "1.0"))
    timeout = float(_os.environ.get("LLM_REQUEST_TIMEOUT_SECONDS", "60"))
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return _call_with_timeout(fn, timeout)
        except Exception as exc:
            last_exc = exc
            if attempt >= max_attempts or not _is_retryable(exc):
                raise
            delay = base * (2 ** (attempt - 1))
            jitter = delay * 0.3 * (2 * _random.random() - 1)
            wait = max(0.1, delay + jitter)
            log.warning(
                "Provider %s attempt %d/%d failed (%s); retrying in %.2fs",
                provider_label,
                attempt,
                max_attempts,
                exc.__class__.__name__,
                wait,
            )
            _time.sleep(wait)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("retry loop exited unexpectedly")
