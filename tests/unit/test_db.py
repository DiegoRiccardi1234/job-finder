import threading
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


def test_nested_write_does_not_deadlock(tmp_path: Path) -> None:
    # ``add_manual_job`` acquires the write lock and then calls ``upsert_job``
    # which acquires it again — only an RLock survives this without hanging.
    db = Database(tmp_path / "s.db")
    try:
        job_id = db.add_manual_job(
            {"titolo": "Nested", "azienda": "Acme", "link": "https://example.com/n"}
        )
        assert job_id > 0
        assert db.get_job(job_id) is not None
    finally:
        db.close()


def test_concurrent_writes_do_not_lose_jobs(tmp_path: Path) -> None:
    # Many threads upserting distinct jobs through the shared connection must
    # all land without races or "database is locked" errors.
    db = Database(tmp_path / "s.db")
    n = 40
    errors: list[Exception] = []

    def worker(i: int) -> None:
        try:
            db.upsert_job(
                {
                    "titolo": f"Role {i}",
                    "azienda": "Acme",
                    "link": f"https://example.com/job/{i}",
                }
            )
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    try:
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(db.list_jobs(limit=1000)) == n
    finally:
        db.close()


def test_save_job_analysis_field_merges_and_guards(tmp_path: Path) -> None:
    db = Database(tmp_path / "s.db")
    try:
        job_id, _, _ = db.upsert_job(
            {"titolo": "T", "azienda": "A", "link": "https://example.com/j"}
        )
        # No analysis row yet -> guarded, returns False.
        assert db.save_job_analysis_field(job_id, "interview_prep", "x") is False

        db.update_job_analysis(job_id, {"punteggio": 7})
        assert db.save_job_analysis_field(job_id, "interview_prep", "Q1") is True
        assert db.save_job_analysis_field(job_id, "tailored_resume", "CV") is True

        job = db.get_job_with_analysis(job_id)
        assert job is not None
        assert job["analysis"]["interview_prep"] == "Q1"
        assert job["analysis"]["tailored_resume"] == "CV"
        assert job["analysis"]["punteggio"] == 7  # original field preserved
    finally:
        db.close()


def test_concurrent_upsert_same_hash_is_idempotent(tmp_path: Path) -> None:
    # Racing on the same job hash must collapse to a single row.
    db = Database(tmp_path / "s.db")
    payload = {"titolo": "QA", "azienda": "Acme", "link": "https://example.com/x"}

    def worker() -> None:
        db.upsert_job(dict(payload))

    try:
        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(db.list_jobs(limit=1000)) == 1
    finally:
        db.close()
