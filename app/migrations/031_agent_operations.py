"""Durable local agent operations, analysis tickets and human approvals."""

from __future__ import annotations

import sqlite3

VERSION = 31
DESCRIPTION = "Local MCP operations and reviewable agent proposals"


def upgrade(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS agent_operations ("
        "id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL, "
        "request_key TEXT UNIQUE, fingerprint TEXT NOT NULL, payload_json TEXT NOT NULL, "
        "result_json TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS agent_operations_status ON agent_operations(status, kind)"
    )
