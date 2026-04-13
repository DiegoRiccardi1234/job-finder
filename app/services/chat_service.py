import json
import re
from typing import Any

from app.db import Database
from app.providers.factory import ProviderManager


# ─── Preference Extraction ──────────────────────────────────────

def _extract_pref_updates(message: str) -> dict[str, str]:
    updates: dict[str, str] = {}
    lower = message.lower()

    if "full remote" in lower or "fully remote" in lower or "solo remote" in lower:
        updates["remote_mode"] = "full_remote"
    elif "hybrid" in lower or "ibrido" in lower:
        updates["remote_mode"] = "hybrid"
    elif "on-site" in lower or "onsite" in lower or "in office" in lower:
        updates["remote_mode"] = "onsite"

    ral_match = re.search(r"(?:min|minimum|salary|ral)\s*(?:of\s*)?(\d{2,6})", lower)
    if ral_match:
        value = int(ral_match.group(1))
        if value < 1000:
            value = value * 1000
        updates["min_ral"] = str(value)

    if "qa" in lower and "no" not in lower:
        updates["prefer_role_qa"] = "1"
    if "cyber" in lower and "no" not in lower:
        updates["prefer_role_cyber"] = "1"
    if "data" in lower and ("analyst" in lower or "analy" in lower):
        updates["prefer_role_data"] = "1"

    return updates


# ─── Context Builders ───────────────────────────────────────────

def _jobs_context(db: Database, limit: int = 5) -> str:
    jobs = db.get_top_jobs(limit=limit)
    if not jobs:
        return "No jobs scanned yet."

    lines = []
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


def _build_profile_context(db: Database) -> str:
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


def _build_preferences_context(db: Database) -> str:
    prefs = []
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


def _get_chat_state(db: Database) -> str:
    """Determine conversation state based on what data is available."""
    profile = db.get_active_candidate_profile()
    jobs = db.get_top_jobs(limit=1)
    prefs_set = bool(
        db.get_preference("remote_mode", "")
        or db.get_preference("min_ral", "")
    )

    if not profile:
        return "no_cv"
    if not prefs_set:
        return "onboarding"
    if not jobs:
        return "ready_to_search"
    return "advising"


# ─── System Prompts ─────────────────────────────────────────────

SYSTEM_PROMPTS = {
    "no_cv": (
        "You are an AI Career Coach. The user hasn't uploaded their CV yet. "
        "Warmly greet them and explain that uploading their CV (PDF, DOCX or TXT) "
        "in the Settings page will unlock personalized job recommendations. "
        "Be encouraging and brief."
    ),
    "onboarding": (
        "You are an AI Career Coach. The user just uploaded their CV. "
        "You can see their profile below. Your job is to ask smart questions to understand:\n"
        "1. What type of role they're targeting\n"
        "2. Remote, hybrid, or on-site preference\n"
        "3. Minimum salary expectation\n"
        "4. Any industries or companies to focus on or avoid\n"
        "5. Location preferences\n\n"
        "Ask ONE question at a time. Be conversational and friendly. "
        "When you've gathered enough info, summarize what you learned and suggest "
        "running a job scan with specific search terms.\n"
        "Respond in the same language the user writes in."
    ),
    "ready_to_search": (
        "You are an AI Career Coach. You know the user's profile and preferences. "
        "No jobs have been scanned yet. Suggest they run a job scan from Settings, "
        "and recommend specific search terms based on their profile. "
        "Respond in the same language the user writes in."
    ),
    "advising": (
        "You are an AI Career Coach for IT professionals. You have the user's CV, "
        "their preferences, and the top job listings with AI scores.\n\n"
        "Your capabilities:\n"
        "- Recommend which jobs to apply for and explain WHY based on their profile\n"
        "- Explain AI scores and what makes a job a good/bad match\n"
        "- Suggest jobs the user might not have considered\n"
        "- Help with application strategy and timing\n"
        "- Generate talking points for interviews\n"
        "- Answer any questions about the job listings\n\n"
        "Be specific, practical, and reference actual job data. "
        "When recommending, always explain the reasoning. "
        "Respond in the same language the user writes in."
    ),
}


