"""In-process rate limiter (sliding window per IP + bucket).

Zero external dependencies. Thread-safe. Intended for localhost deployments
where the primary goal is to stop runaway scripts, not to defend against
distributed abuse. Set ``ENABLE_RATE_LIMIT=0`` to disable (e.g. during tests).
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque

from fastapi import HTTPException, Request

from app.log import get_logger

log = get_logger(__name__)


_ENABLED = os.environ.get("ENABLE_RATE_LIMIT", "1").lower() not in ("0", "false", "no")

_lock = threading.Lock()
_buckets: dict[tuple[str, str], deque[float]] = {}
# Periodic sweep of stale keys so ``_buckets`` doesn't grow unbounded (one deque
# per distinct (ip, bucket) forever). Guarded by ``_lock`` via ``check``.
_STALE_AFTER_SECONDS = 3600.0
_SWEEP_INTERVAL_SECONDS = 300.0
_last_sweep = 0.0


def _client_ip(request: Request) -> str:
    client = request.client
    return client.host if client else "unknown"


def _maybe_sweep(now: float) -> None:
    """Drop buckets whose most recent hit is long past. Caller holds ``_lock``."""
    global _last_sweep
    if now - _last_sweep < _SWEEP_INTERVAL_SECONDS:
        return
    _last_sweep = now
    stale = [k for k, q in _buckets.items() if not q or now - q[-1] > _STALE_AFTER_SECONDS]
    for k in stale:
        del _buckets[k]


def check(request: Request, bucket: str, limit: int, window_seconds: float = 60.0) -> None:
    """Raise HTTP 429 if the caller exceeded ``limit`` requests in the window."""
    if not _ENABLED:
        return
    ip = _client_ip(request)
    now = time.monotonic()
    key = (ip, bucket)
    with _lock:
        _maybe_sweep(now)
        queue = _buckets.setdefault(key, deque())
        while queue and now - queue[0] > window_seconds:
            queue.popleft()
        if len(queue) >= limit:
            retry_in = max(1, int(window_seconds - (now - queue[0])))
            log.info("Rate limit hit: ip=%s bucket=%s (%d/%ds)", ip, bucket, limit, window_seconds)
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded for '{bucket}'. Try again in {retry_in}s.",
                headers={"Retry-After": str(retry_in)},
            )
        queue.append(now)


def reset() -> None:
    with _lock:
        _buckets.clear()
