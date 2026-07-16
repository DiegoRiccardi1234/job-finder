import json
import math
import random
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

# Common technical keywords. If a scrape for one of these returns zero rows,
# it's suspicious — likely a DOM/selector regression on the scraper side
# rather than an actual lack of listings.
_COMMON_CANARY_TERMS = {
    "python",
    "java",
    "javascript",
    "sql",
    "react",
    "data analyst",
    "data engineer",
    "devops",
    "qa tester",
    "cloud engineer",
}


def _is_common_term(term: str) -> bool:
    return term.strip().lower() in _COMMON_CANARY_TERMS


# Map UI experience-level codes to keyword augmentation. We append a token
# to the search term so LinkedIn's relevance algorithm narrows results,
# since jobspy doesn't expose LinkedIn's f_E URL filter directly.
_EXPERIENCE_KEYWORDS: dict[str, str] = {
    "internship": "internship",
    "entry": "entry level",
    "junior": "junior",
    "mid": "",
    "senior": "senior",
    "director": "director",
    "executive": "executive",
}

# jobspy supports a single ``job_type`` kwarg (string). Map UI codes onto it.
_JOBSPY_JOB_TYPE: dict[str, str] = {
    "fulltime": "fulltime",
    "parttime": "parttime",
    "contract": "contract",
    "temporary": "temporary",
    "internship": "internship",
}


def _resolve_jobspy_job_type(job_types: list[str]) -> str | None:
    """Pick the single ``job_type`` to pass to jobspy.

    jobspy accepts only one type. A single selection narrows the scrape; with
    multiple selections we must NOT silently drop to the first (that would hide
    the other chosen types) — return ``None`` so jobspy returns all types, a
    superset of what the user picked.
    """
    mapped = [_JOBSPY_JOB_TYPE[j.lower()] for j in job_types if j.lower() in _JOBSPY_JOB_TYPE]
    return mapped[0] if len(mapped) == 1 else None


def _below_min_salary(max_amount: Any, min_salary: int) -> bool:
    """True only when a job's (known) top salary is below ``min_salary``.

    Jobs with no/unparseable salary are kept (return False) — most listings omit
    pay, so filtering them out would hide almost everything.
    """
    if not min_salary:
        return False
    try:
        amount = float(max_amount)
    except (TypeError, ValueError):
        return False
    return amount < min_salary


def _is_nan(val: Any) -> bool:
    return isinstance(val, float) and math.isnan(val)


def _clean_text(val: Any) -> str:
    """Coerce a jobspy cell to a clean string. ``str()`` of a pandas ``NaN`` or
    ``None`` yields the literal ``"nan"``/``"None"`` which then poisons the LLM
    prompt (and made LinkedIn jobs look like they had a description). Those and
    blank strings collapse to ``""``."""
    if val is None or _is_nan(val):
        return ""
    s = str(val).strip()
    return "" if s.lower() in ("nan", "none") else s


# Bilingual markers for the "requirements" section of a job posting. Used to keep
# that section in the scoring prompt even when it sits past the char budget.
_REQ_MARKERS = (
    "requisiti",
    "requirements",
    "cosa cerchiamo",
    "chi cerchiamo",
    "chi sei",
    "profilo ricercato",
    "profilo ideale",
    "your profile",
    "who you are",
    "qualifiche",
    "competenze richieste",
    "what we",
    "what you",
    "must have",
    "esperienza richiesta",
    "we are looking",
)


def _prep_description(desc: str, limit: int, head: int = 800) -> str:
    """Fit a job description into ``limit`` chars for the scoring prompt WITHOUT
    dropping the requirements. A plain ``desc[:limit]`` cuts off the "Requisiti"
    block (which sits after the intro/responsibilities), so a Master/PhD role was
    scored as junior. When the requirements marker falls beyond ``limit`` we keep
    a head slice + the requirements window instead of the head alone."""
    if not desc or len(desc) <= limit:
        return desc
    low = desc.lower()
    pos = min((low.find(m) for m in _REQ_MARKERS if low.find(m) >= 0), default=-1)
    if pos < 0 or pos + 40 <= limit:
        return desc[:limit]  # requirements already inside the window (or none found)
    head_part = desc[:head].rstrip()
    tail = desc[pos : pos + max(0, limit - len(head_part) - 3)]
    return f"{head_part}\n…\n{tail}"


def _norm_remote(val: Any) -> bool | None:
    """Normalize jobspy's ``is_remote`` (True/False/NaN/missing) to bool|None."""
    if val is None or _is_nan(val):
        return None
    return bool(val)


def _row_job_type_ok(row: Any, job_types: list[str]) -> bool:
    """Keep a scraped row when its job_type matches a selected one.

    jobspy takes a single ``job_type``, so multi-select is enforced here (the
    kwarg only narrows for a single pick). Rows whose type jobspy didn't report
    are kept — never over-drop on missing data.
    """
    selected = {j.lower() for j in job_types if j.lower() in _JOBSPY_JOB_TYPE}
    if not selected:
        return True
    raw = row.get("job_type")
    if raw is None or _is_nan(raw):
        return True
    types = {t.strip().lower() for t in re.split(r"[,\s]+", str(raw)) if t.strip()}
    return not types or bool(types & selected)


def _row_work_mode_ok(row: Any, work_types: list[str]) -> bool:
    """Best-effort work-mode filter from jobspy's ``is_remote``.

    'hybrid' isn't distinguishable in jobspy output, so any selection including
    it (or both remote+onsite) keeps everything. Unknown is_remote is kept.
    """
    modes = {w.lower() for w in work_types}
    if not modes or "hybrid" in modes or {"remote", "onsite"} <= modes:
        return True
    is_remote = _norm_remote(row.get("is_remote"))
    if is_remote is None:
        return True
    if "remote" in modes:
        return is_remote
    if "onsite" in modes:
        return not is_remote
    return True


