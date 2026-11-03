"""MCP workflows preserve Job Finder invariants without making nested AI calls."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent_tools import AgentService, AgentToolError
from app.db import Database
from app.migrations import apply_migrations
from app.services.scan_control import ScanControl


@pytest.fixture
def service(tmp_path: Path):
    db = Database(tmp_path / "isolated.db")
    profile_id = db.save_candidate_profile(
        "synthetic.md",
        "Candidate Example, candidate@example.invalid. Informatica, Python. Esperienza 0 anni.",
        {"name": "Candidate Example", "skills": ["Python"], "years_experience": 0},
        name="Candidate Example",
    )
    db.set_active_profile(profile_id)
    container = SimpleNamespace(
        db=db,
        settings=SimpleNamespace(data_dir=tmp_path),
        scan_control=ScanControl(),
        providers=SimpleNamespace(),
        feature_enabled=lambda name, default=True: default,
    )
    agent = AgentService(container)
    yield agent
    agent.shutdown()
    db.close()


def seed(service: AgentService, title: str = "Python Junior", *, senior: bool = False) -> int:
    desc = (
        "Sviluppo Python e SQL con affiancamento e formazione in un team IT. " * 6
        + ("Richiesti almeno 5 anni di esperienza in Python." if senior else "")
    )
    job_id, _, _ = service.db.upsert_job({
        "titolo": title, "azienda": "Synthetic Co", "descrizione": desc,
        "sede": "Torino, Italy", "link": f"https://example.invalid/{title}",
        "modalita": "onsite",
    })
    return job_id


def evaluation(job_id: int, score: int = 9) -> dict:
    return {"job_id": job_id, "punteggio": score, "riassunto": "Synthetic evaluation"}


def test_additive_migration_preserves_existing_records_and_is_idempotent(service):
    job_id = seed(service)
    service.db.set_job_action(job_id, "note", "Existing user note")
    version = apply_migrations(service.db.conn)
    assert version >= 31
    assert service.db.get_job(job_id)["titolo"] == "Python Junior"
    assert service.db.list_job_actions(job_id)[0]["notes"] == "Existing user note"
    assert apply_migrations(service.db.conn) == version


def test_context_and_analysis_prompt_respect_privacy(service):
    job_id = seed(service)
    context = json.dumps(service.get_context())
    assert "candidate@example.invalid" not in context
    prompt = service.prepare_analysis([job_id])["prompt"]
    assert "candidate@example.invalid" not in prompt
    assert "Candidate Example" not in prompt
    assert service.db.get_job(job_id)["punteggio_ai"] is None


def test_complete_external_analysis_is_stamped_and_hard_constraints_win(service):
    junior, senior = seed(service), seed(service, "Python Senior", senior=True)
    ticket = service.prepare_analysis([junior, senior])
    analyses = [evaluation(junior), evaluation(senior)]
    result = service.submit_analysis(ticket["ticket_id"], analyses, "test-agent")
    assert result["saved_job_ids"] == [junior, senior]
    assert service.db.get_job(junior)["punteggio_ai"] == 9
    assert service.db.get_job(senior)["punteggio_ai"] <= 3
    assert service.db.get_job(senior)["analysis_model"] == "external/test-agent"
    assert service.submit_analysis(ticket["ticket_id"], analyses, "test-agent") == result
    with pytest.raises(AgentToolError, match="already_submitted"):
        service.submit_analysis(ticket["ticket_id"], [evaluation(junior, 1), evaluation(senior)], "x")


@pytest.mark.parametrize("invalid", ["missing", "duplicate", "foreign", "bool_score", "no_summary"])
def test_bad_batch_never_partially_overwrites_scores(service, invalid):
    first, second = seed(service), seed(service, "Other Python")
    service.db.update_job_analysis(first, {"punteggio": 6, "riassunto": "Prior evaluation"})
    ticket = service.prepare_analysis([first, second])
    results = [evaluation(first), evaluation(second)]
    if invalid == "missing":
        results.pop()
    elif invalid == "duplicate":
        results[1] = evaluation(first)
    elif invalid == "foreign":
        results[1]["job_id"] = 900_000
    elif invalid == "bool_score":
        results[1]["punteggio"] = True
    else:
        results[1]["riassunto"] = ""
    with pytest.raises(AgentToolError):
        service.submit_analysis(ticket["ticket_id"], results, "test-agent")
    assert service.db.get_job(first)["punteggio_ai"] == 6
    assert service.db.get_job(second)["punteggio_ai"] is None


@pytest.mark.parametrize("changed", ["profile", "criteria", "posting"])
def test_stale_analysis_is_rejected_without_a_model_call(service, changed):
    job_id = seed(service)
    ticket = service.prepare_analysis([job_id])
    if changed == "profile":
        active = service.db.get_active_candidate_profile()
        service.db.update_candidate_profile_fields(active["id"], markdown="Changed curriculum")
    elif changed == "criteria":
        service.db.set_preference("onboarding_goal", "Different career")
    else:
        with service.db.lock:
            service.db.conn.execute("UPDATE jobs SET descrizione='Changed posting' WHERE id=?", (job_id,))
            service.db.conn.commit()
    with pytest.raises(AgentToolError, match="changed"):
        service.submit_analysis(ticket["ticket_id"], [evaluation(job_id)], "test-agent")
    assert service.db.get_job(job_id)["punteggio_ai"] is None


def test_repeated_writes_do_not_duplicate_timeline_and_conflicting_keys_fail(service):
    job_id = seed(service)
    first = service.manage_job(job_id, "note", "stable-request", "One observation")
    assert service.manage_job(job_id, "note", "stable-request", "One observation") == first
    notes = [a for a in service.db.list_job_actions(job_id) if a["action"] == "note"]
    assert len(notes) == 1
    with pytest.raises(AgentToolError, match="request_id_conflict"):
        service.manage_job(job_id, "note", "stable-request", "Different observation")


def test_archive_restore_are_reversible_and_do_not_erase_application_history(service):
    job_id = seed(service)
    service.db.set_job_action(job_id, "applied", "Existing application")
    service.manage_job(job_id, "archive", "archive-1")
    assert service.db.get_job(job_id)["status"] == "archived"
    service.manage_job(job_id, "restore", "restore-1")
    assert service.db.get_job(job_id)["status"] == "applied"
    assert service.db.get_job(job_id)["applied_at"]


def test_profile_and_application_changes_are_proposals_and_staleness_blocks_them(service):
    job_id = seed(service)
    proposal = service.propose_change(
        "application_state", {"job_id": job_id, "action": "applied"}, "proposal-1"
    )
    assert service.db.get_job(job_id)["status"] == "open"
    assert service.list_proposals()[0]["id"] == proposal["proposal_id"]
    service.decide_proposal(proposal["proposal_id"], True)
    assert service.db.get_job(job_id)["status"] == "applied"
    profile_proposal = service.propose_change("profile", {"skills": ["Python", "SQL"]}, "p2")
    service.db.set_preference("onboarding_goal", "Changed by human")
    with pytest.raises(AgentToolError, match="profile_changed"):
        service.decide_proposal(profile_proposal["proposal_id"], True)


def test_confirm_unchanged_cv_fact_does_not_make_it_a_manual_override(service):
    proposal = service.propose_change(
        "profile", {"years_experience": 0.0, "skills": ["Python", "SQL"]}, "profile-update"
    )
    assert "years_experience" not in service.list_proposals()[0]["payload"]
    service.decide_proposal(proposal["proposal_id"], True)
    assert service.db.get_preference("profile_fact_years_experience", "") == ""
    assert service.db.get_active_candidate_profile()["summary_json"]["skills"] == ["Python", "SQL"]


def test_preference_proposal_updates_both_sources_and_excludes_control_plane(service):
    proposal = service.propose_change(
        "preferences", {"preferred_roles": '["Analista junior"]'}, "roles-1"
    )
    service.decide_proposal(proposal["proposal_id"], True)
    assert service.db.get_active_candidate_profile()["summary_json"]["preferred_roles"] == ["Analista junior"]
    assert json.loads(service.db.get_preference("preferred_roles")) == ["Analista junior"]
    with pytest.raises(AgentToolError, match="unsupported_preference"):
        service.propose_change("preferences", {"feature_privacy_mode": "0"}, "unsafe")


def test_collect_only_search_uses_shared_lock_and_has_no_nested_inference(service, monkeypatch):
    from app.services import scanner_service

    started, release = threading.Event(), threading.Event()
    calls = []

    def collect(**kwargs):
        assert kwargs["analysis_mode"] == "collect"
        calls.append(kwargs["payload"].search_terms)
        started.set()
        release.wait(2)
        yield {"type": "complete", "totale_analizzati": 0}

    monkeypatch.setattr(scanner_service, "run_scan", collect)
    first = service.start_search({"search_terms": ["Python"]}, "search-1")
    assert started.wait(2)
    assert service.start_search({"search_terms": ["Python"]}, "search-1") == first
    with pytest.raises(AgentToolError, match="scan_in_progress"):
        service.start_search({"search_terms": ["SQL"]}, "search-2")
    service.cancel_operation(first["operation_id"])
    release.set()
    for worker in list(service._threads.values()):
        worker.join(3)
    assert service.get_operation(first["operation_id"])["status"] == "cancelled"
    assert calls == [["Python"]]
    assert not service.container.scan_control.running


def test_dispatch_rejects_unknown_tools_and_loose_parameter_types(service):
    job_id = seed(service)
    for name, arguments in (
        ("jobfinder_delete_job", {"job_id": job_id}),
        ("jobfinder_get_job", {"job_id": str(job_id)}),
        ("jobfinder_prepare_analysis", {"job_ids": [True]}),
        ("jobfinder_manage_job", {"job_id": job_id, "action": "favorite", "request_id": "x", "is_favorite": "false"}),
        ("jobfinder_list_jobs", {"file": "credentials"}),
    ):
        with pytest.raises(AgentToolError):
            service.dispatch(name, arguments)


def test_short_posting_is_not_given_an_invented_score(service):
    job_id, _, _ = service.db.upsert_job({
        "titolo": "Python", "azienda": "Synthetic", "descrizione": "Python junior",
        "link": "https://example.invalid/short",
    })
    ticket = service.prepare_analysis([job_id])
    result = service.submit_analysis(ticket["ticket_id"], [evaluation(job_id)], "agent")
    assert result["unevaluated_job_ids"] == [job_id]
    assert service.db.get_job(job_id)["punteggio_ai"] is None


def test_clone_detection_does_not_call_a_second_model(service):
    first, second = seed(service), seed(service, "Python in another team")
    ticket = service.prepare_analysis([first, second])
    values = [evaluation(first), evaluation(second)]
    for item in values:
        item["riassunto"] = "The same unusually specific judgement copied onto distinct postings."
    with pytest.raises(AgentToolError, match="cloned_evaluations"):
        service.submit_analysis(ticket["ticket_id"], values, "agent")
    assert service.db.get_job(first)["punteggio_ai"] is None


def test_application_proposal_does_not_overwrite_a_newer_human_state(service):
    job_id = seed(service)
    proposal = service.propose_change(
        "application_state", {"job_id": job_id, "action": "applied"}, "p-newstate"
    )
    service.db.set_job_action(job_id, "interviewing", "Confirmed interview")
    with pytest.raises(AgentToolError, match="changed"):
        service.decide_proposal(proposal["proposal_id"], True)
    assert service.db.get_job(job_id)["status"] == "interviewing"


def test_shutdown_defers_close_until_a_blocking_scraper_finishes(service, monkeypatch):
    from app.services import scanner_service

    started, release, closed = threading.Event(), threading.Event(), threading.Event()

    def collect(**kwargs):
        started.set()
        release.wait(2)
        # The worker is still allowed to finish its DB bookkeeping after cancel.
        service.db.set_preference("test_worker_finished", "1")
        yield {"type": "complete", "cancelled": kwargs["cancel_check"]()}

    monkeypatch.setattr(scanner_service, "run_scan", collect)
    service.start_search({"search_terms": ["Python"]}, "shutdown-search")
    assert started.wait(2)
    assert not service.shutdown(close_callback=closed.set, join_timeout=0.01)
    assert not closed.is_set()
    with pytest.raises(AgentToolError, match="app_shutting_down"):
        service.dispatch("jobfinder_get_context", {})
    # A second shutdown must not race the deferred callback by closing twice.
    assert not service.shutdown(close_callback=lambda: pytest.fail("duplicate close"), join_timeout=0)
    release.set()
    assert closed.wait(2)
    assert service.db.get_preference("test_worker_finished") == "1"
