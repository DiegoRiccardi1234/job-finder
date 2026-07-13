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
from app.services.onboarding import onboarding_context
from app.services.pii import redact_pii
from app.services.recruiter_scrape import fetch_recruiter

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
    # we keep a fast lean but with a quality floor: de-rank models that advertise
    # a size under 40B, and reward capability again. Target: gpt-oss-120b:free
    # (~4s, capable) over a 1.2B free model.
    "prefer_fast": True,
    "prefer_quality": True,
    "prefer_free": True,
    "max_cost_tier": "high",
    "min_size_b": 40,
    "weights": {
        "size": 16,
        "speed": 12,
        "family": 20,
        "instruct": 25,
        "chat": 10,
        "json": 12,
        "reasoning": 4,
        "small_penalty": -150,
    },
}

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


def pre_filtro(titolo: str, descrizione: str) -> tuple[bool, str]:
    testo = (titolo + " " + descrizione).lower()
    for frase in BLACKLIST:
        if frase in testo:
            return True, frase
    return False, ""


def _analysis_prompt(
    profile_markdown: str,
    titolo: str,
    azienda: str,
    descrizione: str,
    extra_context: str = "",
) -> str:
    extra = f"\nPREFERENZE CANDIDATO:\n{extra_context}\n" if extra_context.strip() else ""
    return f"""Analizza questa offerta IT e rispondi SOLO con JSON valido, senza testo extra.

CV candidato:
{profile_markdown[:3500]}
{extra}
OFFERTA:
Titolo: {titolo}
Azienda: {azienda}
Descrizione: {descrizione[:1800]}

JSON richiesto:
{{
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
  "skills_match": {{
    "hai": ["skills che il candidato ha e l'offerta richiede"],
    "mancano": ["skills richieste che il candidato non ha"]
  }},
  "livello_richiesto": "internship|entry|junior|mid|senior|lead",
  "match_axes": {{
    "skills_match": <0-10>,
    "seniority_match": <0-10>,
    "remote_match": <0-10>,
    "salary_match": <0-10>,
    "contract_match": <0-10>
  }}
}}
"""


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
    # Privacy Mode: scrub the CV before it reaches the LLM. Scoring never needs
    # the name/contacts, so no restore — the token map is discarded. The local
    # keyword fallback below keeps the ORIGINAL markdown for a better match.
    prompt_markdown = profile_markdown
    if privacy:
        prompt_markdown, _ = redact_pii(profile_markdown, candidate_name)
    prompt = _analysis_prompt(prompt_markdown, titolo, azienda, descrizione, extra_context)
    try:
        result = provider_manager.complete_json(
            prompt=prompt, max_tokens=200, policy_override=_SCORING_POLICY
        )
        if not isinstance(result, dict):
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

    profile = db.get_active_candidate_profile()
    profile_markdown = profile["markdown"] if profile else "Profile not loaded."

    linkedin_url = db.get_preference("linkedin_url", "")
    if linkedin_url:
        profile_markdown += f"\n\nLinkedIn profile: {linkedin_url}"

    # Privacy Mode + onboarding preferences, resolved once for every job in this
    # scan. feature_privacy_mode mirrors container.feature_enabled semantics.
    privacy = db.get_preference("feature_privacy_mode", "1") not in ("0", "false", "off", "")
    onboarding = onboarding_context(db)
    candidate_name = profile.get("name") if profile else None
    log.info(
        "Scan scoring model (speed-biased): %s",
        provider_manager.preview_scoring_model(_SCORING_POLICY),
    )

    terms = payload.search_terms or settings.default_search_terms
    exp_levels = list(payload.experience_levels or [])
    job_types = list(payload.job_types or [])
    work_types = list(payload.work_types or [])
    min_salary = int(payload.min_salary or 0)

    is_remote_effective = payload.is_remote or ("remote" in work_types)

    location = payload.location or (
        settings.location_remote_default if is_remote_effective else settings.location_default
    )
    modalita = "Full Remote" if is_remote_effective else "In sede"

    augmented_terms = [_augment_search_term(t, exp_levels, work_types) for t in terms]

    jobspy_job_type = _resolve_jobspy_job_type(job_types)

    db.set_preference("last_scan_location", location)
    db.set_preference("last_scan_is_remote", "1" if is_remote_effective else "0")
    db.set_preference("last_scan_terms", json.dumps(terms, ensure_ascii=False))
    db.set_preference(
        "last_scan_filters",
        json.dumps(
            {"experience_levels": exp_levels, "job_types": job_types, "work_types": work_types},
            ensure_ascii=False,
        ),
    )

    run_id = db.begin_scan(location=location, is_remote=is_remote_effective, terms=terms)

    started_at_ms = int(time.time() * 1000)
    expected_total = max(1, len(terms) * max(1, settings.max_annunci))

    yield {
        "status": "started",
        "terms": terms,
        "location": location,
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

    def _score_job(item: dict[str, Any]) -> dict[str, Any]:
        """Score one offer (LLM) + best-effort recruiter fetch. Runs on a worker
        thread; DB writes happen back on the generator thread."""
        row = item["row"]
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
        if link and "linkedin.com" in link and not db.get_recruiter(item["job_id"]):
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

    for idx, term in enumerate(terms):
        if cancelled():
            break
        if idx > 0:
            time.sleep(random.uniform(0.8, 2.4))

        effective_term = augmented_terms[idx] if idx < len(augmented_terms) else term

        elapsed_ms = int(time.time() * 1000) - started_at_ms
        seen = idx * max(1, settings.max_annunci)
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
            "country_indeed": "Italy",
        }
        if jobspy_job_type:
            scrape_kwargs["job_type"] = jobspy_job_type

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
            titolo = str(row.get("title", "N/A"))
            azienda = str(row.get("company", "N/A"))
            descrizione = str(row.get("description", ""))

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
                "sede": str(row.get("location", "")),
                "fonte": str(row.get("site", "")),
                "link": str(row.get("job_url", "")),
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

            to_score.append(
                {
                    "job_id": job_id,
                    "titolo": titolo,
                    "azienda": azienda,
                    "descrizione": descrizione,
                    "row": row,
                    "link": payload_job.get("link") or "",
                }
            )

        # Pass 2 (concurrent): score surviving jobs, emit each as it resolves.
        if to_score and not cancelled():
            workers = max(1, min(settings.scan_concurrency, len(to_score)))
            pool = ThreadPoolExecutor(max_workers=workers)
            try:
                futures = {pool.submit(_score_job, item): item for item in to_score}
                for fut in as_completed(futures):
                    if cancelled():
                        break
                    try:
                        result = fut.result()
                    except Exception as exc:  # analyze_offer degrades internally
                        log.warning("scoring task failed: %s", exc)
                        continue
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
                            min(99, int(seen_now * 100 / expected_total)) if expected_total else 0
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
