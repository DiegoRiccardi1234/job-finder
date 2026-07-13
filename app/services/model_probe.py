"""Active model probe: benchmark candidate models with a tiny JSON prompt to
learn which ones actually respond fast and return valid JSON.

The name heuristic in :mod:`app.providers.model_selector` can't see runtime
truth — some free models 200 with empty content, some are credit-gated (403),
some never emit JSON. This probes them empirically. Used by the Settings
"test models" action to rank models and to seed the factory's penalty map.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.providers.base import LLMProvider

# Minimal, unambiguous JSON ask — a healthy chat/instruct model answers in well
# under a second of tokens; reasoning-only models tend to return empty content.
PROBE_PROMPT = 'Rispondi SOLO con JSON valido e nulla altro: {"ok": true, "n": 7}'


def _probe_one(provider: LLMProvider, model: str) -> dict[str, Any]:
    t0 = time.monotonic()
    try:
        result = provider.complete_json(prompt=PROBE_PROMPT, model=model, max_tokens=120)
        latency_ms = int((time.monotonic() - t0) * 1000)
        json_ok = isinstance(result, dict) and bool(result)
        return {
            "model": model,
            "ok": True,
            "latency_ms": latency_ms,
            "json_ok": json_ok,
            "empty": not result,
            "error": None,
        }
    except Exception as exc:
        latency_ms = int((time.monotonic() - t0) * 1000)
        return {
            "model": model,
            "ok": False,
            "latency_ms": latency_ms,
            "json_ok": False,
            "empty": False,
            "error": str(exc)[:160],
        }


def probe_models(
    provider: LLMProvider,
    model_ids: list[str],
    *,
    timeout: float = 25.0,
    concurrency: int = 6,
) -> list[dict[str, Any]]:
    """Probe each model once, concurrently. Returns results ranked best-first
    (valid JSON, then any success, then fastest). Never raises — a hung/slow
    model becomes an ``ok: False`` row with ``error: "timeout"``.
    """
    if not model_ids:
        return []
    results: list[dict[str, Any]] = []
    workers = min(max(1, concurrency), len(model_ids))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_probe_one, provider, m): m for m in model_ids}
        for fut, model in futures.items():
            try:
                results.append(fut.result(timeout=timeout))
            except FutureTimeout:
                results.append(
                    {
                        "model": model,
                        "ok": False,
                        "latency_ms": int(timeout * 1000),
                        "json_ok": False,
                        "empty": False,
                        "error": "timeout",
                    }
                )
            except Exception as exc:  # pragma: no cover - defensive
                results.append(
                    {
                        "model": model,
                        "ok": False,
                        "latency_ms": 0,
                        "json_ok": False,
                        "empty": False,
                        "error": str(exc)[:160],
                    }
                )
    results.sort(key=lambda r: (not r["json_ok"], not r["ok"], r["latency_ms"]))
    return results


def penalty_reason(result: dict[str, Any]) -> str | None:
    """Map a probe result to a factory penalty reason, or None if the model is
    healthy (probe succeeded with valid JSON)."""
    if result.get("json_ok"):
        return None
    if result.get("empty"):
        return "empty"
    err = (result.get("error") or "").lower()
    if "403" in err or "forbidden" in err or "key limit" in err:
        return "forbidden"
    if "429" in err or "rate limit" in err or "too many requests" in err:
        return "rate_limit"
    return "json_fail"
