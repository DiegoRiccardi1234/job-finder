"""Main chat handler: orchestrates state → context → prompt → provider → parse."""

from __future__ import annotations

import json
from typing import Any

from app.db import Database
from app.log import get_logger
from app.providers.factory import ProviderManager
from app.services.chat.context import (
    build_preferences_context,
    build_profile_context,
    jobs_context,
)
from app.services.chat.fallback import fallback_answer
from app.services.chat.prompts import system_prompt
from app.services.chat.state import extract_pref_updates, get_chat_state

log = get_logger(__name__)


def _strip_markdown_fence(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[len("```json"):].lstrip()
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:].lstrip()
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].rstrip()
    return cleaned


def _parse_llm_response(raw: str) -> tuple[str, dict[str, Any] | None]:
    """Extract ``answer`` and ``action`` from the model's JSON envelope.

    Falls back to the raw text as ``answer`` if JSON parsing fails.
    """
    try:
        parsed = json.loads(_strip_markdown_fence(raw))
    except json.JSONDecodeError as exc:
        log.info("Chat response not valid JSON, using raw text: %s", exc)
        return raw, None

    if not isinstance(parsed, dict):
        return raw, None

    answer = parsed.get("answer") or raw
    action = parsed.get("action") if isinstance(parsed.get("action"), dict) else None
    return str(answer), action


def handle_chat_message(
    db: Database,
    provider_manager: ProviderManager,
    message: str,
    session_id: str,
    provider: str | None = None,
) -> dict[str, Any]:
    """Handle one chat turn.

    Flow:
    1. Persist the user message.
    2. Extract preference updates from the message and store them.
    3. Compute the chat state (``no_cv`` / ``onboarding`` / ``ready_to_search`` / ``advising``).
    4. Build profile, preferences, and jobs context blocks.
    5. Call the active LLM provider with the state-specific system prompt.
    6. Parse the JSON envelope (``answer`` + optional ``action``). Fall back
       to a rule-based answer if the provider fails.
    7. Persist the assistant reply and return a summary dict.
    """
    db.save_chat_message(session_id=session_id, role="user", content=message)

    updates = extract_pref_updates(message)
    for key, value in updates.items():
        db.set_preference(key, value)

    state = get_chat_state(db)
    ui_lang = db.get_preference("ui_language", "en")
    sys_prompt = system_prompt(state=state, ui_language=ui_lang)

    prompt_messages = [
        {"role": "system", "content": sys_prompt},
        {
            "role": "user",
            "content": (
                f"=== Candidate Profile ===\n{build_profile_context(db)}\n\n"
                f"=== Preferences ===\n{build_preferences_context(db)}\n\n"
                f"=== Top Job Listings ===\n{jobs_context(db)}\n\n"
                f"=== User Message ===\n{message}"
            ),
        },
    ]

    try:
        raw_answer = provider_manager.chat(
            prompt_messages, max_tokens=700, provider_name=provider
        )
        answer, action_payload = _parse_llm_response(raw_answer)
    except Exception as exc:
        log.error("Provider chat call failed, using fallback: %s", exc, exc_info=True)
        answer, action_payload = fallback_answer(db=db, message=message)

    db.save_chat_message(session_id=session_id, role="assistant", content=answer)
    return {
        "session_id": session_id,
        "answer": answer,
        "updated_preferences": updates,
        "chat_state": state,
        "action": action_payload,
    }
