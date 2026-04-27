"""Unit tests for `_parse_llm_response` edge cases including `suggested_roles`."""

from __future__ import annotations

from app.services.chat.handler import _parse_llm_response


def test_parse_plain_valid_json() -> None:
    raw = '{"answer": "ok", "action": null}'
    answer, action, roles = _parse_llm_response(raw)
    assert answer == "ok"
    assert action is None
    assert roles == []


def test_parse_strips_json_fence() -> None:
    raw = '```json\n{"answer": "wrapped", "action": null}\n```'
    answer, _, _ = _parse_llm_response(raw)
    assert answer == "wrapped"


def test_parse_invalid_json_returns_raw_as_answer() -> None:
    raw = "this is not json"
    answer, action, roles = _parse_llm_response(raw)
    assert answer == raw
    assert action is None
    assert roles == []


def test_parse_non_dict_payload() -> None:
    raw = '["a", "b"]'
    answer, action, roles = _parse_llm_response(raw)
    assert answer == raw
    assert action is None
    assert roles == []


def test_parse_extracts_suggested_roles() -> None:
    raw = (
        '{"answer": "ecco", "action": null, '
        '"suggested_roles": [{"label": "ML Engineer", "keywords": ["ML", "Machine Learning"]}]}'
    )
    _, _, roles = _parse_llm_response(raw)
    assert len(roles) == 1
    assert roles[0]["label"] == "ML Engineer"
    assert "ML" in roles[0]["keywords"]


def test_parse_drops_role_without_label() -> None:
    raw = (
        '{"answer": "x", "action": null, '
        '"suggested_roles": [{"keywords": ["foo"]}, {"label": "  ", "keywords": ["bar"]}, '
        '{"label": "Valid", "keywords": ["v"]}]}'
    )
    _, _, roles = _parse_llm_response(raw)
    assert len(roles) == 1
    assert roles[0]["label"] == "Valid"


def test_parse_role_keywords_fallback_to_label() -> None:
    raw = '{"answer": "x", "action": null, "suggested_roles": [{"label": "Cloud Engineer"}]}'
    _, _, roles = _parse_llm_response(raw)
    assert roles[0]["keywords"] == ["Cloud Engineer"]


def test_parse_role_keywords_non_list_is_replaced() -> None:
    raw = (
        '{"answer": "x", "action": null, '
        '"suggested_roles": [{"label": "DBA", "keywords": "oracle"}]}'
    )
    _, _, roles = _parse_llm_response(raw)
    assert roles[0]["keywords"] == ["DBA"]


def test_parse_ignores_non_dict_role_entries() -> None:
    raw = (
        '{"answer": "x", "action": null, '
        '"suggested_roles": ["not a dict", null, {"label": "OK", "keywords": ["k"]}]}'
    )
    _, _, roles = _parse_llm_response(raw)
    assert len(roles) == 1
    assert roles[0]["label"] == "OK"


def test_parse_action_kept_when_dict() -> None:
    raw = (
        '{"answer": "ricerca pronta", '
        '"action": {"type": "FILL_SCAN_FORM", "keywords": ["Python"], "locations": ["Roma"]}}'
    )
    _, action, _ = _parse_llm_response(raw)
    assert action is not None
    assert action["type"] == "FILL_SCAN_FORM"


def test_parse_action_rejected_when_not_dict() -> None:
    raw = '{"answer": "ok", "action": "FILL_FORM"}'
    _, action, _ = _parse_llm_response(raw)
    assert action is None
