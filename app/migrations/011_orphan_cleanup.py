"""One-off cleanup of orphaned per-job child rows.

``delete_job``/``delete_all_jobs`` used to remove only the ``jobs`` row: the
declared FK cascades were inert (``PRAGMA foreign_keys`` is never enabled, and
``job_actions`` has no ``ON DELETE`` at all), so every historic delete left
rows behind in ``job_actions``/``recruiters``/``pinned_jobs``. The delete
methods now clear children explicitly; this migration sweeps the orphans that
already accumulated. Pure DELETEs of unreachable rows — idempotent, no schema
change.
"""

from __future__ import annotations

import sqlite3

VERSION = 11
DESCRIPTION = "delete orphaned job_actions/recruiters/pinned_jobs rows"


def upgrade(conn: sqlite3.Connection) -> None:
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for tbl in ("job_actions", "recruiters", "pinned_jobs"):
        if tbl in tables:
            conn.execute(f"DELETE FROM {tbl} WHERE job_id NOT IN (SELECT id FROM jobs)")
