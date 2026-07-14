"""Add ``duration_ms`` to ``usage_log`` for latency-aware model selection.

Records wall-clock time of each LLM call so auto-selection can learn which
models are actually fast (the name heuristic can't know this), and so the AI
usage panel can surface latency. Nullable — old rows keep NULL.
"""

from __future__ import annotations

import sqlite3

VERSION = 9
DESCRIPTION = "usage_log.duration_ms for latency tracking"


def upgrade(conn: sqlite3.Connection) -> None:
    table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='usage_log'"
    ).fetchone()
    if not table:
        return  # baseline DBs that predate usage_log (migration 004) — nothing to alter
    cols = {row[1] for row in conn.execute("PRAGMA table_info(usage_log)")}
    if "duration_ms" not in cols:
        conn.execute("ALTER TABLE usage_log ADD COLUMN duration_ms INTEGER")
