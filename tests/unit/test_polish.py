"""Phase 4 polish: bounded rate-limit buckets + cheap analysis check."""

from __future__ import annotations

from collections import deque
from pathlib import Path

from app.db import Database


def test_rate_limit_sweeps_stale_buckets() -> None:
    from app import rate_limit as rl

    rl.reset()
    rl._buckets[("1.2.3.4", "old")] = deque([1.0])  # ancient last hit
    rl._last_sweep = 0.0
    rl._maybe_sweep(rl._STALE_AFTER_SECONDS + 1000.0)
    assert ("1.2.3.4", "old") not in rl._buckets
    rl.reset()


def test_job_has_analysis(tmp_path: Path) -> None:
    db = Database(tmp_path / "j.db")
    try:
        job_id, _, _ = db.upsert_job({"titolo": "X", "azienda": "Y", "link": "l1"})
        assert db.job_has_analysis(job_id) is False
        db.update_job_analysis(job_id=job_id, analysis={"punteggio": 7})
        assert db.job_has_analysis(job_id) is True
    finally:
        db.close()


def test_update_candidate_profile_fields(tmp_path: Path) -> None:
    db = Database(tmp_path / "p.db")
    try:
        pid = db.save_candidate_profile(
            source_name="cv.md", markdown="old text", summary={"name": "Old"}, name="Old"
        )
        db.update_candidate_profile_fields(pid, markdown="new CV text for scoring", name="New Name")
        prof = db.get_candidate_profile(pid)
        assert prof is not None
        assert prof["markdown"] == "new CV text for scoring"
        assert prof["name"] == "New Name"
    finally:
        db.close()
