"""CV replacement must preserve search choices and honest fact provenance.

The router is mounted directly so tests never import the global app/container
or contact a configured provider. Every database belongs to tmp_path.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import rate_limit
from app.cv_ingest import summarize_profile
from app.db import Database
from app.routers.profile import build_router
from app.services import candidate_facts as cf
from app.services.readiness import profile_readiness
from app.services.search_intent import parse_preferred_roles, resolve_search_terms


class OfflineProvider:
    def complete_json(self, **_kwargs):
        raise RuntimeError("offline CV parser")


@pytest.fixture
def client(tmp_path: Path):
    db = Database(tmp_path / "isolated.db")
    container = SimpleNamespace(
        db=db,
        providers=OfflineProvider(),
        log=logging.getLogger("cv-regression"),
        feature_enabled=lambda _key, default: default,
    )
    app = FastAPI()
    app.include_router(build_router(container))
    rate_limit.reset()
    with TestClient(app) as browser:
        yield browser, db
    db.close()


def upload(browser: TestClient, city: str):
    cv = (
        "Anna Bianchi\nanna@example.com | 340 1122334 | " + city + ", Italia\n\n"
        "PROFILO\nJunior IT: analisi funzionale e automazione AI.\n"
        "ESPERIENZA\nAnnotator 04/2026 - 05/2026. Valutazione LLM.\n"
        "COMPETENZE\nPython, SQL, REST API, Git.\n"
        "ISTRUZIONE\nLaurea triennale in Informatica, 07/2025.\n"
    )
    response = browser.post("/api/upload-cv", files={"file": ("cv.txt", cv.encode(), "text/plain")})
    assert response.status_code == 200
    return response.json()


def test_upload_preserves_selected_roles_and_previous_queries(client):
    browser, db = client
    upload(browser, "Torino")
    chosen = ["Junior Functional Analyst", "AI Automation Junior"]
    browser.patch("/api/profile", json={"preferred_roles": chosen})
    db.set_preference("last_scan_terms", json.dumps(["analista funzionale junior"]))
    upload(browser, "Milano")
    assert json.loads(db.get_preference("preferred_roles")) == chosen
    assert resolve_search_terms(db) == (["analista funzionale junior"], "last_scan")
    db.set_preference("last_scan_terms", "")
    assert resolve_search_terms(db) == (chosen, "profile")


def test_upload_city_follows_active_cv_without_creating_a_manual_override(client):
    browser, db = client
    first = upload(browser, "Torino")
    assert db.get_preference(cf.FACT_BASE_CITIES, "") == ""
    assert cf.candidate_facts(db).sources["work_rule"] == "cv"
    assert cf.candidate_facts(db).work_rule.cities == ("torino",)
    upload(browser, "Milano")
    assert cf.candidate_facts(db).work_rule.cities == ("milano",)
    assert profile_readiness(db)["suggested_locations"] == ["Milano"]
    # Reusing an existing CV follows its inferred city again as well.
    assert browser.post(f"/api/profiles/{first['profile_id']}/activate").status_code == 200
    assert cf.candidate_facts(db).work_rule.cities == ("torino",)


def test_manual_city_survives_cv_upload_and_can_be_reset_explicitly(client):
    browser, db = client
    upload(browser, "Torino")
    assert browser.patch("/api/profile", json={"base_cities": ["Bari"]}).status_code == 200
    upload(browser, "Milano")
    facts = cf.candidate_facts(db)
    assert facts.work_rule.cities == ("bari",)
    assert facts.sources["work_rule"] == "manuale"
    browser.patch("/api/profile", json={"base_cities": []})
    assert cf.candidate_facts(db).work_rule.cities == ("milano",)
    assert cf.candidate_facts(db).sources["work_rule"] == "cv"


def test_unmarked_legacy_city_is_preserved_and_requests_review(client):
    browser, db = client
    db.set_preference(cf.FACT_BASE_CITIES, "Torino")
    upload(browser, "Milano")
    result = browser.get("/api/profile/matching-facts").json()
    assert result["base_cities"] == ["torino"]
    assert result["sources"]["work_rule"] == "da_verificare"
    assert result["needs_review"] == ["work_rule"]
    assert "work_rule" in profile_readiness(db)["warnings"]
    # Agreement without changed facts must never turn a legacy value manual.
    browser.patch("/api/profile", json={})
    assert cf.candidate_facts(db).sources["work_rule"] == "da_verificare"


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ('["Analista funzionale", "Automation, AI"]', ["Analista funzionale", "Automation, AI"]),
        ("Analista funzionale, Automation AI", ["Analista funzionale", "Automation AI"]),
        ('["broken', []),
        ("{}", []),
    ],
)
def test_roles_parser_accepts_both_stored_formats(stored, expected):
    assert parse_preferred_roles(stored) == expected


def test_cv_roles_are_suggestions_only_and_an_explicit_empty_selection_is_kept(client):
    browser, db = client
    upload(browser, "Torino")
    assert db.get_preference("preferred_roles", "") == ""
    roles, origin = resolve_search_terms(db)
    assert origin == "cv"
    assert roles[:2] == ["Junior Functional Analyst", "Junior Automation / AI Integration"]
    browser.patch("/api/profile", json={"preferred_roles": []})
    assert resolve_search_terms(db) == ([], "profile")


def test_role_suggestions_do_not_invent_future_technology_skills():
    result = summarize_profile(
        "PROFILO\nJunior IT: analisi funzionale e automazione AI.\n"
        "COMPETENZE\nPython e SQL.\n"
        "Obiettivo: imparare le integrazioni aziendali con affiancamento."
    )
    assert result["preferred_roles"][:2] == [
        "Junior Functional Analyst",
        "Junior Automation / AI Integration",
    ]
    assert "sap" not in result["skills"]
    assert "rpa" not in result["skills"]
    assert "power platform" not in result["skills"]
