"""Opening relevant junior AI roles must not open every engineering trade."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db import Database
from app.routers.saved_searches import build_router
from app.services import candidate_facts as cf
from app.services.onboarding import onboarding_context
from app.services.scan.prompts import _SCORING_RULES, _analysis_prompt, _batch_analysis_prompt
from app.services.scan.vocab import pre_filtro, title_off_topic, title_vocabulary


@pytest.fixture
def vocabulary():
    return title_vocabulary(
        search_terms=["Junior AI Engineer", "AI Automation Developer", "Integration Engineer"],
        skills=["Python", "SQL", "REST API"],
        roles=["Junior Functional Analyst"],
    )


@pytest.mark.parametrize(
    "title",
    [
        "Junior AI Engineer",
        "AI Automation Developer",
        "Junior Integration Engineer",
        "Python AI Developer",
    ],
)
def test_relevant_developer_engineer_titles_are_not_excluded(vocabulary, title):
    assert title_off_topic(title, vocabulary) is False
    assert pre_filtro(title, "Inserimento junior con affiancamento e formazione.") == (False, "")


@pytest.mark.parametrize(
    "title", ["Mechanical Engineer", "Civil Engineer", "Business Developer", "Sales Engineer"]
)
def test_generic_role_words_do_not_admit_other_trades(vocabulary, title):
    assert title_off_topic(title, vocabulary) is True


def test_senior_mentor_in_junior_description_is_not_a_senior_opening():
    assert pre_filtro(
        "Junior AI Automation Developer",
        "Sarai affiancato da un senior developer e da un principal engineer.",
    ) == (False, "")


@pytest.mark.parametrize(
    "title",
    [
        "Senior AI Engineer",
        "Senior ML Engineer",
        "Lead AI Developer",
        "Principal Machine Learning Engineer",
    ],
)
def test_senior_qualified_titles_remain_excluded(title):
    assert pre_filtro(title, "Formazione sul progetto e lavoro con il team.")[0] is True


def test_advanced_ml_research_requirement_stays_visible_without_banning_all_ml():
    graduate = cf.CandidateFacts(education_level="Triennale")
    level, reason = cf.education_status("Requirements: PhD in Machine Learning required.", graduate)
    assert level == "PhD" and reason
    _level, optional = cf.education_status("Bachelor's degree required, PhD preferred.", graduate)
    assert optional is None


def test_common_python_skill_does_not_claim_goal_alignment_in_either_scoring_path():
    context = "Obiettivo di carriera: analisi funzionale e automazione AI con affiancamento."
    single = _analysis_prompt(
        "Skills: Python",
        "Python Full Stack Developer",
        "Acme",
        "Sviluppo full stack di un portale.",
        context,
    )
    batch = _batch_analysis_prompt(
        "Skills: Python",
        [
            {
                "titolo": "Python Full Stack Developer",
                "azienda": "Acme",
                "descrizione": "Sviluppo full stack.",
            }
        ],
        context,
    )
    for prompt in (single, batch):
        assert _SCORING_RULES in prompt
        assert "Condividere Python" in prompt
        assert "spiegalo in punti_deboli" in prompt
        assert context in prompt


def test_selected_roles_reach_scoring_context_without_json_punctuation(tmp_path):
    db = Database(tmp_path / "context.db")
    try:
        db.set_preference("onboarding_goal", "Automazione AI con affiancamento")
        db.set_preference(
            "preferred_roles", json.dumps(["Junior Functional Analyst", "AI Automation Developer"])
        )
        context = onboarding_context(db)
        assert "Obiettivo di carriera: Automazione AI con affiancamento" in context
        assert "Ruoli preferiti: Junior Functional Analyst, AI Automation Developer" in context
        assert '["' not in context
    finally:
        db.close()


def test_saved_search_api_preserves_multicity_and_remote_configurations(tmp_path):
    db = Database(tmp_path / "searches.db")
    try:
        app = FastAPI()
        app.include_router(build_router(SimpleNamespace(db=db)))
        onsite = {
            "terms": ["analista funzionale junior", "AI automation junior"],
            "location": ["Torino", "Roma"],
            "country": "italy",
            "is_remote": False,
            "sites": ["linkedin", "indeed"],
            "experience_levels": ["entry", "junior"],
            "work_types": ["onsite", "hybrid"],
        }
        remote = {**onsite, "location": ["Italy"], "is_remote": True, "work_types": ["remote"]}
        with TestClient(app) as client:
            for name, config in [("Junior Torino/Roma", onsite), ("Junior remoto", remote)]:
                assert (
                    client.post(
                        "/api/saved-searches", json={"name": name, "config": config}
                    ).status_code
                    == 201
                )
            saved = client.get("/api/saved-searches").json()["searches"]
            by_name = {row["name"]: row["config"] for row in saved}
            assert by_name == {"Junior Torino/Roma": onsite, "Junior remoto": remote}
    finally:
        db.close()
