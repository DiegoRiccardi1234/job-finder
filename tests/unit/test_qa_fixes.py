"""Regression tests for the v1.6.x QA findings.

Covers: chat PII redaction (F-07), tailored-resume contact restore (F-06),
structured-generation stringify (F-12), and the speed-biased scan scoring
policy (F-11).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.db import Database
from app.providers.cerebras_provider import CerebrasProvider
from app.providers.groq_provider import GroqProvider
from app.providers.model_selector import choose_best_model
from app.services.chat.context import build_profile_context
from app.services.generation import _extract_content, _stringify_content
from app.services.pii import restore_contacts
from app.services.scanner_service import _SCORING_POLICY


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "qa.db")
    yield database
    database.close()


def _save_cv(database: Database, markdown: str) -> None:
    pid = database.save_candidate_profile(source_name="cv.md", markdown=markdown, summary={})
    database.set_active_profile(pid)


# ── F-07: chat context strips contacts, keeps the name ──────────────────────
def test_chat_context_redacts_contacts_but_keeps_name(db: Database) -> None:
    _save_cv(db, "Diego Riccardi\ndiego.riccardi@outlook.com | +39 334 5829619\nVia Roma 1, Torino")
    db.set_preference("feature_privacy_mode", "1")
    ctx = build_profile_context(db)
    assert "diego.riccardi@outlook.com" not in ctx
    assert "334 5829619" not in "".join(c for c in ctx if c.isdigit() or c == " ")
    assert "3345829619" not in "".join(c for c in ctx if c.isdigit())
    assert "Diego" in ctx  # the coach still addresses the candidate by name


def test_chat_context_keeps_contacts_when_privacy_off(db: Database) -> None:
    _save_cv(db, "Diego Riccardi\ndiego.riccardi@outlook.com")
    db.set_preference("feature_privacy_mode", "0")
    ctx = build_profile_context(db)
    assert "diego.riccardi@outlook.com" in ctx


# ── F-06: restore real contacts into the tailored résumé output ─────────────
def test_restore_contacts_reinstates_from_source() -> None:
    source = "Diego Riccardi\ndiego@x.io | +39 334 5829619\nVia Roma 1, Torino"
    generated = "Diego Riccardi\n[EMAIL] [PHONE]\n[ADDRESS]"
    out = restore_contacts(generated, source)
    assert "diego@x.io" in out and "[EMAIL]" not in out
    assert "334 5829619" in out and "[PHONE]" not in out
    assert "Via Roma 1, Torino" in out and "[ADDRESS]" not in out


def test_restore_contacts_noop_without_sentinels() -> None:
    text = "Just a plain résumé with no placeholders."
    assert restore_contacts(text, "diego@x.io") == text


# ── F-12: structured generation renders as readable text, not str(dict) ─────
def test_extract_content_formats_nested_dict() -> None:
    result = {"content": {"## Domande tecniche": [{"domanda": "Q1", "spunto": "A1"}]}}
    out = _extract_content(result, "interview_prep")
    assert "{'" not in out  # no Python dict repr leaking to the user
    assert "Q1" in out and "A1" in out
    assert "## Domande tecniche" in out


def test_extract_content_plain_string_unchanged() -> None:
    assert _extract_content({"content": "Gentile Team,"}, "cover_letter") == "Gentile Team,"


def test_stringify_content_list_of_scalars() -> None:
    assert _stringify_content(["a", "b"]) == "a\nb"


# ── F-11: scan scoring policy prefers a free model, never a paid one (403) ───
def test_scoring_policy_prefers_free_over_paid_fast() -> None:
    # A credit-less OpenRouter account 403s paid models, so scoring must pick a
    # ":free" model even when a paid one scores higher on name heuristics.
    catalog = [
        "openai/gpt-3.5-turbo-instruct",  # paid, fast/instruct — would 403
        "openai/gpt-oss-120b:free",
        "cohere/north-mini-code:free",
    ]
    pick = choose_best_model(catalog, policy=_SCORING_POLICY)
    assert pick.endswith(":free")


def test_scoring_policy_returns_a_model_when_only_paid() -> None:
    # Even an all-paid catalog must still return something, never crash.
    catalog = ["openai/gpt-oss-120b", "anthropic/claude-opus-4"]
    assert choose_best_model(catalog, policy=_SCORING_POLICY) in catalog


def test_scoring_policy_quality_floor_skips_tiny_models() -> None:
    # The live regression: a fast bias picked a 1.2B/20B model that scored
    # matches badly. The quality floor must pick the capable 120B instead.
    catalog = [
        "liquid/lfm-2.5-1.2b-instruct:free",
        "openai/gpt-oss-20b:free",
        "openai/gpt-oss-120b:free",
    ]
    assert choose_best_model(catalog, policy=_SCORING_POLICY) == "openai/gpt-oss-120b:free"


def test_cv_policy_prefers_capable_free_model() -> None:
    # CV tools (review/improve) must use a capable model, never a tiny one.
    from app.services.generation import CV_POLICY

    catalog = [
        "liquid/lfm-2.5-1.2b-instruct:free",
        "openai/gpt-oss-20b:free",
        "openai/gpt-oss-120b:free",
    ]
    assert choose_best_model(catalog, policy=CV_POLICY) == "openai/gpt-oss-120b:free"


# ── F-02: cerebras/groq complete_json degrades on a chatty reply ─────────────
class _FakeClient:
    """Minimal stand-in for an OpenAI/Groq SDK client returning fixed content."""

    def __init__(self, content: str) -> None:
        self.content = content
        self.chat = self  # type: ignore[assignment]
        self.completions = self  # type: ignore[assignment]

    def create(self, **_kwargs: object) -> object:
        message = type("M", (), {"content": self.content})()
        choice = type("C", (), {"message": message})()
        return type("R", (), {"choices": [choice], "usage": None})()


@pytest.mark.parametrize("provider_cls", [CerebrasProvider, GroqProvider])
@pytest.mark.parametrize("content", ['{"punteggio": 8}', 'Ecco il punteggio: {"punteggio": 8}'])
def test_complete_json_handles_structured_and_prose(provider_cls: type, content: str) -> None:
    provider = provider_cls(api_key=None)
    provider.client = _FakeClient(content)  # type: ignore[assignment]
    provider._selected_model = "gpt-oss-120b"
    # A chatty (prose-wrapped) reply must not raise a bare ValueError into
    # failover — it degrades to regex JSON extraction like the OpenAI-compat base.
    assert provider.complete_json("prompt") == {"punteggio": 8}
