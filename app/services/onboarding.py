"""User onboarding answers (target sector, goal, seniority, work mode).

Stored as plain preferences and formatted into a short context block that is
injected into job scoring and the CV advisor, so recommendations reflect what
the user is actually looking for rather than only what the CV implies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.db import Database

# Preference key -> human label used in the prompt block. Kept in one place so
# the UI form, the scoring prompt and the advisor stay in sync.
ONBOARDING_FIELDS: tuple[tuple[str, str], ...] = (
    ("onboarding_sector", "Settore target"),
    ("onboarding_goal", "Obiettivo di carriera"),
    ("onboarding_seniority", "Seniority cercata"),
    ("onboarding_work_mode", "Modalita preferita"),
)


def onboarding_context(db: Database) -> str:
    """Return the filled onboarding answers as ``Label: value`` lines (or "")."""
    lines = []
    for key, label in ONBOARDING_FIELDS:
        value = (db.get_preference(key, "") or "").strip()
        if value:
            lines.append(f"{label}: {value}")
    return "\n".join(lines)
