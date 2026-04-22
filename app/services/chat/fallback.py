"""Rule-based fallback when the LLM provider is unavailable or errors out."""

from __future__ import annotations

from typing import Any

from app.db import Database
from app.services.chat.context import suggest_keywords_from_profile, suggest_locations
from app.services.chat.intents import has_role_guidance_intent, has_search_intent
from app.services.chat.state import is_italian_ui


def fallback_answer(db: Database, message: str) -> tuple[str, dict[str, Any] | None]:
    """Return a canned answer + optional action when no LLM is reachable."""
    lower = message.lower()

    recommend_intent = any(
        token in lower
        for token in ["recommend", "best", "top", "consiglia", "miglior", "priorit", "candid"]
    )

    recommended_jobs = db.get_recommended_jobs(limit=3)
    top_jobs = recommended_jobs or db.get_top_jobs(limit=3)

    if recommend_intent:
        if not top_jobs:
            if is_italian_ui(db):
                return (
                    "Non ho ancora offerte analizzate. Avvia prima una scansione dalla pagina Settings.",
                    None,
                )
            return "No analyzed jobs available yet. Run a scan first from Settings.", None

        if is_italian_ui(db):
            lines = ["Ecco le offerte prioritarie in questo momento:"]
        else:
            lines = ["Here are the top picks right now:"]

        for idx, job in enumerate(top_jobs, 1):
            score = int(job.get("punteggio_ai") or 0)
            advice = job.get("consiglio") or "Valutabile"
            lines.append(
                f"{idx}. {job.get('titolo')} @ {job.get('azienda')} "
                f"(score {score}/10, {advice})"
            )

        if is_italian_ui(db):
            lines.append("\nApri il pannello Details per vedere motivazioni e prossima azione consigliata.")
        else:
            lines.append("\nOpen the Details panel on any job to learn more or apply.")
        return "\n".join(lines), None

    if has_role_guidance_intent(message):
        keywords = suggest_keywords_from_profile(db)
        locations = suggest_locations(db)
        action = {"type": "FILL_SCAN_FORM", "keywords": keywords, "locations": locations}

        top_roles = ", ".join(keywords[:3])
        if is_italian_ui(db):
            return (
                f"Dal tuo CV le figure piu coerenti sono: {top_roles}. "
                "Ho anche precompilato la ricerca con parole chiave e location: avvia la scansione e poi ti dico quali offerte hanno fit migliore.",
                action,
            )

        return (
            f"From your CV, the most suitable roles are: {top_roles}. "
            "I also pre-filled search terms and location: run the scan and I will rank the best matches.",
            action,
        )

    if has_search_intent(message):
        keywords = suggest_keywords_from_profile(db)
        locations = suggest_locations(db)
        action = {"type": "FILL_SCAN_FORM", "keywords": keywords, "locations": locations}

        if is_italian_ui(db):
            return (
                "Ho preparato una ricerca coerente con CV e preferenze. "
                f"Parole chiave: {', '.join(keywords)}. "
                f"Location: {', '.join(locations)}. "
                "Avvia la scansione e poi ti ordino le offerte per priorita.",
                action,
            )

        return (
            "I prepared a search aligned with your CV and preferences. "
            f"Keywords: {', '.join(keywords)}. "
            f"Locations: {', '.join(locations)}. "
            "Run the scan and I will prioritize the best opportunities.",
            action,
        )

    if is_italian_ui(db):
        return (
            "Messaggio salvato. Posso suggerirti cosa cercare, avviare una ricerca guidata e ordinare le offerte migliori.",
            None,
        )
    return (
        "I've saved your message. I can suggest what to search and prioritize your best jobs — just ask!",
        None,
    )
