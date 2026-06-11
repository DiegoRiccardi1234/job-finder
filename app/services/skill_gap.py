"""Skill-gap analysis.

Aggregates the skills that recurring job analyses flag as *missing* for the
candidate (``analysis_json.skills_match.mancano``) across all scored jobs, so
the user sees which competencies to learn to unlock more matches. Pure
aggregation over already-stored analysis — no extra LLM calls.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.db import Database


def _normalize(skill: str) -> str:
    return " ".join(skill.strip().lower().split())


def _profile_skills(db: Database) -> set[str]:
    profile = db.get_active_candidate_profile()
    if not profile:
        return set()
    summary = profile.get("summary_json") or {}
    skills = summary.get("skills") if isinstance(summary, dict) else None
    if not isinstance(skills, list):
        return set()
    return {_normalize(str(s)) for s in skills if str(s).strip()}


def compute_skill_gap(db: Database, *, top: int = 12) -> dict[str, Any]:
    """Return the most frequently missing skills across analyzed jobs.

    A skill the candidate already has (per their profile) is never reported as
    a gap. Each entry carries the occurrence count and a few example job ids.
    """
    have = _profile_skills(db)
    counts: dict[str, int] = {}
    labels: dict[str, str] = {}
    examples: dict[str, list[int]] = {}
    analyzed_jobs = 0

    for job in db.list_jobs(limit=2000):
        analysis = job.get("analysis_json")
        if isinstance(analysis, str):
            import json

            try:
                analysis = json.loads(analysis)
            except (ValueError, TypeError):
                analysis = None
        if not isinstance(analysis, dict):
            continue
        match = analysis.get("skills_match")
        if not isinstance(match, dict):
            continue
        analyzed_jobs += 1
        missing = match.get("mancano")
        if not isinstance(missing, list):
            continue
        job_id = int(job.get("id", 0) or 0)
        for raw in missing:
            label = str(raw).strip()
            if not label:
                continue
            key = _normalize(label)
            if key in have:
                continue
            counts[key] = counts.get(key, 0) + 1
            labels.setdefault(key, label)
            examples.setdefault(key, [])
            if job_id and len(examples[key]) < 5:
                examples[key].append(job_id)

    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
    gaps = [
        {"skill": labels[key], "count": count, "jobs": examples.get(key, [])}
        for key, count in ranked
    ]
    return {
        "gaps": gaps,
        "analyzed_jobs": analyzed_jobs,
        "max_count": gaps[0]["count"] if gaps else 0,
    }
