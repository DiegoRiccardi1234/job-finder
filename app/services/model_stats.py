"""OpenRouter model health stats (uptime / latency / throughput) from the public
``/models/{slug}/endpoints`` metadata endpoint.

These are GLOBAL aggregates over all OpenRouter traffic and cost NO inference —
they don't count against the free 1000-requests/day cap — so we can rank models
by live health/speed and skip ones that are down *right now* WITHOUT probing
(which fires real inference requests and burns the shared free quota).

OpenRouter only: :func:`get_model_health` returns ``{}`` for any other provider
(or on missing key / network error), so callers transparently fall back to the
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

# Health changes slowly and scans are bursty, so a long TTL keeps a whole scan
# on cache hits — the cold fetch runs at most once per 30 min per catalog.
_CACHE_TTL_SECONDS = 1800.0
# A model counts as "down now" when its endpoint status is not OK, or its
# 5-minute uptime drops below this floor.
_UP5M_FLOOR = 50.0
_REQUEST_TIMEOUT = 8.0
_MAX_WORKERS = 8

# model_id -> (fetched_ts, stats | None). ``None`` caches a "no usable endpoint /
# fetch failed" outcome so a bad id isn't refetched every call within the TTL.
_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}


def _p50(val: Any) -> float | None:
    """OpenRouter reports latency/throughput as a percentile dict; take p50.
    Tolerates a bare number too."""
    if isinstance(val, dict):
        p = val.get("p50")
        return float(p) if isinstance(p, (int, float)) else None
    if isinstance(val, (int, float)):
        return float(val)
    return None


def _parse_endpoints(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Pick the healthiest endpoint (status OK, best 30-min uptime) and extract
    its live stats. ``None`` when no endpoint is present."""
    data = payload.get("data")
    eps = (data.get("endpoints") if isinstance(data, dict) else None) or []
    if not eps:
        return None
    up = [e for e in eps if e.get("status") == 0]
    best = max(up or eps, key=lambda e: e.get("uptime_last_30m") or 0)
    return {
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
    slug = model_id.split(":")[0]  # strip :free / other variant suffix
    url = f"{base_url.rstrip('/')}/models/{slug}/endpoints"
    try:
        resp = requests.get(
            url, headers={"Authorization": f"Bearer {api_key}"}, timeout=_REQUEST_TIMEOUT
        )
        resp.raise_for_status()
        payload = resp.json()
        return _parse_endpoints(payload) if isinstance(payload, dict) else None
    except Exception as exc:
        log.debug("model_stats fetch failed for %s: %s", model_id, exc)
        return None


def get_model_health(provider: LLMProvider, model_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Map ``{model_id: stats}`` for OpenRouter models from cached endpoint
    metadata. Non-OpenRouter, missing key / ``requests``, or a per-model fetch
    error simply yields no entry for that id (never raises). Bounded concurrency,
    30-minute cache.
    """
    if getattr(provider, "name", "") != "openrouter":
        return {}
    api_key = getattr(provider, "api_key", None)
    base_url = getattr(provider, "base_url", None)
    if not api_key or not base_url or requests is None or not model_ids:
        return {}

    now = time.time()
    stale = [m for m in model_ids if now - _cache.get(m, (0.0, None))[0] >= _CACHE_TTL_SECONDS]
    if stale:
        workers = min(_MAX_WORKERS, len(stale))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            fetched = list(ex.map(lambda m: _fetch_one(base_url, api_key, m), stale))
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
