"""Unit tests for the shared profile-aware generation service."""

from __future__ import annotations

from typing import Any

from app.services.generation import build_prompt, generate_with_profile


class FakeProviderManager:
    """Minimal stub exposing the one method generation.py calls."""

    def __init__(
        self, json_response: dict[str, Any] | None = None, raise_on_json: bool = False
    ) -> None:
        self.json_response = json_response or {"content": "stub"}
        self.raise_on_json = raise_on_json
        self.json_calls: list[dict[str, Any]] = []

    def complete_json(self, prompt: str, max_tokens: int = 700, **kwargs: Any) -> dict[str, Any]:
        self.json_calls.append({"prompt": prompt, "max_tokens": max_tokens})
        if self.raise_on_json:
            raise RuntimeError("fake provider json failure")
        return dict(self.json_response)


def test_build_prompt_includes_cv_and_offer() -> None:
    prompt = build_prompt(
        "interview_prep",
        "# Diego\nPython, SQL",
        {"titolo": "Backend Dev", "azienda": "Acme", "descrizione": "Build APIs"},
    )
    assert "Python, SQL" in prompt
    assert "Backend Dev" in prompt
    assert "Acme" in prompt
    assert "Build APIs" in prompt
    assert '"content"' in prompt  # JSON output instruction is appended


def test_build_prompt_extra_block_and_empty_description() -> None:
    prompt = build_prompt(
        "cover_letter",
        "CV",
        {"titolo": "T", "azienda": "A", "descrizione": ""},
        extra_block="DESTINATARIO: Mario",
    )
    assert "DESTINATARIO: Mario" in prompt
    assert "Descrizione:" not in prompt  # omitted when empty


def test_generate_with_profile_returns_content() -> None:
    fake = FakeProviderManager(json_response={"content": "Generated body"})
    out = generate_with_profile(fake, "interview_prep", "CV", {"titolo": "T"})
    assert out == "Generated body"
    assert fake.json_calls and fake.json_calls[0]["max_tokens"] == 900


def test_generate_with_profile_falls_back_to_first_value() -> None:
    fake = FakeProviderManager(json_response={"cover_letter": "Legacy key"})
    out = generate_with_profile(fake, "cover_letter", "CV", {"titolo": "T"})
    assert out == "Legacy key"


def test_generate_with_profile_propagates_errors() -> None:
    fake = FakeProviderManager(raise_on_json=True)
    try:
        generate_with_profile(fake, "cover_letter", "CV", {"titolo": "T"})
    except RuntimeError as exc:
        assert "fake provider json failure" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected RuntimeError to propagate")
