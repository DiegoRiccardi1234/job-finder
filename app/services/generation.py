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

from app.services.pii import redact_pii, restore_pii

if TYPE_CHECKING:
    from app.providers.factory import ProviderManager

_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts" / "generation"

# Default output budgets per artifact type (cover letters are short, a tailored
# resume can be long).
MAX_TOKENS = {
    "cover_letter": 600,
    "interview_prep": 900,
    "resume_tailoring": 1300,
    "cv_review": 1200,
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
    json_output: bool = True,
) -> str:
    """Assemble the full LLM prompt for a generation task.

    Tasks with no job attached (e.g. the standalone CV review) pass an empty
    ``job_info`` and the OFFERTA block is dropped. With ``json_output=False`` the
    prompt asks for plain markdown instead of the JSON wrapper — used as a
    fallback when a model can't produce the JSON envelope.
    """
    task = _load_task(content_type)
    extra = f"{extra_block.strip()}\n" if extra_block.strip() else ""
    has_job = any(job_info.get(k) for k in ("titolo", "azienda", "descrizione"))
    if has_job:
        titolo = job_info.get("titolo", "N/A")
        azienda = job_info.get("azienda", "N/A")
        descrizione = job_info.get("descrizione", "") or ""
        descrizione_line = ("Descrizione: " + descrizione[:1800]) if descrizione else ""
        offer_block = f"OFFERTA:\nTitolo: {titolo}\nAzienda: {azienda}\n{descrizione_line}\n\n"
    else:
        offer_block = ""
    if json_output:
        tail = (
            "Non aggiungere testo extra. Rispondi SOLO con JSON valido con la chiave "
            '"content":\n{"content": "..."}'
        )
    else:
        tail = "Rispondi in testo semplice (markdown), senza JSON e senza testo extra."
    return f"{task}\n{extra}\nCV candidato:\n{profile_markdown[:3500]}\n\n{offer_block}{tail}"


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
    redact: bool = False,
    candidate_name: str | None = None,
) -> str:
    """Generate profile-tailored content for a job. Returns the text body.

    When ``redact`` is set (Privacy Mode) the CV is PII-scrubbed before the
    provider call and the candidate's own name is restored in the returned text,
    so the letter/resume the user sends still reads correctly while nothing
    identifying leaves the machine.

    Happy path asks for a JSON ``{"content": ...}`` envelope. Weak/free models
    sometimes reply in prose ("Nessun JSON trovato"); rather than fail with a
    502, we retry once as a plain-text ``chat`` call and use that text directly.
    A genuine failure (no provider, network) still propagates from the fallback.
    """
    token_map: dict[str, str] = {}
    if redact:
        profile_markdown, token_map = redact_pii(profile_markdown, candidate_name)
    budget = max_tokens if max_tokens is not None else MAX_TOKENS.get(content_type, 700)
    try:
        prompt = build_prompt(content_type, profile_markdown, job_info, extra_block=extra_block)
        result = provider_manager.complete_json(prompt=prompt, max_tokens=budget)
        content = _extract_content(result, content_type)
    except Exception:
        prose_prompt = build_prompt(
            content_type, profile_markdown, job_info, extra_block=extra_block, json_output=False
        )
        content = provider_manager.chat(
            [{"role": "user", "content": prose_prompt}], max_tokens=budget
        )
    return restore_pii(content, token_map)