def _augment_search_term(term: str, exp_levels: list[str], work_types: list[str]) -> str:
    bits = [term]
    for lvl in exp_levels:
        kw = _EXPERIENCE_KEYWORDS.get(lvl, "")
        if kw and kw not in term.lower():
            bits.append(kw)
    if "hybrid" in work_types and "hybrid" not in term.lower():
        bits.append("hybrid")
    return " ".join(bits).strip()


from app.config import AppSettings
from app.db import Database
from app.lifecycle import apply_post_scan_lifecycle
from app.log import get_logger
from app.models import ScanRequest
from app.providers.factory import ProviderManager
from app.providers.model_selector import SCORING_MIN_SIZE_B
from app.services.onboarding import onboarding_context
from app.services.pii import redact_pii
from app.services.recruiter_scrape import fetch_linkedin_description, fetch_recruiter

log = get_logger(__name__)

# Speed-biased model policy for scan scoring. Scoring a job (0-10 + JSON) barely
# needs model quality but is high-volume, so we bias hard toward fast/small
# models (flash/turbo/instant, size-penalised) and drop the global preferred_model
# pin. Passed as ``policy_override`` so it re-ranks the provider's LIVE catalog —
# no hardcoded model id, so it survives OpenRouter's changing catalog. Chat and
# letter generation keep the quality default (they don't pass this).
_SCORING_POLICY: dict[str, Any] = {
    # "Fastest among CAPABLE models." A pure speed bias picked 1-20B toys that
    # scored job↔CV matches badly (a Product Owner at 8/10, empty analyses), so
    # we keep a fast lean but with a quality floor: de-rank models under
    # SCORING_MIN_SIZE_B, and reward capability again. The floor is 26 (not 40)
    # so clean mid-size models like gemma-4-26b stay eligible — the 40B floor was
    # de-ranking the models that emit JSON reliably and favouring 120-550B
    # reasoning giants that truncate it. ``reasoning`` weight is 0: for compact
    # JSON scoring a hidden chain-of-thought is a liability, not a plus (and the
    # runtime "truncated" penalty routes around whichever models actually cut off).
    "prefer_fast": True,
    "prefer_quality": True,
    "prefer_free": True,
    "max_cost_tier": "high",
    "min_size_b": SCORING_MIN_SIZE_B,
    "weights": {
        "size": 16,
        "speed": 12,
        "family": 20,
        "instruct": 25,
        "chat": 10,
        "json": 12,
        "reasoning": 0,
        "small_penalty": -150,
    },
}

# Cap on locations per scan: a scan is terms x locations x ~20 jobs, so this
# bounds the volume (and the free-tier LLM scoring time) for a multi-location run.
_MAX_SCAN_LOCATIONS = 8

try:
    from jobspy import scrape_jobs
except ImportError:  # pragma: no cover
    scrape_jobs = None


BLACKLIST = [
    "senior developer",
    "senior engineer",
    "senior consultant",
    "senior analyst",
    "lead developer",
    "principal engineer",
    "5+ anni",
    "4+ anni",
    "partita iva",
    "p.iva",
    "freelance",
    "cto",
    "ciso",
]

STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "have",
    "has",
    "will",
    "your",
    "you",
    "our",
    "all",
    "per",
    "con",
    "dei",
    "delle",
    "della",
    "dell",
    "una",
    "uno",
    "sono",
    "come",
    "sulla",
    "sulle",
    "degli",
    "nella",
    "nelle",
    "into",
    "about",
    "role",
    "lavoro",
    "lavori",
    "offerta",
    "annuncio",
    "candidate",
    "team",
    "company",
}

TECH_KEYWORDS = {
    "python",
    "java",
    "javascript",
    "typescript",
    "react",
    "node",
    "sql",
    "docker",
    "kubernetes",
    "aws",
    "azure",
    "gcp",
    "api",
    "fastapi",
    "django",
    "selenium",
    "playwright",
    "testing",
    "qa",
    "data",
    "analytics",
    "machine",
    "learning",
}

# Domain vocabulary (bilingual it+en) for the relevance gate: a scraped job whose
# title+description shares NONE of these — nor any of the candidate's own skill
# tokens — is off-topic (kitchen/spa/food-QC/pharma) and dropped before wasting an
# LLM scoring call. Deliberately excludes generic role words (analyst/engineer/
# quality) so it keys on the actual tech/AI/data domain, not the fuzzy match.
_DOMAIN_VOCAB = {
    # AI / data / ML
    "ai",
    "ml",
    "nlp",
    "llm",
    "genai",
    "data",
    "dati",
    "dataset",
    "analytics",
    "analisi",
    "machine",
    "learning",
    "apprendimento",
    "deep",
    "neural",
    "rete",
    "reti",
    "model",
    "modelli",
    "modello",
    "algorithm",
    "algoritmo",
    "algoritmi",
    "intelligenza",
    "artificiale",
    "annotation",
    "annotazione",
    "labeling",
    "etichettatura",
    "linguistic",
    "linguistica",
    "linguistico",
    "computational",
    "computazionale",
    "prompt",
    "embedding",
    # software / dev
    "software",
    "sviluppo",
    "sviluppatore",
    "developer",
    "development",
    "programmazione",
    "programming",
    "coding",
    "informatica",
    "informatico",
    "backend",
    "frontend",
    "fullstack",
    "api",
    "database",
    "cloud",
    "devops",
    "python",
    "java",
    "javascript",
    "typescript",
    "react",
    "node",
    "sql",
    "docker",
}


