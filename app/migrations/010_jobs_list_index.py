"""Composite index for the hot job-list query.

``list_jobs`` filters on status/score and always orders by
``punteggio_ai DESC, last_seen_at DESC``. Only ``job_hash`` (unique) and
``dedup_key`` were indexed before, so the list did a full scan + sort. Cheap at
current scale but a trivial, correct polish.
"""

from __future__ import annotations

import sqlite3

VERSION = 10
DESCRIPTION = "composite index on jobs(status, punteggio_ai, last_seen_at)"


def upgrade(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    # Guard for baseline DBs that predate these columns (added by earlier,
    # baselined migrations) — nothing to index there.
    if {"status", "punteggio_ai", "last_seen_at"} <= cols:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_list "
            "ON jobs(status, punteggio_ai DESC, last_seen_at DESC)"
        )
