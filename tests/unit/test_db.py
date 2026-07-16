import threading
from pathlib import Path

from app.db import Database, make_dedup_key, make_job_hash


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


def test_list_jobs_remote_only_filters_by_modalita(tmp_path: Path) -> None:
    """remote_only keeps jobs whose free-text modalita mentions 'remot'."""
    db = Database(tmp_path / "s.db")
    try:
        db.upsert_job(
            {
                "titolo": "Dev",
                "azienda": "Acme",
                "link": "https://ex.com/r",
                "modalita": "Full Remote",
            }
        )
        db.upsert_job(
            {"titolo": "Dev", "azienda": "Beta", "link": "https://ex.com/o", "modalita": "In sede"}
        )

        assert len(db.list_jobs(limit=100)) == 2
        remote = db.list_jobs(remote_only=True, limit=100)
        assert len(remote) == 1
        assert remote[0]["azienda"] == "Acme"
    finally:
        db.close()


def test_job_actions_timeline_and_note_keeps_status(tmp_path: Path) -> None:
    """A 'note' action records a timeline entry WITHOUT changing the job status."""
    db = Database(tmp_path / "s.db")
    try:
        jid, _, _ = db.upsert_job({"titolo": "QA", "azienda": "Acme", "link": "https://ex/1"})
        db.set_job_action(jid, "applied", "sent CV")
        db.set_job_action(jid, "note", "recruiter replied")

        assert db.get_job(jid)["status"] == "applied"  # note must NOT reset status
        actions = db.list_job_actions(jid)
        assert [a["action"] for a in actions] == ["applied", "note"]
        assert actions[0]["notes"] == "sent CV"
        assert actions[1]["notes"] == "recruiter replied"
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


def test_dedup_key_modes() -> None:
    # city mode: region/country spelling differences collapse to the city
    assert make_dedup_key("Dev", "Acme", "Milano, Lombardia, Italia", "city") == make_dedup_key(
        "Dev", "Acme", "Milano, Italy", "city"
    )
    # exact mode keeps those distinct
    assert make_dedup_key("Dev", "Acme", "Milano, Lombardia", "exact") != make_dedup_key(
        "Dev", "Acme", "Milano, Italy", "exact"
    )
    # title_company mode ignores location entirely
    assert make_dedup_key("Dev", "Acme", "Milano", "title_company") == make_dedup_key(
        "Dev", "Acme", "Roma", "title_company"
    )


def test_upsert_honors_dedup_mode_pref(tmp_path: Path) -> None:
    a = {
        "titolo": "Dev",
        "azienda": "Acme",
        "sede": "Milano, Lombardia, Italia",
        "link": "https://a/1",
    }
    b = {"titolo": "Dev", "azienda": "Acme", "sede": "Milano, Italy", "link": "https://a/2"}

    db = Database(tmp_path / "city.db")
    try:
        db.set_preference("dedup_mode", "city")
        db.upsert_job(dict(a))
        db.upsert_job(dict(b))
        assert len(db.list_jobs(limit=100)) == 1  # city normalization merges
    finally:
        db.close()

    db = Database(tmp_path / "exact.db")
    try:
        db.set_preference("dedup_mode", "exact")
        db.upsert_job(dict(a))
        db.upsert_job(dict(b))
        assert len(db.list_jobs(limit=100)) == 2  # different spellings stay separate
    finally:
        db.close()


def test_cross_source_dedup_merges_same_role(tmp_path: Path) -> None:
    """Same title+company+location from two sources (different URLs) collapses
    to one row that records both sources."""
    db = Database(tmp_path / "s.db")
    try:
        jid, is_new, _ = db.upsert_job(
            {
                "titolo": "Backend Dev",
                "azienda": "Acme",
                "sede": "Milano, Italy",
                "fonte": "linkedin",
                "link": "https://linkedin.com/jobs/1",
            }
        )
        assert is_new
        jid2, is_new2, _ = db.upsert_job(
            {
                "titolo": "Backend Dev",
                "azienda": "Acme",
                "sede": "Milano, Italy",
                "fonte": "indeed",
                "link": "https://indeed.com/viewjob?jk=2",
            }
        )
        assert jid2 == jid  # merged, not a new row
        assert not is_new2

        jobs = db.list_jobs(limit=100)
        assert len(jobs) == 1
        sources = jobs[0]["sources"]
        assert {s["fonte"] for s in sources} == {"linkedin", "indeed"}
        assert len(sources) == 2
    finally:
        db.close()