# ─── Fallback (no LLM) ─────────────────────────────────────────

def _fallback_answer(db: Database, message: str) -> str:
    lower = message.lower()
    top_jobs = db.get_top_jobs(limit=3)

    if any(w in lower for w in ["recommend", "best", "top", "consiglia", "miglior"]):
        if not top_jobs:
            return "No analyzed jobs available yet. Run a scan first from Settings."
        lines = ["Here are the top picks right now:"]
        for idx, job in enumerate(top_jobs, 1):
            lines.append(
                f"{idx}. {job.get('titolo')} @ {job.get('azienda')} "
                f"(score {job.get('punteggio_ai')}/10, {job.get('consiglio')})"
            )
        lines.append("\nOpen the Details panel on any job to learn more or apply.")
        return "\n".join(lines)

    return "I've saved your message. I can recommend top jobs or update your preferences — just ask!"


# ─── Main Handler ───────────────────────────────────────────────

def handle_chat_message(
    db: Database,
    provider_manager: ProviderManager,
    message: str,
    session_id: str,
    provider: str | None = None,
) -> dict[str, Any]:
    db.save_chat_message(session_id=session_id, role="user", content=message)

    updates = _extract_pref_updates(message)
    for key, value in updates.items():
        db.set_preference(key, value)

    state = _get_chat_state(db)
    profile_context = _build_profile_context(db)
    prefs_context = _build_preferences_context(db)
    jobs_context = _jobs_context(db)

    system_prompt = SYSTEM_PROMPTS.get(state, SYSTEM_PROMPTS["advising"])
    system_prompt += (
        "\n\nIMPORTANT: You must always output ONLY a raw JSON object string (do not use markdown formatting blocks like ```json). "
        "The JSON MUST have the following structure:\n"
        "{\n"
        '  "answer": "Your conversational response as plain text or markdown here",\n'
        '  "action": null\n'
        "}\n\n"
        "However, NO MATTER WHICH STATE YOU ARE IN, if the user explicitly asks you to search for jobs, scan for jobs, or find new listings (e.g., 'Cerca lavori per Python a Roma' or 'Find me remote java jobs'), "
        "you MUST formulate the parameters and put them in the 'action' field, like this:\n"
        "{\n"
        '  "answer": "Ho preparato la ricerca per...',
        '  "action": {"type": "FILL_SCAN_FORM", "keywords": ["Python"], "locations": ["Roma"]}\n'
        "}"
    )

    prompt_messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": (
                f"=== Candidate Profile ===\n{profile_context}\n\n"
                f"=== Preferences ===\n{prefs_context}\n\n"
                f"=== Top Job Listings ===\n{jobs_context}\n\n"
                f"=== User Message ===\n{message}"
            ),
        },
    ]

    action_payload = None
    try:
        raw_answer = provider_manager.chat(prompt_messages, max_tokens=700, provider_name=provider)
        try:
            # Strip markdown block if model ignored instructions
            if raw_answer.startswith("```json"):
                raw_answer = raw_answer.replace("```json", "", 1)
            if raw_answer.endswith("```"):
                raw_answer = raw_answer[::-1].replace("```" , "", 1)[::-1]
            
            parsed = json.loads(raw_answer.strip())
            answer = parsed.get("answer", raw_answer)
            action_payload = parsed.get("action")
        except json.JSONDecodeError:
            answer = raw_answer
    except Exception:
        answer = _fallback_answer(db=db, message=message)

    db.save_chat_message(session_id=session_id, role="assistant", content=answer)
    return {
        "session_id": session_id,
        "answer": answer,
        "updated_preferences": updates,
        "chat_state": state,
        "action": action_payload,
    }
