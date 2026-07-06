"""ChatResponse must carry ``degraded`` + ``chat_state`` through to the client.

``handle_chat_message`` returns both fields, but the router does
``ChatResponse(**result)``. If the model doesn't declare them, Pydantic drops
them silently — so the frontend "degraded answer" indicator (web/app.js) reads
``undefined`` and never renders, and the user can't tell a canned fallback from
a real LLM reply.
"""

from __future__ import annotations

from app.models import ChatResponse


def test_chat_response_preserves_degraded_and_state() -> None:
    result = {
        "session_id": "default",
        "answer": "canned fallback",
        "updated_preferences": {},
        "action": None,
        "suggested_roles": [],
        "chat_state": "ready_to_search",
        "degraded": True,
    }
    dumped = ChatResponse(**result).model_dump()
    assert dumped["degraded"] is True
    assert dumped["chat_state"] == "ready_to_search"


def test_chat_response_degraded_defaults_false() -> None:
    dumped = ChatResponse(session_id="default", answer="hi").model_dump()
    assert dumped["degraded"] is False
    assert dumped["chat_state"] == ""
