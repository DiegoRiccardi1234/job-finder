"""Post-scrape scan filters: job_type (multi-select) and work mode (on-site).

jobspy accepts a single job_type and only an is_remote hint, so multi-type and
on-site selections were silently ignored. These helpers filter the scraped rows
after the fact, keeping rows whose field jobspy didn't report (never over-drop).
"""

from __future__ import annotations

from app.services.scanner_service import _row_job_type_ok, _row_work_mode_ok


def test_job_type_keeps_selected_and_drops_others() -> None:
    assert _row_job_type_ok({"job_type": "fulltime"}, ["fulltime"]) is True
    assert _row_job_type_ok({"job_type": "parttime"}, ["fulltime"]) is False
    # multi-select: keep any of the chosen types
    assert _row_job_type_ok({"job_type": "contract"}, ["fulltime", "contract"]) is True


def test_job_type_keeps_unknown_and_no_filter() -> None:
    assert _row_job_type_ok({}, ["fulltime"]) is True  # jobspy didn't report → keep
    assert _row_job_type_ok({"job_type": float("nan")}, ["fulltime"]) is True
    assert _row_job_type_ok({"job_type": "parttime"}, []) is True  # no filter selected


def test_work_mode_remote_and_onsite() -> None:
    assert _row_work_mode_ok({"is_remote": True}, ["remote"]) is True
    assert _row_work_mode_ok({"is_remote": False}, ["remote"]) is False
    assert _row_work_mode_ok({"is_remote": False}, ["onsite"]) is True
    assert _row_work_mode_ok({"is_remote": True}, ["onsite"]) is False


def test_work_mode_both_hybrid_unknown_and_none() -> None:
    assert _row_work_mode_ok({"is_remote": True}, ["remote", "onsite"]) is True  # both → all
    assert _row_work_mode_ok({"is_remote": True}, ["hybrid"]) is True  # can't distinguish → all
    assert _row_work_mode_ok({}, ["onsite"]) is True  # unknown is_remote → keep
    assert _row_work_mode_ok({"is_remote": float("nan")}, ["onsite"]) is True
    assert _row_work_mode_ok({"is_remote": True}, []) is True  # no filter
