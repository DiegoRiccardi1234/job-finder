import json

from app.db import Database
from app.services.chat.intents import has_role_guidance_intent, has_search_intent
from app.services.chat.prompts import system_prompt
from app.services.chat.state import extract_pref_updates, get_chat_state
from app.services.chat_service import handle_chat_message


def test_extract_pref_updates_remote_and_salary() -> None:
    result = extract_pref_updates("I want full remote and min salary 35000")
    assert result["remote_mode"] == "full_remote"
    assert result["min_ral"] == "35000"


def test_extract_pref_updates_hybrid_lowercases_k_units() -> None:
    result = extract_pref_updates("I accept hybrid, RAL of 30")
    assert result["remote_mode"] == "hybrid"
    assert result["min_ral"] == "30000"


def test_search_intent_detection() -> None:
    assert has_search_intent("Please find Python jobs in Milan")
    assert has_search_intent("cerca lavori remoti")
    assert not has_search_intent("What is my CV summary?")


def test_role_guidance_intent_requires_all_hints() -> None:
    assert has_role_guidance_intent("Quali figure lavorative si adattano al mio CV?")
    assert not has_role_guidance_intent("Find me jobs")


def test_system_prompt_includes_language_and_envelope() -> None:
    prompt = system_prompt("onboarding", "it")
    assert "Italian" in prompt
    assert "FILL_SCAN_FORM" in prompt
    assert "AI Career Coach" in prompt


def test_system_prompt_falls_back_on_unknown_state() -> None:
    prompt = system_prompt("unknown_state", "en")
    assert "AI Career Coach for IT professionals" in prompt  # advising default


def test_get_chat_state_no_cv(db: Database) -> None:
    assert get_chat_state(db) == "no_cv"


def test_handle_chat_message_uses_provider_json_envelope(tmp_path, fake_provider) -> None:
    fake_provider.chat_response = json.dumps({"answer": "hello Diego", "action": None})
    db = Database(tmp_path / "searcher.db")
    try:
        result = handle_chat_message(
            db=db,
            provider_manager=fake_provider,
            message="ciao",
            session_id="s1",
        )
    finally:
        db.close()

    assert result["answer"] == "hello Diego"
    assert result["action"] is None
    assert result["chat_state"] == "no_cv"
    assert len(fake_provider.chat_calls) == 1


def test_handle_chat_message_falls_back_when_provider_raises(tmp_path, fake_provider) -> None:
    fake_provider.raise_on_chat = True
    db = Database(tmp_path / "searcher.db")
    try:
        result = handle_chat_message(
            db=db,
            provider_manager=fake_provider,
            message="please recommend the best jobs",
            session_id="s2",
        )
    finally:
        db.close()

    assert "scan" in result["answer"].lower() or "no analyzed" in result["answer"].lower()
    assert result["chat_state"] == "no_cv"


def test_handle_chat_message_strips_markdown_fence(tmp_path, fake_provider) -> None:
    fake_provider.chat_response = '```json\n{"answer": "wrapped", "action": null}\n```'
    db = Database(tmp_path / "searcher.db")
    try:
        result = handle_chat_message(
            db=db,
            provider_manager=fake_provider,
            message="ciao",
            session_id="s3",
        )
    finally:
        db.close()

    assert result["answer"] == "wrapped"
