"""Add the ``saved_searches`` table (F7).

Named scan presets: a snapshot of the Job Search filters (terms, location,
sites, experience/job/work types, min salary) stored as JSON so the user can
re-run a search with one click.
"""

from __future__ import annotations

import sqlite3

VERSION = 8
DESCRIPTION = "saved_searches table (named scan presets)"


def upgrade(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS saved_searches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            config_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
