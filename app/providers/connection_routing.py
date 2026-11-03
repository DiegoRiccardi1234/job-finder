"""Explicit connection routes, separate from the legacy automatic API pool."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from app.providers.base import LLMProvider
from app.scoring_schema import ANSWERED_BY_KEY


def provider_for(manager: Any, connection_id: str, *, task: str = "diagnostics") -> LLMProvider:
    with manager._connection_provider_lock:
        return _provider_for(manager, connection_id, task)


def _provider_for(manager: Any, connection_id: str, task: str) -> LLMProvider:
    row = manager.connections.get(connection_id)
    if row is None:
        raise ValueError("unknown_connection")
    key = manager.connections.secret(connection_id)
    kind = row["kind"]
    # Native instances own cancellation state. Metadata/model edits must not
    # replace an active instance and make its process unreachable by Stop.
    signature = (
        kind
        if kind in ("codex", "claude")
        else json.dumps(row, sort_keys=True) + "\0" + (key or "")
    )
    cache_key = f"{task}::{connection_id}"
    cached = manager._connection_providers.get(cache_key)
    if cached and cached[0] == signature:
        return cached[1]  # type: ignore[no-any-return]
    if kind in ("codex", "claude"):
        from app.agent_runtime import create_runtime_provider

        provider = create_runtime_provider(kind, connection_id)
    else:
        from app.providers.openai_compat import CustomOpenAIProvider

        provider = CustomOpenAIProvider(key, row.get("base_url"))
        provider.name = connection_id
        if row.get("model"):
            provider.default_model = row["model"]
    manager._connection_providers[cache_key] = (signature, provider)
    return provider


def run_route(
    manager: Any,
    task: str,
    endpoint: str,
    call: Callable[[LLMProvider, str], Any],
    *,
    route_override: list[str] | None = None,
    model_override: str | None = None,
    policy_override: dict[str, Any] | None = None,
) -> Any:
    """Attempt each opted-in connection once; never enter the global API pool."""
    route = route_override if route_override is not None else manager.connections.route(task)

    def check_cancelled() -> None:
        if (
            manager._connection_cancellations.get(task)
            and manager._connection_cancellations[task].is_set()
        ):
            from app.agent_runtime import RuntimeConnectionError

            raise RuntimeConnectionError("cancelled")

    def checked_call(provider: LLMProvider, model: str) -> Any:
        check_cancelled()
        return call(provider, model)

    last_error: Exception = RuntimeError("No AI connection available")
    for connection_id in route:
        check_cancelled()
        if connection_id.startswith("api:"):
            # Explicit provider pins constrain failover to this one API account;
            # model ranking/health checks remain the existing implementation.
            try:
                if task == "scoring" and getattr(manager, "_db", None) is not None:
                    from app.services.quota import blocks_scan

                    if blocks_scan(manager._db):
                        raise RuntimeError("API daily request guard reached")
                return manager._run_with_failover(
                    endpoint=endpoint,
                    explicit_provider=connection_id[4:],
                    explicit_model=model_override,
                    call=checked_call,
                    policy_override=policy_override,
                )
            except Exception as exc:
                last_error = exc
                continue
        row = manager.connections.get(connection_id)
        if not row or not row.get("enabled"):
            last_error = RuntimeError("AI connection disabled")
            continue
        model = model_override or str(row.get("model") or "")
        started = time.monotonic()
        provider = None
        try:
            provider = provider_for(manager, connection_id, task=task)
            check_cancelled()
            if not model:
                model = provider.select_model()
            value = checked_call(provider, model)
            if value is None or value == "" or value == {}:
                raise ValueError("AI connection returned an empty response")
        except Exception as exc:
            if provider is not None:
                manager._record_call(
                    provider,
                    model,
                    endpoint,
                    False,
                    str(getattr(exc, "code", type(exc).__name__)),
                    int((time.monotonic() - started) * 1000),
                )
            if getattr(exc, "code", "") == "cancelled":
                raise
            last_error = exc
            continue
        manager._record_call(
            provider, model, endpoint, True, None, int((time.monotonic() - started) * 1000)
        )
        if isinstance(value, dict):
            value.setdefault(ANSWERED_BY_KEY, f"{connection_id}/{model}")
        return value
    raise last_error


def has_route(manager: Any, task: str) -> bool:
    return bool(manager.connections.route(task))


def task_kwargs(manager: Any, task: str) -> dict[str, Any]:
    """Keep lightweight provider-manager test doubles backward compatible."""
    method = getattr(manager, "has_connection_route", None)
    return {"task": task} if callable(method) and method(task) else {}
