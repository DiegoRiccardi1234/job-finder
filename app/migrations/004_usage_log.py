"""Add ``usage_log`` table to track LLM token consumption per call.

No cost tracking in v1.1.0 — only raw token counts (prompt / completion / total)
plus provider, model and endpoint label so the user can answer "how much did I
use today?". Aggregations live in :mod:`app.services.usage_tracker`.
"""

from __future__ import annotations

import sqlite3

VERSION = 4
DESCRIPTION = "usage_log table for token tracking"


def upgrade(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS usage_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            endpoint TEXT,
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            success INTEGER NOT NULL DEFAULT 1,
            error_type TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_log_ts ON usage_log(ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_log_provider ON usage_log(provider)")