def pre_filtro(titolo: str, descrizione: str) -> tuple[bool, str]:
    testo = (titolo + " " + descrizione).lower()
    for frase in BLACKLIST:
        if frase in testo:
            return True, frase
    return False, ""


# Per-offer analysis schema, shared by the single-offer prompt and the batch
# prompt so both stay identical (job_detail.js depends on this exact shape:
# radar match_axes, skills_match, requisiti…). Plain string with literal braces
# — inserted by concatenation, so no f-string escaping.
_PER_OFFER_SCHEMA = """{
  "punteggio": <1-10>,
  "programmazione_richiesta": "Bassa|Media|Alta",
  "smart_working": "Sì|No|Non specificato",
  "contratto": "Dipendente|Apprendistato|Stage|Partita IVA|Non specificato",
  "junior_friendly": "Sì|No|Non specificato",
  "anni_esperienza_richiesti": "0|1|2|3+|Non specificato",
  "punti_forza_per_diego": "1 frase",
  "punti_deboli_per_diego": "1 frase",
  "riassunto": "2 righe max",
  "consiglio": "Candidati subito|Valutabile|Salta",
  "ral_stimata": "XX.000€-YY.000€|Non stimabile",
  "reputazione_azienda": "Ottima|Buona|Nella media|Scarsa|Sconosciuta",
  "adatta_neolaureati": "Sì|No|Non specificato",
  "note_azienda": "1 frase",
  "requisiti": ["max 5 requisiti chiave dell'offerta, brevi"],
  "responsabilita": ["max 5 responsabilità principali, brevi"],
  "benefit": ["max 5 benefit menzionati, brevi"],
  "skills_match": {
    "hai": ["skills che il candidato ha e l'offerta richiede"],
    "mancano": ["skills richieste che il candidato non ha"]
  },
  "livello_richiesto": "internship|entry|junior|mid|senior|lead",
  "match_axes": {
    "skills_match": <0-10>,
    "seniority_match": <0-10>,
    "remote_match": <0-10>,
    "salary_match": <0-10>,
    "contract_match": <0-10>
  }
}"""


def _analysis_prompt(
    profile_markdown: str,
    titolo: str,
    azienda: str,
    descrizione: str,
    extra_context: str = "",
) -> str:
    extra = f"\nPREFERENZE CANDIDATO:\n{extra_context}\n" if extra_context.strip() else ""
    return (
        "Analizza questa offerta IT e rispondi SOLO con JSON valido, senza testo extra.\n\n"
        f"CV candidato:\n{profile_markdown[:3500]}\n{extra}\n"
        f"OFFERTA:\nTitolo: {titolo}\nAzienda: {azienda}\n"
        f"Descrizione: {_prep_description(descrizione, 2600)}\n\n"
        "JSON richiesto:\n" + _PER_OFFER_SCHEMA + "\n"
    )


def _batch_analysis_prompt(
    profile_markdown: str,
    offers: list[dict[str, Any]],
    extra_context: str = "",
) -> str:
    """Prompt for scoring N offers for the SAME candidate in one LLM call.

    The CV is sent once; offers are numbered 1..N; the model must return a JSON
    object ``{"valutazioni": [ ...N objects... ]}`` in the same order, each with
    the per-offer schema. Wrapped in an object (not a bare array) so the shared
    ``complete_json`` extractor returns a dict as everywhere else.
    """
    extra = f"\nPREFERENZE CANDIDATO:\n{extra_context}\n" if extra_context.strip() else ""
    n = len(offers)
    blocks = [
        f"--- OFFERTA {i} ---\n"
        f"Titolo: {off['titolo']}\nAzienda: {off['azienda']}\n"
        f"Descrizione: {_prep_description(str(off['descrizione']), 2200)}"
        for i, off in enumerate(offers, 1)
    ]
    offers_text = "\n\n".join(blocks)
    return (
        f"Analizza le {n} offerte IT qui sotto per lo STESSO candidato e rispondi "
        "SOLO con JSON valido, senza testo extra.\n\n"
        f"CV candidato:\n{profile_markdown[:3500]}\n{extra}\n"
        f"OFFERTE ({n}):\n{offers_text}\n\n"
        f'Rispondi con un oggetto JSON con una sola chiave "valutazioni" = array di '
        f"ESATTAMENTE {n} oggetti, UNO per offerta nello STESSO ordine "
        "(OFFERTA 1 -> primo elemento). Ogni oggetto ha questo schema:\n" + _PER_OFFER_SCHEMA + "\n"
    )


def _tokenize(text: str) -> set[str]:
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9+#\.-]{2,}", text.lower())
    return {token for token in tokens if token not in STOPWORDS}


def _estimate_programming_demand(offer_text: str) -> str:
    hits = sum(1 for kw in TECH_KEYWORDS if kw in offer_text)
    if hits >= 5:
        return "Alta"
    if hits >= 2:
        return "Media"
    return "Bassa"


def _estimate_experience_band(offer_text: str) -> str:
    years_match = re.search(r"(\d+)\s*\+?\s*(?:anni|years)", offer_text)
    if years_match:
        years = int(years_match.group(1))
        if years <= 0:
            return "0"
        if years == 1:
            return "1"
        if years == 2:
            return "2"
        return "3+"

    if any(
        token in offer_text for token in ["junior", "entry level", "neolaureat", "stage", "intern"]
    ):
        return "0"
    return "Non specificato"


