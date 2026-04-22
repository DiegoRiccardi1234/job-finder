"""Build textual context blocks fed to the LLM (profile, preferences, jobs)."""

from __future__ import annotations

import json
from typing import Any

from app.db import Database


def jobs_context(db: Database, limit: int = 5) -> str:
    jobs = db.get_top_jobs(limit=limit)
    if not jobs:
        return "No jobs scanned yet."

    lines: list[str] = []
    for idx, job in enumerate(jobs, 1):
        analysis_raw = job.get("analysis_json") or "{}"
        try:
            analysis = json.loads(analysis_raw) if isinstance(analysis_raw, str) else analysis_raw
        except (json.JSONDecodeError, TypeError):
            analysis = {}

        smart_working = analysis.get("smart_working", "N/A")
        contratto = analysis.get("contratto", "N/A")
        exp_years = analysis.get("anni_esperienza_richiesti", "N/A")

        lines.append(
            f"{idx}. {job.get('titolo')} @ {job.get('azienda')} | "
            f"Score: {job.get('punteggio_ai')}/10 | "
            f"Advice: {job.get('consiglio')} | "
            f"Remote: {smart_working} | Contract: {contratto} | Exp: {exp_years}y"
        )
    return "\n".join(lines)


def profile_summary_dict(db: Database) -> dict[str, Any]:
    profile = db.get_active_candidate_profile()
    if not profile:
        return {}

    summary = profile.get("summary_json")
    if isinstance(summary, dict):
        return summary
    if isinstance(summary, str):
        try:
            loaded = json.loads(summary)
            if isinstance(loaded, dict):
                return loaded
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def build_profile_context(db: Database) -> str:
    profile = db.get_active_candidate_profile()
    if not profile:
        return "No CV uploaded yet."

    profile_text = profile["markdown"][:2000]
    summary = profile.get("summary_json")
    if summary:
        try:
            summary_data = json.loads(summary) if isinstance(summary, str) else summary
            if isinstance(summary_data, dict):
                profile_text += "\n\n--- Profile Summary ---\n"
                for k, v in summary_data.items():
                    profile_text += f"- {k}: {v}\n"
        except (json.JSONDecodeError, TypeError):
            pass

    linkedin_url = db.get_preference("linkedin_url", "")
    if linkedin_url:
        profile_text += f"\nLinkedIn: {linkedin_url}"

    return profile_text


def build_preferences_context(db: Database) -> str:
    prefs: list[str] = []
    remote = db.get_preference("remote_mode", "")
    if remote:
        prefs.append(f"Work mode: {remote}")
    min_ral = db.get_preference("min_ral", "")
    if min_ral:
        prefs.append(f"Minimum salary: {min_ral}")
    if db.get_preference("prefer_role_qa", "") == "1":
        prefs.append("Interested in: QA/Testing roles")
    if db.get_preference("prefer_role_cyber", "") == "1":
        prefs.append("Interested in: Cybersecurity roles")
    if db.get_preference("prefer_role_data", "") == "1":
        prefs.append("Interested in: Data Analyst roles")
    return "\n".join(prefs) if prefs else "No specific preferences set yet."


def _append_unique(values: list[str], value: str) -> None:
    normalized = value.strip()
    if not normalized:
        return
    existing = {item.lower() for item in values}
    if normalized.lower() in existing:
        return
    values.append(normalized)


def suggest_keywords_from_profile(db: Database, limit: int = 4) -> list[str]:
    keywords: list[str] = []
    summary = profile_summary_dict(db)

    preferred_roles = summary.get("preferred_roles") or summary.get("ruoli_preferiti") or []
    if isinstance(preferred_roles, list):
        for role in preferred_roles:
            _append_unique(keywords, str(role))

    if db.get_preference("prefer_role_data", "") == "1":
        _append_unique(keywords, "Data Analyst")
    if db.get_preference("prefer_role_qa", "") == "1":
        _append_unique(keywords, "QA Tester")
    if db.get_preference("prefer_role_cyber", "") == "1":
        _append_unique(keywords, "Cybersecurity Analyst")

    profile_text = build_profile_context(db).lower()
    if "python" in profile_text:
        _append_unique(keywords, "Python Developer")
    if "data" in profile_text:
        _append_unique(keywords, "Data Analyst")
    if "qa" in profile_text or "testing" in profile_text:
        _append_unique(keywords, "QA Tester")

    if not keywords:
        keywords = ["Python Developer", "Data Analyst", "QA Tester"]

    return keywords[:limit]


def suggest_locations(db: Database) -> list[str]:
    remote_mode = db.get_preference("remote_mode", "")
    last_scan_location = db.get_preference("last_scan_location", "").strip()

    if remote_mode == "full_remote":
        return ["Italy"]
    if last_scan_location:
        return [last_scan_location]
    return ["Italy"]