def test_cross_source_dedup_keeps_distinct_locations_separate(tmp_path: Path) -> None:
    """Same title+company but different city = two distinct openings, no merge."""
    db = Database(tmp_path / "s.db")
    try:
        db.upsert_job(
            {"titolo": "Sales", "azienda": "Acme", "sede": "Milano", "link": "https://a/1"}
        )
        db.upsert_job({"titolo": "Sales", "azienda": "Acme", "sede": "Roma", "link": "https://a/2"})
        assert len(db.list_jobs(limit=100)) == 2
    finally:
        db.close()


def test_reminders_manual_set_clear_and_overdue(tmp_path: Path) -> None:
    db = Database(tmp_path / "s.db")
    try:
        jid, _, _ = db.upsert_job({"titolo": "QA", "azienda": "Acme", "link": "https://ex/1"})
        db.set_job_reminder(jid, "2000-01-01", "call HR")  # past date → overdue
        out = db.list_reminders()
        rem = [r for r in out["reminders"] if r["job_id"] == jid]
        assert len(rem) == 1
        assert rem[0]["overdue"] is True
        assert rem[0]["note"] == "call HR"

        db.clear_job_reminder(jid)
        assert all(r["job_id"] != jid for r in db.list_reminders()["reminders"])
    finally:
        db.close()


def test_reminders_stale_application_nudge(tmp_path: Path) -> None:
    db = Database(tmp_path / "s.db")
    try:
        jid, _, _ = db.upsert_job({"titolo": "Dev", "azienda": "Acme", "link": "https://ex/1"})
        db.set_job_action(jid, "applied", "sent CV")
        # Backdate the application so it counts as stale.
        old = "2000-01-01T00:00:00+00:00"
        db.conn.execute("UPDATE job_actions SET created_at = ? WHERE job_id = ?", (old, jid))
        db.conn.execute("UPDATE jobs SET updated_at = ? WHERE id = ?", (old, jid))
        db.conn.commit()

        # A freshly-applied job must NOT be flagged.
        jid2, _, _ = db.upsert_job({"titolo": "Dev2", "azienda": "Acme", "link": "https://ex/2"})
        db.set_job_action(jid2, "applied", "sent CV")

        stale = db.list_reminders(stale_days=7)["stale"]
        stale_ids = {s["job_id"] for s in stale}
        assert jid in stale_ids
        assert jid2 not in stale_ids
    finally:
        db.close()


def test_saved_searches_crud(tmp_path: Path) -> None:
    db = Database(tmp_path / "s.db")
    try:
        cfg = {"terms": ["QA"], "location": ["Milano"], "is_remote": True, "sites": ["linkedin"]}
        sid = db.create_saved_search("My QA search", cfg)
        assert sid > 0

        rows = db.list_saved_searches()
        assert len(rows) == 1
        assert rows[0]["name"] == "My QA search"
        assert rows[0]["config"]["terms"] == ["QA"]
        assert rows[0]["config"]["is_remote"] is True

        assert db.delete_saved_search(sid) is True
        assert db.list_saved_searches() == []
        assert db.delete_saved_search(sid) is False
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


def _child_counts(db: Database, job_id: int) -> dict[str, int]:
    counts = {}
    for tbl in ("job_actions", "recruiters", "pinned_jobs"):
        row = db.conn.execute(
            f"SELECT COUNT(*) FROM {tbl} WHERE job_id = ?", (job_id,)
        ).fetchone()
        counts[tbl] = int(row[0])
    return counts


def _job_with_children(db: Database, link: str) -> int:
    # azienda derived from the link so two helper jobs don't dedup-merge
    job_id, _new, _hash = db.upsert_job({"titolo": "QA", "azienda": f"Acme {link}", "link": link})
    db.set_job_action(job_id, "applied", "note")
    db.upsert_recruiter(job_id, {"name": "R", "title": "T"})
    db.pin_job("default", job_id)
    assert all(v == 1 for v in _child_counts(db, job_id).values())
    return job_id


def test_delete_job_removes_child_rows(tmp_path: Path) -> None:
    """FK ON DELETE CASCADE is inert (PRAGMA foreign_keys never enabled):
    deleting a job must explicitly clear actions/recruiter/pins or they
    accumulate as orphans forever."""
    db = Database(tmp_path / "d.db")
    try:
        job_id = _job_with_children(db, "https://example.com/1")
        assert db.delete_job(job_id) is True
        assert all(v == 0 for v in _child_counts(db, job_id).values())
    finally:
        db.close()


def test_delete_all_jobs_removes_child_rows(tmp_path: Path) -> None:
    db = Database(tmp_path / "d.db")
    try:
        j1 = _job_with_children(db, "https://example.com/1")
        j2 = _job_with_children(db, "https://example.com/2")
        assert db.delete_all_jobs() == 2
        for jid in (j1, j2):
            assert all(v == 0 for v in _child_counts(db, jid).values())
    finally:
        db.close()
