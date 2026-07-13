"""Persistent log of LLM token consumption.

Inserted by :class:`app.providers.factory.ProviderManager` after every chat /
complete_text / complete_json call (success or failure). The frontend reads
aggregated stats via ``GET /api/usage/stats``.

No pricing in v1.1.0 — only raw token counts. The optional ``endpoint`` column
lets us segment spend by feature (chat coach vs. CV summary vs. job analysis)
without forcing every call site to attribute itself.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from app.log import get_logger

log = get_logger(__name__)


def record_usage(
    db: Any,
    *,
    provider: str,
    model: str,
    endpoint: str | None,
    last_usage: dict[str, Any] | None,
    success: bool = True,
    error_type: str | None = None,
    duration_ms: int | None = None,
) -> None:
    """Insert one row into ``usage_log``. Silently skipped when ``last_usage`` is empty.

    ``last_usage`` is the dict the provider populated on its instance after the
    SDK call. Schema:
        {"prompt_tokens": int, "completion_tokens": int, "total_tokens": int}

    None / empty payload still records the call (with zeros) so failure rates
    are visible in the UI.
    """
    prompt_tokens = int((last_usage or {}).get("prompt_tokens") or 0)
    completion_tokens = int((last_usage or {}).get("completion_tokens") or 0)
    total_tokens = int(
        (last_usage or {}).get("total_tokens") or (prompt_tokens + completion_tokens)
    )
    try:
        conn = db._get_connection() if hasattr(db, "_get_connection") else db.conn
        conn.execute(
            """
            INSERT INTO usage_log
                (ts, provider, model, endpoint, prompt_tokens, completion_tokens,
                 total_tokens, success, error_type, duration_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(UTC).isoformat(timespec="seconds"),
                provider,
                model,
                endpoint or "",
                prompt_tokens,
                completion_tokens,
                total_tokens,
                1 if success else 0,
                error_type,
                duration_ms,
            ),
        )
        conn.commit()
    except sqlite3.DatabaseError as exc:
        # Tracking failures must never break the user-facing feature.
        log.warning("usage_log insert failed: %s", exc)


def _range_floor(range_: str) -> str:
    """Return ISO timestamp for the start of the requested window."""
    now = datetime.now(UTC)
    if range_ == "today":
        floor = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif range_ == "week":
        floor = now - timedelta(days=7)
    elif range_ == "month":
        floor = now - timedelta(days=30)
    else:
        # ``all`` — pick a year-2000 epoch that's safely older than any row.
        floor = datetime(2000, 1, 1, tzinfo=UTC)
    return floor.isoformat(timespec="seconds")


def aggregate_stats(db: Any, range_: str = "today") -> dict[str, Any]:
    """Return totals + per-provider + per-day breakdown for the given window.

    Window choices: ``today`` (since 00:00 UTC), ``week`` (last 7 days),
    ``month`` (last 30 days), ``all`` (no lower bound).
    """
    floor = _range_floor(range_)
    conn = db._get_connection() if hasattr(db, "_get_connection") else db.conn

    totals_row = conn.execute(
        """
        SELECT COUNT(*) AS calls,
               COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
               COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
               COALESCE(SUM(total_tokens), 0) AS total_tokens,
               COALESCE(SUM(success), 0) AS successes
        FROM usage_log
        WHERE ts >= ?
        """,
        (floor,),
    ).fetchone()

    by_provider_rows = conn.execute(
        """
        SELECT provider,
               COUNT(*) AS calls,
               COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
               COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
               COALESCE(SUM(total_tokens), 0) AS total_tokens
        FROM usage_log
        WHERE ts >= ?
        GROUP BY provider
        ORDER BY total_tokens DESC
        """,
        (floor,),
    ).fetchall()

    by_day_rows = conn.execute(
        """
        SELECT substr(ts, 1, 10) AS day,
               COUNT(*) AS calls,
               COALESCE(SUM(total_tokens), 0) AS total_tokens
        FROM usage_log
        WHERE ts >= ?
        GROUP BY day
        ORDER BY day ASC
        """,
        (floor,),
    ).fetchall()

    return {
        "range": range_,
        "since": floor,
        "total_calls": totals_row["calls"] if totals_row else 0,
        "total_tokens": totals_row["total_tokens"] if totals_row else 0,
        "prompt_tokens": totals_row["prompt_tokens"] if totals_row else 0,
        "completion_tokens": totals_row["completion_tokens"] if totals_row else 0,
        "successes": totals_row["successes"] if totals_row else 0,
        "by_provider": [dict(row) for row in by_provider_rows],
        "by_day": [dict(row) for row in by_day_rows],
    }
