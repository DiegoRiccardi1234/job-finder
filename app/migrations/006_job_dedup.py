"""Add cross-source dedup key + alternate-sources list to ``jobs``.

``make_job_hash`` includes the posting ``link``, so the same role scraped from
LinkedIn and from Indeed lands as two rows (different URLs → different hash).
``dedup_key`` hashes title+company+location only, so ``upsert_job`` can detect
"same role, different source" and merge instead of inserting a duplicate.
``sources_json`` records every ``{fonte, link}`` the role was seen at, so the
UI can show an "also on: LinkedIn, Indeed" badge.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3

VERSION = 6
DESCRIPTION = "jobs.dedup_key + jobs.sources_json (cross-source dedup)"


def _dedup_key(titolo: str, azienda: str, sede: str) -> str:
    raw = f"{titolo.strip().lower()}|{azienda.strip().lower()}|{sede.strip().lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def upgrade(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    if "dedup_key" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN dedup_key TEXT")
    if "sources_json" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN sources_json TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_dedup_key ON jobs(dedup_key)")

    # Backfill existing rows: compute dedup_key and seed sources_json with the
    # row's own (fonte, link). Reference sede/fonte/link defensively — a very old
    # baselined table may predate one of them ('' stands in when absent).
    cols_now = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}

    def _col(name: str) -> str:
        return name if name in cols_now else "''"

    rows = conn.execute(
        f"SELECT id, COALESCE(titolo, ''), COALESCE(azienda, ''), "
        f"COALESCE({_col('sede')}, ''), COALESCE({_col('fonte')}, ''), "
        f"COALESCE({_col('link')}, '') FROM jobs "
        f"WHERE dedup_key IS NULL OR sources_json IS NULL"
    ).fetchall()
    for row in rows:
        job_id, titolo, azienda, sede, fonte, link = row
        key = _dedup_key(titolo or "", azienda or "", sede or "")
        sources = json.dumps([{"fonte": fonte or "", "link": link or ""}], ensure_ascii=False)
        conn.execute(
            "UPDATE jobs SET dedup_key = ?, sources_json = ? WHERE id = ?",
            (key, sources, job_id),
        )