def _estimate_contract_type(offer_text: str) -> str:
    if any(token in offer_text for token in ["apprendistat", "apprenticeship"]):
        return "Apprendistato"
    if any(token in offer_text for token in ["stage", "intern"]):
        return "Stage"
    if any(token in offer_text for token in ["partita iva", "p.iva", "freelance", "contractor"]):
        return "Partita IVA"
    if any(
        token in offer_text
        for token in ["tempo indeterminato", "full-time", "dipendente", "permanent"]
    ):
        return "Dipendente"
    return "Non specificato"


def _estimate_smart_working(offer_text: str) -> str:
    if any(
        token in offer_text
        for token in ["remote", "full remote", "smart working", "hybrid", "ibrid"]
    ):
        return "Sì"
    if any(token in offer_text for token in ["on-site", "onsite", "in office"]):
        return "No"
    return "Non specificato"


def _fallback_analysis(
    reason: str,
    profile_markdown: str,
    titolo: str,
    azienda: str,
    descrizione: str,
) -> dict[str, Any]:
    # ``reason`` (provider error / invalid response) is for diagnostics only —
    # it must never leak into the user-facing ``riassunto`` below.
    log.warning("Heuristic fallback analysis for '%s' @ %s: %s", titolo, azienda, reason)
    offer_text = f"{titolo} {descrizione}".lower()
    profile_tokens = _tokenize(profile_markdown)
    offer_tokens = _tokenize(offer_text)
    overlap = sorted(profile_tokens.intersection(offer_tokens))

    score = 3 + min(4, len(overlap) // 3)
    if "junior" in offer_text or "entry level" in offer_text:
        score += 2
    if any(token in offer_text for token in ["remote", "hybrid", "smart working"]):
        score += 1
    if any(token in offer_text for token in ["senior", "lead", "principal", "staff"]):
        score -= 2

    score = max(1, min(score, 10))

    if score >= 8:
        advice = "Candidati subito"
    elif score >= 6:
        advice = "Valutabile"
    else:
        advice = "Salta"

    overlap_preview = ", ".join(overlap[:5]) if overlap else "competenze base IT"
    weakness_text = (
        "Richieste non completamente allineate al profilo"
        if score < 7
        else "Competenze verificabili in colloquio"
    )

    return {
        "punteggio": score,
        "programmazione_richiesta": _estimate_programming_demand(offer_text),
        "smart_working": _estimate_smart_working(offer_text),
        "contratto": _estimate_contract_type(offer_text),
        "junior_friendly": "Sì"
        if any(token in offer_text for token in ["junior", "entry", "stage", "intern"])
        else "Non specificato",
        "anni_esperienza_richiesti": _estimate_experience_band(offer_text),
        "punti_forza_per_diego": f"Match su: {overlap_preview}.",
        "punti_deboli_per_diego": weakness_text,
        "riassunto": f"Analisi euristica usata (IA non disponibile). Match stimato {score}/10.",
        "consiglio": advice,
        "ral_stimata": "Non stimabile",
        "reputazione_azienda": "Sconosciuta",
        "adatta_neolaureati": "Sì"
        if any(token in offer_text for token in ["junior", "stage", "intern", "entry"])
        else "Non specificato",
        "note_azienda": f"Valutazione automatica fallback per {azienda}.",
        "match_axes": {
            "skills_match": max(0, min(10, score + min(2, len(overlap) // 2))),
            "seniority_match": 8
            if any(t in offer_text for t in ["junior", "entry", "stage", "intern"])
            else (3 if any(t in offer_text for t in ["senior", "lead"]) else 6),
            "remote_match": 9
            if any(t in offer_text for t in ["remote", "smart working"])
            else (6 if "hybrid" in offer_text or "ibrid" in offer_text else 4),
            "salary_match": 5,
            "contract_match": 3 if any(t in offer_text for t in ["partita iva", "p.iva"]) else 7,
        },
    }


# Below this many chars a description carries no requirements/seniority signal
# (real case: an 82-char marketing blurb) — LLM-scoring it just hallucinates.
# Such jobs take the honest capped path instead. Length measured post-clean.
MIN_DESCRIPTION_CHARS = 300


def _insufficient_description_analysis(
    profile_markdown: str, titolo: str, azienda: str, descrizione: str = ""
) -> dict[str, Any]:
    """A job whose description is missing or too short to judge on merit
    (LinkedIn blocked the page, or served a marketing blurb without the JD).
    Score it heuristically from the little text available so it still gets an
    ordering, but flag it honestly and CAP it — an unread job must never
    surface as a top "Candidati subito"/9. Skips the LLM (no point scoring
    blind, and it would hallucinate requirements)."""
    result = _fallback_analysis(
        "insufficient_description",
        profile_markdown=profile_markdown,
        titolo=titolo,
        azienda=azienda,
        descrizione=descrizione,
    )
    result["punteggio"] = min(int(result.get("punteggio", 3) or 3), 6)
    result["consiglio"] = "Valutabile" if result["punteggio"] >= 5 else "Salta"
    if descrizione.strip():
        result["riassunto"] = (
            "Descrizione troppo breve — stima dal titolo. Apri l'annuncio per valutare."
        )
        result["punti_deboli_per_diego"] = (
            "Descrizione quasi assente: requisiti ed esperienza richiesta non verificati."
        )
    else:
        result["riassunto"] = (
            "Descrizione non disponibile — stima dal titolo. Apri l'annuncio per valutare."
        )
        result["punti_deboli_per_diego"] = (
            "Descrizione non recuperata: requisiti ed esperienza richiesta non verificati."
        )
    return result


def _no_description_analysis(profile_markdown: str, titolo: str, azienda: str) -> dict[str, Any]:
    """Backwards-compatible wrapper: empty-description case."""
    return _insufficient_description_analysis(profile_markdown, titolo, azienda, "")


def _scoring_call_kwargs(provider_manager: ProviderManager) -> dict[str, Any]:
    """Provider/model kwargs for a scoring call: pin the user-chosen scoring
    model if set (no failover), else auto-select via the speed-biased policy.
    getattr-guarded so test stubs without ``.settings`` still work.
    """
    settings = getattr(provider_manager, "settings", None)
    scoring_model = getattr(settings, "scoring_model", None)
    if scoring_model:
        order = getattr(settings, "llm_provider_order", None) or []
        return {"provider_name": order[0] if order else None, "model_name": scoring_model}
    return {"policy_override": _SCORING_POLICY}


def analyze_offer(
    provider_manager: ProviderManager,
    profile_markdown: str,
    titolo: str,
    azienda: str,
    descrizione: str,
    *,
    privacy: bool = False,
    extra_context: str = "",
    candidate_name: str | None = None,
) -> dict[str, Any]:
    # Missing or too-short description (LinkedIn blocked the retry, or served a
    # marketing blurb) -> don't LLM-score it blind; honest capped estimate.
    if len(descrizione.strip()) < MIN_DESCRIPTION_CHARS:
        return _insufficient_description_analysis(profile_markdown, titolo, azienda, descrizione)
    # Privacy Mode: scrub the CV before it reaches the LLM. Scoring never needs
    # the name/contacts, so no restore — the token map is discarded. The local
    # keyword fallback below keeps the ORIGINAL markdown for a better match.
    prompt_markdown = profile_markdown
    if privacy:
        prompt_markdown, _ = redact_pii(profile_markdown, candidate_name)
    prompt = _analysis_prompt(prompt_markdown, titolo, azienda, descrizione, extra_context)
    try:
        result = provider_manager.complete_json(
            prompt=prompt, max_tokens=200, **_scoring_call_kwargs(provider_manager)
        )
        # A non-dict, empty dict, or dict without a score is NOT an analysis:
        # persisting it would set analyzed_at with punteggio=0 and the job
        # would never be re-scored (job_has_analysis). Same key check as the
        # batch path.
        if not isinstance(result, dict) or "punteggio" not in result:
            return _fallback_analysis(
                "invalid response",
                profile_markdown=profile_markdown,
                titolo=titolo,
                azienda=azienda,
                descrizione=descrizione,
            )
        return result
    except Exception as exc:
        return _fallback_analysis(
            str(exc),
            profile_markdown=profile_markdown,
            titolo=titolo,
            azienda=azienda,
            descrizione=descrizione,
        )


def analyze_offers_batch(
    provider_manager: ProviderManager,
    profile_markdown: str,
    offers: list[dict[str, Any]],
    *,
    privacy: bool = False,
    extra_context: str = "",
    candidate_name: str | None = None,
) -> list[dict[str, Any]]:
    """Score N offers in one LLM call, returning exactly ``len(offers)`` analyses
    in order. A batch that fails, returns non-JSON, or yields too few / invalid
    elements degrades gracefully: each missing slot is filled by a per-offer
    :func:`analyze_offer` (which itself falls back to a heuristic). Never raises.
    """
    if not offers:
        return []

    prompt_markdown = profile_markdown
    if privacy:
        prompt_markdown, _ = redact_pii(profile_markdown, candidate_name)

    parsed: list[Any] = []
    try:
        prompt = _batch_analysis_prompt(prompt_markdown, offers, extra_context)
        # Token budget = fixed reasoning headroom + per-offer output. The scoring
        # target is gpt-oss-120b, a REASONING model that spends completion tokens
        # thinking BEFORE emitting the array — a tight budget (e.g. 1k for 3
        # offers) is fully consumed by reasoning, leaving an empty completion and
        # voiding the batch. ~1600 base + 500/offer survives it (verified live:
        # 3 offers returned a full valid array at ~3k tokens, empty at ~1k).
        result = provider_manager.complete_json(
            prompt=prompt,
            max_tokens=500 * len(offers) + 1600,
            **_scoring_call_kwargs(provider_manager),
        )
        if isinstance(result, dict):
            raw = result.get("valutazioni") or result.get("evaluations") or result.get("results")
            if isinstance(raw, list):
                parsed = raw
    except Exception as exc:
        log.warning("Batch scoring failed (n=%d): %s; falling back per-offer", len(offers), exc)

    out: list[dict[str, Any]] = []
    for i, off in enumerate(offers):
        # A description-less (or near-empty) offer can't be judged on merit —
        # override whatever the batch guessed with the honest capped estimate
        # (never a blind 9). Same threshold as the single path.
        desc_i = str(off.get("descrizione", "") or "")
        if len(desc_i.strip()) < MIN_DESCRIPTION_CHARS:
            out.append(
                _insufficient_description_analysis(
                    profile_markdown, off["titolo"], off["azienda"], desc_i
                )
            )
            continue
        item = parsed[i] if i < len(parsed) else None
        if isinstance(item, dict) and "punteggio" in item:
            out.append(item)
        else:
            # Missing / malformed slot: single-offer scoring for just this one.
            out.append(
                analyze_offer(
                    provider_manager=provider_manager,
                    profile_markdown=profile_markdown,
                    titolo=off["titolo"],
                    azienda=off["azienda"],
                    descrizione=off["descrizione"],
                    privacy=privacy,
                    extra_context=extra_context,
                    candidate_name=candidate_name,
                )
            )
    return out


def run_scan(
    db: Database,
    settings: AppSettings,
    provider_manager: ProviderManager,
    payload: ScanRequest,
    cancel_check: Callable[[], bool] | None = None,
) -> Any:
    """Run a job scan and yield progress events for SSE streaming.

    For each requested search term, scrapes listings via python-jobspy,
    deduplicates, applies the pre-filter blacklist, scores the surviving jobs
    concurrently via :func:`analyze_offer` (bounded by ``scan_concurrency``),
    and persists results. Yields dicts describing status transitions
    (``scraped``, ``analyzed``, ``cancelled``, ``complete``, ``error``) that the
    caller forwards to the client as Server-Sent Events. ``cancel_check`` — when
    it returns True the run stops promptly (user hit stop / tab closed).
    """
    cancelled = cancel_check or (lambda: False)
    if scrape_jobs is None:
        yield {"error": "python-jobspy not installed"}
        return

    # Truncation penalties are sticky within a scan (long cooldown) but reset
    # between scans, so a model that recovered gets another chance next run.
    provider_manager.clear_model_penalties("truncated")

    profile = db.get_active_candidate_profile()
    profile_markdown = profile["markdown"] if profile else "Profile not loaded."
    # Relevance vocabulary = domain base + the candidate's own skill tokens. A
    # scraped job sharing none of it (kitchen/spa/food-QC…) is dropped before it
    # wastes an LLM scoring call. Conservative: zero-overlap only.
    _summary = profile.get("summary_json") if profile else None
    _skills = _summary.get("skills") if isinstance(_summary, dict) else None
    relevance_vocab = _DOMAIN_VOCAB | (
        _tokenize(" ".join(str(s) for s in _skills)) if isinstance(_skills, list) else set()
    )

    linkedin_url = db.get_preference("linkedin_url", "")
    if linkedin_url:
        profile_markdown += f"\n\nLinkedIn profile: {linkedin_url}"

    # Privacy Mode + onboarding preferences, resolved once for every job in this
    # scan. feature_privacy_mode mirrors container.feature_enabled semantics.
    privacy = db.get_preference("feature_privacy_mode", "1") not in ("0", "false", "off", "")
    onboarding = onboarding_context(db)
    candidate_name = profile.get("name") if profile else None
    log.info(
        "Scan scoring model: %s",
        settings.scoring_model
        or f"auto → {provider_manager.preview_scoring_model(_SCORING_POLICY)}",
    )

    terms = payload.search_terms or settings.default_search_terms
    exp_levels = list(payload.experience_levels or [])
    job_types = list(payload.job_types or [])
    work_types = list(payload.work_types or [])
    min_salary = int(payload.min_salary or 0)

    is_remote_effective = payload.is_remote or ("remote" in work_types)

    # Multi-location: scrape each location. Fall back to the single location (or
    # the settings default) for backward compat / saved searches. Capped to keep
    # a scan bounded (terms x locations x ~20 jobs each).
    default_location = (
        settings.location_remote_default if is_remote_effective else settings.location_default
    )
    locations_list = [loc.strip() for loc in (payload.locations or []) if loc and loc.strip()]
    if not locations_list:
        locations_list = [payload.location.strip()] if payload.location else [default_location]
    if len(locations_list) > _MAX_SCAN_LOCATIONS:
        locations_list = locations_list[:_MAX_SCAN_LOCATIONS]
    country = (payload.country or settings.country_default or "italy").strip()
    primary_location = locations_list[0]
    modalita = "Full Remote" if is_remote_effective else "In sede"

    augmented_terms = [_augment_search_term(t, exp_levels, work_types) for t in terms]

    jobspy_job_type = _resolve_jobspy_job_type(job_types)

    db.set_preference("last_scan_location", primary_location)
    db.set_preference("last_scan_locations", json.dumps(locations_list, ensure_ascii=False))
    db.set_preference("last_scan_country", country)
    db.set_preference("last_scan_is_remote", "1" if is_remote_effective else "0")
    db.set_preference("last_scan_terms", json.dumps(terms, ensure_ascii=False))
    db.set_preference(
        "last_scan_filters",
        json.dumps(
            {"experience_levels": exp_levels, "job_types": job_types, "work_types": work_types},
            ensure_ascii=False,
        ),
    )

    run_id = db.begin_scan(location=primary_location, is_remote=is_remote_effective, terms=terms)

    started_at_ms = int(time.time() * 1000)
    total_batches = max(1, len(terms) * len(locations_list))
    expected_total = max(1, total_batches * max(1, settings.max_annunci))

    yield {
        "status": "started",
        "terms": terms,
        "location": primary_location,
        "locations": locations_list,
        "country": country,
        "is_remote": is_remote_effective,
        "filters": {
            "experience_levels": exp_levels,
            "job_types": job_types,
            "work_types": work_types,
        },
        "expected_total": expected_total,
    }

    totale_trovati = 0
    totale_nuovi = 0
    totale_analizzati = 0
    totale_scartati = 0
    new_flags_cleared = False

    def _finalize_scored(item: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
        """Attach salary, coerce the score to int, best-effort recruiter fetch.
        Shared by the single- and batch-scoring paths. Runs on a worker thread;
        ALL DB access (reads included) happens back on the generator thread —
        the recruiter presence check is precomputed in Pass 1 (has_recruiter)."""
        row = item["row"]
        analysis = dict(analysis)
        analysis["stipendio_min"] = row.get("min_amount") or "N/D"
        analysis["stipendio_max"] = row.get("max_amount") or "N/D"
        raw_score = analysis.get("punteggio", 0)
        try:
            analysis["punteggio"] = int(raw_score)
        except (TypeError, ValueError):
            numbers = re.findall(r"\d+", str(raw_score))
            analysis["punteggio"] = int(numbers[0]) if numbers else 0

        recruiter = None
        link = item["link"]
        if link and "linkedin.com" in link and not item.get("has_recruiter"):
            try:
                recruiter = fetch_recruiter(link, timeout=3.0)
            except Exception as exc:
                log.debug("recruiter scrape skipped for job %s: %s", item["job_id"], exc)
        return {
            "job_id": item["job_id"],
            "titolo": item["titolo"],
            "azienda": item["azienda"],
            "analysis": analysis,
            "recruiter": recruiter,
        }

    def _score_job(item: dict[str, Any]) -> dict[str, Any]:
        """Score one offer (one LLM call) + finalize."""
        analysis = analyze_offer(
            provider_manager=provider_manager,
            profile_markdown=profile_markdown,
            titolo=item["titolo"],
            azienda=item["azienda"],
            descrizione=item["descrizione"],
            privacy=privacy,
            extra_context=onboarding,
            candidate_name=candidate_name,
        )
        return _finalize_scored(item, analysis)

    def _score_batch(chunk: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Score a chunk of offers in one LLM call (per-offer fallback inside
        analyze_offers_batch) + finalize each. Returns one result per offer."""
        analyses = analyze_offers_batch(
            provider_manager=provider_manager,
            profile_markdown=profile_markdown,
            offers=chunk,
            privacy=privacy,
            extra_context=onboarding,
            candidate_name=candidate_name,
        )
        return [
            _finalize_scored(item, analysis) for item, analysis in zip(chunk, analyses, strict=True)
        ]

    def _score_unit(unit: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Score a work unit: >1 offer → one batched call; a lone offer → single
        call (avoids wasting a batch prompt on the last odd job)."""
        if len(unit) > 1:
            return _score_batch(unit)
        return [_score_job(unit[0])]

    # Flat (location, term) pairs so the existing single-loop body stays intact;
    # batch_no drives global progress across the whole location x term grid.
    scan_pairs = [(loc, ti, term) for loc in locations_list for ti, term in enumerate(terms)]
    for batch_no, (location, idx, term) in enumerate(scan_pairs):
        if cancelled():
            break
        if batch_no > 0:
            time.sleep(random.uniform(0.8, 2.4))

        effective_term = augmented_terms[idx] if idx < len(augmented_terms) else term

        elapsed_ms = int(time.time() * 1000) - started_at_ms
        seen = batch_no * max(1, settings.max_annunci)
        eta_ms = int((elapsed_ms / max(1, seen)) * (expected_total - seen)) if seen > 0 else 0
        yield {
            "status": "progress",
            "step": "scraping",
            "term": effective_term,
            "current": seen,
            "total": expected_total,
            "percent": int(seen * 100 / expected_total) if expected_total else 0,
            "elapsed_ms": elapsed_ms,
            "eta_ms": eta_ms,
        }

        scrape_kwargs: dict[str, Any] = {
            "site_name": payload.sites,
            "search_term": effective_term,
            "location": location,
            "is_remote": is_remote_effective,
            "results_wanted": settings.max_annunci,
            "hours_old": settings.hours_old,
            "country_indeed": country,
        }
        if jobspy_job_type:
            scrape_kwargs["job_type"] = jobspy_job_type
        # LinkedIn's search API returns only job cards (no description); the text
        # lives on each job's own page. Without this jobspy leaves LinkedIn jobs
        # description-less and the AI scores them blind (title only). Costs one
        # extra fetch per job (~1.6s); Indeed already includes descriptions.
        if "linkedin" in payload.sites:
            scrape_kwargs["linkedin_fetch_description"] = True

        try:
            df = scrape_jobs(**scrape_kwargs)
        except Exception as exc:
            log.warning(
                "scrape_jobs failed (term=%r, location=%r): %s", effective_term, location, exc
            )
            yield {"status": "scrape_error", "term": effective_term, "error": str(exc)}
            continue

        df = df.drop_duplicates(subset=["title", "company"])
        total_rows = len(df)
        totale_trovati += total_rows

        if total_rows == 0 and _is_common_term(term):
            log.warning(
                "SCRAPER_EMPTY_BUT_EXPECTED: term=%r location=%r returned 0 rows. "
                "Possible DOM/selector regression upstream.",
                term,
                location,
            )
            yield {
                "status": "canary_warning",
                "term": term,
                "message": (
                    "The search returned 0 results for a common keyword. "
                    "The source site may have changed its layout — please report this."
                ),
            }

        yield {
            "status": "scraped",
            "term": term,
            "found": total_rows,
            "site": ", ".join(payload.sites),
        }

        # Clear the previous run's "new" badges only now that a scrape actually
        # returned rows (a fully-failed scan must not wipe them — see F-3).
        if total_rows > 0 and not new_flags_cleared:
            db.clear_new_flags()
            new_flags_cleared = True

        # Pass 1 (serial, DB-only): filter + upsert, collect jobs needing scoring.
        to_score: list[dict[str, Any]] = []
        for _, row in df.iterrows():
            titolo = _clean_text(row.get("title")) or "N/A"
            azienda = _clean_text(row.get("company")) or "N/A"
            descrizione = _clean_text(row.get("description"))
            fonte = _clean_text(row.get("site"))
            link = _clean_text(row.get("job_url"))
            # LinkedIn's per-job description fetch occasionally 429s on a single
            # job, leaving it description-less; retry that one page (spaced out
            # from jobspy's burst, so the transient throttle has usually cleared).
            if not descrizione and fonte == "linkedin" and link:
                descrizione = fetch_linkedin_description(link)

            # Relevance gate: drop a job whose text shares NOTHING with the
            # candidate's domain/skills (e.g. a spa kitchen helper matched only
            # because the search term said "QC"). Only fires on a non-empty
            # description with zero overlap — conservative, and logged.
            # A too-short description is judged by TITLE only: a stray domain
            # token in an 82-char marketing blurb must not save an off-topic
            # job, nor its missing tokens condemn a good one. Fully empty
            # descriptions stay exempt (LinkedIn 429: we read nothing — the
            # capped estimate the user can inspect beats a silent drop).
            desc_sufficient = len(descrizione) >= MIN_DESCRIPTION_CHARS
            gate_text = f"{titolo} {descrizione}" if desc_sufficient else titolo
            if descrizione and relevance_vocab and not (_tokenize(gate_text) & relevance_vocab):
                totale_scartati += 1
                log.info(
                    "RELEVANCE_SKIP%s: '%s' @ %s (zero domain/skills overlap)",
                    "" if desc_sufficient else " (title-only)",
                    titolo,
                    azienda,
                )
                continue

            skip, _reason = pre_filtro(titolo=titolo, descrizione=descrizione)
            if skip:
                totale_scartati += 1
                continue

            if _below_min_salary(row.get("max_amount"), min_salary):
                totale_scartati += 1
                continue

            # Post-scrape filters for selections jobspy can't honor natively
            # (multiple job_types, on-site work mode). Missing fields are kept.
            if not _row_job_type_ok(row, job_types):
                totale_scartati += 1
                continue
            if not _row_work_mode_ok(row, work_types):
                totale_scartati += 1
                continue

            payload_job = {
                "titolo": titolo,
                "azienda": azienda,
                "descrizione": descrizione,
                "sede": _clean_text(row.get("location")),
                "fonte": fonte,
                "link": link,
                "ricerca_usata": term,
                "modalita": modalita,
            }
            job_id, is_new, status = db.upsert_job(payload_job)

            if is_new:
                totale_nuovi += 1

            # Skip re-analysis if the user already closed the job.
            if status in {"applied", "rejected", "archived"}:
                continue

            # A brand-new job has no analysis yet; only existing ones need the
            # (lightweight) check — avoids a full get_job() per scraped row.
            if not is_new and db.job_has_analysis(job_id):
                continue

            link = payload_job.get("link") or ""
            to_score.append(
                {
                    "job_id": job_id,
                    "titolo": titolo,
                    "azienda": azienda,
                    "descrizione": descrizione,
                    "row": row,
                    "link": link,
                    # DB read done here on the generator thread so workers in
                    # _finalize_scored never touch the shared connection.
                    "has_recruiter": bool(
                        link and "linkedin.com" in link and db.get_recruiter(job_id)
                    ),
                }
            )

        # Pass 2 (concurrent): score surviving jobs, emit each as it resolves.
        # Jobs are chunked into work units of ``scan_batch_size`` (default >1 =
        # one LLM call per N offers → fewer free-tier 429s, faster); each unit is
        # one future. Concurrency bounds concurrent LLM *calls*, so batching cuts
        # total calls. A drained unit yields one "analyzed" event per offer.
        if to_score and not cancelled():
            batch_size = max(1, settings.scan_batch_size)
            units = [to_score[i : i + batch_size] for i in range(0, len(to_score), batch_size)]
            workers = max(1, min(settings.scan_concurrency, len(units)))
            pool = ThreadPoolExecutor(max_workers=workers)
            try:
                futures = {pool.submit(_score_unit, unit): unit for unit in units}
                for fut in as_completed(futures):
                    if cancelled():
                        break
                    try:
                        results = fut.result()
                    except Exception as exc:  # analyze_offer(s) degrade internally
                        log.warning("scoring task failed: %s", exc)
                        continue
                    for result in results:
                        db.update_job_analysis(job_id=result["job_id"], analysis=result["analysis"])
                        if result["recruiter"]:
                            db.upsert_recruiter(result["job_id"], result["recruiter"])
                        totale_analizzati += 1

                        elapsed_ms = int(time.time() * 1000) - started_at_ms
                        seen_now = (idx * max(1, settings.max_annunci)) + totale_analizzati
                        eta_ms = (
                            int((elapsed_ms / max(1, seen_now)) * (expected_total - seen_now))
                            if seen_now > 0
                            else 0
                        )
                        yield {
                            "status": "analyzed",
                            "job": {
                                "titolo": result["titolo"],
                                "azienda": result["azienda"],
                                "score": result["analysis"].get("punteggio", 0),
                            },
                            "current": seen_now,
                            "total": expected_total,
                            "percent": (
                                min(99, int(seen_now * 100 / expected_total))
                                if expected_total
                                else 0
                            ),
                            "elapsed_ms": elapsed_ms,
                            "eta_ms": eta_ms,
                        }
            finally:
                pool.shutdown(wait=False, cancel_futures=True)

        if cancelled():
            break
        time.sleep(settings.delay_tra_ricerche)

    was_cancelled = cancelled()
    # Skip retention archiving on a cancelled run (partial data — don't prune).
    archiviati = (
        0
        if was_cancelled
        else apply_post_scan_lifecycle(db=db, retention_days=settings.retention_days)
    )
    db.finish_scan(
        run_id=run_id,
        totale_trovati=totale_trovati,
        totale_nuovi=totale_nuovi,
        totale_analizzati=totale_analizzati,
        totale_scartati=totale_scartati,
    )

    duration_ms = int(time.time() * 1000) - started_at_ms
    yield {
        "status": "complete",
        "run_id": run_id,
        "totale_trovati": totale_trovati,
        "totale_nuovi": totale_nuovi,
        "totale_analizzati": totale_analizzati,
        "totale_scartati": totale_scartati,
        "archiviati": archiviati,
        "duration_ms": duration_ms,
        "percent": 100,
        "cancelled": was_cancelled,
    }
