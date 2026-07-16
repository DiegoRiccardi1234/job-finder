"""PII redaction (Privacy Mode) — the util plus its use in scoring/generation."""

from __future__ import annotations

from typing import Any

from app.services.pii import NAME_TOKEN, redact_pii, restore_pii


def test_redacts_email_phone_address_url() -> None:
    text = (
        "Mario Rossi\n"
        "mario.rossi@example.com | +39 347 1234567\n"
        "Via Roma 10, Milano\n"
        "Portfolio: https://mysite.dev/cv"
    )
    red, tmap = redact_pii(text, "Mario Rossi")
    assert "mario.rossi@example.com" not in red and "[EMAIL]" in red
    assert "347 1234567" not in red and "[PHONE]" in red
    assert "[ADDRESS]" in red and "Via Roma 10" not in red
    assert "mysite.dev" not in red and "[URL]" in red
    assert "Mario Rossi" not in red and NAME_TOKEN in red
    assert tmap[NAME_TOKEN] == "Mario Rossi"


def test_name_restored_in_output() -> None:
    red, tmap = redact_pii("Cordiali saluti, Mario Rossi", "Mario Rossi")
    assert NAME_TOKEN in red
    assert restore_pii(red, tmap) == "Cordiali saluti, Mario Rossi"


def test_short_number_and_date_range_not_redacted() -> None:
    # 8-digit year range must survive (only >= 9 digit runs read as phones).
    red, _ = redact_pii("Esperienza 2016 - 2020 presso Acme", None)
    assert "2016 - 2020" in red
    assert "[PHONE]" not in red


def test_contacts_redacted_without_name() -> None:
    red, tmap = redact_pii("write to test@x.io", None)
    assert "[EMAIL]" in red
    assert tmap == {}


def test_restore_is_noop_without_map() -> None:
    assert restore_pii("hello", {}) == "hello"


# Long enough to clear MIN_DESCRIPTION_CHARS so analyze_offer reaches the LLM.
_JD = "Sviluppo frontend React, TypeScript e REST API in team agile. " * 6


class _CapturePM:
    """Fake provider manager that records the prompt it is handed."""

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.prompt = ""

    def complete_json(self, prompt: str, max_tokens: int = 500, **_: Any) -> dict[str, Any]:
        self.prompt = prompt
        return dict(self.response)


def test_analyze_offer_privacy_scrubs_prompt() -> None:
    from app.services.scanner_service import analyze_offer

    pm = _CapturePM({"punteggio": 6})
    analyze_offer(
        pm,
        "Mario Rossi\nmario@x.io",
        "T",
        "A",
        _JD,
        privacy=True,
        candidate_name="Mario Rossi",
    )
    assert "mario@x.io" not in pm.prompt and "[EMAIL]" in pm.prompt
    assert "Mario Rossi" not in pm.prompt


def test_analyze_offer_injects_extra_context() -> None:
    from app.services.scanner_service import analyze_offer

    pm = _CapturePM({"punteggio": 5})
    analyze_offer(pm, "CV", "T", "A", _JD, extra_context="Settore target: fintech")
    assert "Settore target: fintech" in pm.prompt
    assert "PREFERENZE CANDIDATO" in pm.prompt


def test_generate_with_profile_redacts_input_restores_name() -> None:
    from app.services.generation import generate_with_profile

    class PM:
        def __init__(self) -> None:
            self.prompt = ""

        def complete_json(self, prompt: str, max_tokens: int = 700, **_: Any) -> dict[str, str]:
            self.prompt = prompt
            return {"content": "Best regards, [[CV_NAME]]"}

    pm = PM()
    out = generate_with_profile(
        pm,
        "cover_letter",
        "Mario Rossi\nmario@x.io",
        {"titolo": "T"},
        redact=True,
        candidate_name="Mario Rossi",
    )
    assert out == "Best regards, Mario Rossi"  # restored in output
    assert "mario@x.io" not in pm.prompt and "[EMAIL]" in pm.prompt  # scrubbed on input
