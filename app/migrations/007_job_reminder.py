"""Add manual follow-up reminder fields to ``jobs`` (F4).

``reminder_at`` (date / ISO datetime) + ``reminder_note`` let the user set an
explicit deadline or follow-up date on a job. The automatic "stale application"
nudge is derived from ``job_actions`` at query time and needs no column.
"""

from __future__ import annotations

import sqlite3

VERSION = 7
DESCRIPTION = "jobs.reminder_at + jobs.reminder_note (application reminders)"


def upgrade(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    if "reminder_at" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN reminder_at TEXT")
    if "reminder_note" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN reminder_note TEXT")
