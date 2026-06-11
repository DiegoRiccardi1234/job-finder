"""Profile-aware content generation shared across job artifacts.

One entry point builds a CV+offer prompt from a task template and asks the
active LLM for a single JSON ``{"content": ...}`` payload. Used by the
cover-letter, interview-prep and resume-tailoring endpoints so the prompt
plumbing lives in one place.

Templates hold only the *task description*; the CV / offer data and the
JSON-output instruction are assembled here in code, which keeps the literal
braces of the JSON example out of any ``str.format`` path.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.providers.factory import ProviderManager

_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts" / "generation"

# Default output budgets per artifact type (cover letters are short, a tailored
# resume can be long).
MAX_TOKENS = {
    "cover_letter": 600,
    "interview_prep": 900,
    "resume_tailoring": 1300,
}


@cache
def _load_task(content_type: str) -> str:
    path = _PROMPTS_DIR / f"{content_type}.txt"
    return path.read_text(encoding="utf-8").strip()


def build_prompt(
    content_type: str,
    profile_markdown: str,
    job_info: dict[str, Any],
    *,
    extra_block: str = "",
) -> str:
    """Assemble the full LLM prompt for a generation task."""
    task = _load_task(content_type)
    titolo = job_info.get("titolo", "N/A")
    azienda = job_info.get("azienda", "N/A")
    descrizione = job_info.get("descrizione", "") or ""
    descrizione_line = ("Descrizione: " + descrizione[:1800]) if descrizione else ""
    extra = f"{extra_block.strip()}\n" if extra_block.strip() else ""
    return (
        f"{task}\n"
        f"{extra}\n"
        f"CV candidato:\n{profile_markdown[:3500]}\n\n"
        f"OFFERTA:\nTitolo: {titolo}\nAzienda: {azienda}\n"
        f"{descrizione_line}\n\n"
        "Non aggiungere testo extra. Rispondi SOLO con JSON valido con la chiave "
        '"content":\n{"content": "..."}'
    )


def _extract_content(result: Any, content_type: str) -> str:
    if isinstance(result, dict):
        value = result.get("content") or result.get(content_type)
        if value is None and result:
            value = next(iter(result.values()), "")
        return str(value or "")
    return str(result)


def generate_with_profile(
    provider_manager: ProviderManager,
    content_type: str,
    profile_markdown: str,
    job_info: dict[str, Any],
    *,
    extra_block: str = "",
    max_tokens: int | None = None,
) -> str:
    """Generate profile-tailored content for a job. Returns the text body.

    Raises whatever ``provider_manager.complete_json`` raises (no provider,
    network error) so callers can decide how to surface failures.
    """
    prompt = build_prompt(content_type, profile_markdown, job_info, extra_block=extra_block)
    budget = max_tokens if max_tokens is not None else MAX_TOKENS.get(content_type, 700)
    result = provider_manager.complete_json(prompt=prompt, max_tokens=budget)
    return _extract_content(result, content_type)
