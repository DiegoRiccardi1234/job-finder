"""OpenRouter model health stats (uptime / latency / throughput) from the public
``/models/{slug}/endpoints`` metadata endpoint.

These are GLOBAL aggregates over all OpenRouter traffic and cost NO inference —
they don't count against the free 1000-requests/day cap — so we can rank models
by live health/speed and skip ones that are down *right now* WITHOUT probing
(which fires real inference requests and burns the shared free quota).

OpenRouter only: :func:`get_model_health` returns ``{}`` for any other provider
(or on network error), so callers transparently fall back to the
name-based ranking + the passive penalty map. Never raises.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from app.log import get_logger

if TYPE_CHECKING:
    from app.providers.base import LLMProvider

log = get_logger(__name__)

try:
    import requests
except Exception:  # pragma: no cover
    requests = None  # type: ignore[assignment]

# Match the five-minute signal used to make selection decisions.
_CACHE_TTL_SECONDS = 300.0
# A model counts as "down now" when its endpoint status is not OK, or its
# 5-minute uptime drops below this floor.
_UP5M_FLOOR = 50.0
_REQUEST_TIMEOUT = 8.0
_MAX_WORKERS = 8

# model_id -> (fetched_ts, stats | None). ``None`` caches a "no usable endpoint /
# fetch failed" outcome so a bad id isn't refetched every call within the TTL.
_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}


def _p50(val: Any) -> float | None:
    """Read scalar latency/throughput, tolerating historical percentile data."""
    if isinstance(val, dict):
        p = val.get("p50")
        return float(p) if isinstance(p, (int, float)) else None
    if isinstance(val, (int, float)):
        return float(val)
    return None


def _parse_endpoints(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Pick status OK then best five-minute uptime; distinguish dead from unknown."""
    data = payload.get("data")
    eps = data.get("endpoints") if isinstance(data, dict) else None
    if not isinstance(eps, list):
        return None
    if not eps:
        return {"status": -1, "endpoint_count": 0, "up5m": None}
    eps = [e for e in eps if isinstance(e, dict)]
    if not eps:
        return None
    up = [e for e in eps if e.get("status") == 0]
    best = max(up or eps, key=lambda e: e.get("uptime_last_5m") or 0)
    return {
        "endpoint_count": len(eps),
        "provider_name": best.get("provider_name"),
        "status": best.get("status"),
        "up5m": best.get("uptime_last_5m"),
        "up30m": best.get("uptime_last_30m"),
        "lat_ms": _p50(best.get("latency_last_30m")),
        "tput": _p50(best.get("throughput_last_30m")),
        "ctx": best.get("context_length"),
        "maxc": best.get("max_completion_tokens"),
    }


def _fetch_one(base_url: str, api_key: str, model_id: str) -> dict[str, Any] | None:
    if requests is None:
        return None
    url = f"{base_url.rstrip('/')}/models/{model_id}/endpoints"
    try:
        resp = requests.get(url, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
        return _parse_endpoints(payload) if isinstance(payload, dict) else None
    except Exception as exc:
        log.debug("model_stats fetch failed for %s: %s", model_id, type(exc).__name__)
        return None


def get_model_health(provider: LLMProvider, model_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Map ``{model_id: stats}`` for OpenRouter models from cached endpoint
    metadata. Non-OpenRouter, missing ``requests``, or a per-model fetch
    error simply yields no entry for that id (never raises). Bounded concurrency,
    Five-minute cache; the public endpoint requires no authentication.
    """
    if getattr(provider, "name", "") != "openrouter":
        return {}
    base_url = getattr(provider, "base_url", None) or "https://openrouter.ai/api/v1"
    if requests is None or not model_ids:
        return {}

    now = time.time()
    stale = [m for m in model_ids if now - _cache.get(m, (0.0, None))[0] >= _CACHE_TTL_SECONDS]
    if stale:
        workers = min(_MAX_WORKERS, len(stale))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            fetched = list(ex.map(lambda m: _fetch_one(base_url, "", m), stale))
        for m, stats in zip(stale, fetched, strict=True):
            _cache[m] = (now, stats)

    out: dict[str, dict[str, Any]] = {}
    for m in model_ids:
        entry = _cache.get(m)
        if entry and entry[1] is not None:
            out[m] = entry[1]
    return out


def unhealthy_ids(health: dict[str, dict[str, Any]]) -> set[str]:
    """Model ids that are down/degraded right now: endpoint status not OK, or
    5-minute uptime below the floor. Missing signals are treated as healthy
    (we never sink a model on absent data)."""
    bad: set[str] = set()
    for mid, s in health.items():
        status = s.get("status")
        up5m = s.get("up5m")
        down_status = status is not None and status != 0
        low_uptime = isinstance(up5m, (int, float)) and up5m < _UP5M_FLOOR
        if down_status or low_uptime:
            bad.add(mid)
    return bad


def rank_healthy_models(models: list[str], health: dict[str, dict[str, Any]]) -> list[str]:
    """Exclude known dead endpoints and rank uptime in 2% bands, keeping pool ties."""
    bad = unhealthy_ids(health)
    survivors = [m for m in models if m not in bad]

    def bucket(mid: str) -> int:
        signal = health.get(mid, {})
        uptime = signal.get("up5m")
        if signal.get("status") == 0 and isinstance(uptime, (int, float)):
            return int(float(uptime) // 2)
        return -1

    return sorted(survivors, key=bucket, reverse=True) if health else survivors
