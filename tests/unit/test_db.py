from pathlib import Path

from app.db import Database, make_job_hash


def test_make_job_hash_is_deterministic_and_case_insensitive() -> None:
    a = make_job_hash("  Data Analyst ", "Acme ", "https://example.com/x")
    b = make_job_hash("data analyst", "acme", "HTTPS://EXAMPLE.COM/X")
    assert a == b
    assert len(a) == 64


def test_upsert_and_get_job(tmp_path: Path) -> None:
    db = Database(tmp_path / "s.db")
    try:
        payload = {
            "titolo": "QA Tester",
            "azienda": "Acme",
            "descrizione": "Test role",
            "sede": "Remote",
            "fonte": "linkedin",
            "link": "https://example.com/job/1",
            "ricerca_usata": "QA Tester",
            "modalita": "Full Remote IT",
        }
        job_id, is_new, status = db.upsert_job(payload)
        assert is_new
        assert status == "open"

        fetched = db.get_job(job_id)
        assert fetched["titolo"] == "QA Tester"

        job_id_2, is_new_2, _ = db.upsert_job(payload)
        assert job_id_2 == job_id
        assert not is_new_2
    finally:
        db.close()


def test_preferences_round_trip(tmp_path: Path) -> None:
    db = Database(tmp_path / "s.db")
    try:
        db.set_preference("remote_mode", "full_remote")
        assert db.get_preference("remote_mode", "") == "full_remote"
        assert db.get_preference("missing", "default") == "default"
    finally:
        db.close()


def test_wal_mode_is_enabled(tmp_path: Path) -> None:
    db = Database(tmp_path / "s.db")
    try:
        mode = db.conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
    finally:
        db.close()
