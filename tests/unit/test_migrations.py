"""Unit tests for lightweight schema migrations."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.db import Database
from app.migrations import _discover, apply_migrations


def _schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version").fetchone()
    return int(row[0] if row else 0)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def test_migrations_discovered_and_ordered() -> None:
    items = _discover()
    assert len(items) >= 1
    versions = [m.version for m in items]
    assert versions == sorted(versions)
    assert versions == sorted(set(versions)), "duplicate versions detected"


def test_fresh_db_gets_all_migrations_applied(tmp_path: Path) -> None:
    db_path = tmp_path / "fresh.db"
    db = Database(db_path)
    try:
        conn = db.conn
        latest = _discover()[-1].version
        assert _schema_version(conn) == latest
        # Core tables present
        for tbl in (
            "jobs",
            "scan_runs",
            "candidate_profiles",
            "chat_messages",
            "preferences",
            "job_actions",
        ):
            assert _table_exists(conn, tbl), f"missing table {tbl}"
    finally:
        db.close()


def test_baseline_detection_for_preexisting_db(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    # Simulate a pre-migration DB: jobs table exists, no schema_version table.
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE jobs (id INTEGER PRIMARY KEY, job_hash TEXT UNIQUE,
            titolo TEXT, azienda TEXT, first_seen_at TEXT, last_seen_at TEXT, updated_at TEXT);
        """
    )
    conn.commit()
    conn.close()

    db = Database(db_path)
    try:
        latest = _discover()[-1].version
        assert _schema_version(db.conn) == latest
    finally:
        db.close()


def test_apply_migrations_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "idem.db"
    db = Database(db_path)
    try:
        first = _schema_version(db.conn)
        apply_migrations(db.conn)
        apply_migrations(db.conn)
        second = _schema_version(db.conn)
        assert first == second
    finally:
        db.close()


def test_migration_003_adds_content_hash_column_and_index(tmp_path: Path) -> None:
    db_path = tmp_path / "hash.db"
    db = Database(db_path)
    try:
        cols = {
            row[1] for row in db.conn.execute("PRAGMA table_info(candidate_profiles)").fetchall()
        }
        assert "content_hash" in cols
        idx_rows = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='candidate_profiles'"
        ).fetchall()
        idx_names = {r[0] for r in idx_rows}
        assert "idx_candidate_profiles_hash" in idx_names
    finally:
        db.close()


def test_migration_011_cleans_orphaned_child_rows(tmp_path: Path) -> None:
    """Historic deletes left rows in job_actions/recruiters/pinned_jobs whose
    job no longer exists (FK cascade was never enforced). Migration 011 must
    remove exactly the orphans and keep rows attached to live jobs."""
    db_path = tmp_path / "orphans.db"
    db = Database(db_path)
    try:
        job_id, _new, _hash = db.upsert_job(
            {"titolo": "QA", "azienda": "Acme", "link": "https://example.com/live"}
        )
        db.set_job_action(job_id, "applied", "keep me")
        db.upsert_recruiter(job_id, {"name": "R"})
        db.pin_job("default", job_id)
        # Synthesize orphans as an old delete_job would have left them.
        ghost = job_id + 999
        db.conn.execute(
            "INSERT INTO job_actions(job_id, action, notes, created_at) VALUES (?, 'applied', '', '2024-01-01')",
            (ghost,),
        )
        db.conn.execute(
            "INSERT INTO recruiters(job_id, name, title, headline, profile_url, raw_text, fetched_at) "
            "VALUES (?, 'ghost', '', '', '', '', '2024-01-01')",
            (ghost,),
        )
        db.conn.execute(
            "INSERT INTO pinned_jobs(session_id, job_id, pinned_at) VALUES ('default', ?, '2024-01-01')",
            (ghost,),
        )
        # Rewind the tracker to before 011 so the cleanup migration re-runs
        # against this DB (fresh DBs are already at the latest version).
        db.conn.execute("DELETE FROM schema_version WHERE version >= 11")
        db.conn.commit()

        apply_migrations(db.conn)

        for tbl in ("job_actions", "recruiters", "pinned_jobs"):
            ghosts = db.conn.execute(
                f"SELECT COUNT(*) FROM {tbl} WHERE job_id = ?", (ghost,)
            ).fetchone()[0]
            kept = db.conn.execute(
                f"SELECT COUNT(*) FROM {tbl} WHERE job_id = ?", (job_id,)
            ).fetchone()[0]
            assert ghosts == 0, f"orphans left in {tbl}"
            assert kept == 1, f"live rows lost from {tbl}"
    finally:
        db.close()


def test_find_candidate_profile_by_hash_dedup(tmp_path: Path) -> None:
    db_path = tmp_path / "dedup.db"
    db = Database(db_path)
    try:
        first_id = db.save_candidate_profile(
            source_name="cv.txt",
            markdown="dummy",
            summary={"skills": ["python"]},
            content_hash="abc123",
        )
        assert db.find_candidate_profile_by_hash("abc123") == first_id
        assert db.find_candidate_profile_by_hash("not-found") is None
    finally:
        db.close()
