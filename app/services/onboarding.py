"""User onboarding answers (target sector, goal, seniority, work mode, salary).

Stored as plain preferences and formatted into a short context block that is
injected into job scoring and the CV advisor, so recommendations reflect what
the user is actually looking for rather than only what the CV implies.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.db import Database

# Preference key -> human label used in the prompt block. Kept in one place so
# the UI form, the scoring prompt and the advisor stay in sync.
RAL_MIN_KEY = "onboarding_ral_min"
RAL_TARGET_KEY = "onboarding_ral_target"
# Labels are also the parse anchors: the scoring path reads the minimum back out
# of the rendered context block instead of taking a second plumbing route.
RAL_MIN_LABEL = "RAL minima accettabile (EUR lordi/anno)"
RAL_TARGET_LABEL = "RAL target (EUR lordi/anno)"

ONBOARDING_FIELDS: tuple[tuple[str, str], ...] = (
    ("onboarding_sector", "Settore target"),
    ("onboarding_goal", "Obiettivo di carriera"),
    ("onboarding_seniority", "Seniority cercata"),
    ("onboarding_work_mode", "Modalita preferita"),
    (RAL_MIN_KEY, RAL_MIN_LABEL),
    (RAL_TARGET_KEY, RAL_TARGET_LABEL),
)


def parse_ral_amount(raw: str) -> int | None:
    """Read a salary a human typed: "35000", "35.000", "35.000 €", "35k".

    Returns euros per year, or None when nothing plausible is in the string.
    Values below 1000 are read as thousands ("35" -> 35000) — nobody means a
    35-euro annual salary, and the form is a free-text number field.
    """
    text = (raw or "").strip().lower()
    if not text:
        return None
    thousands = "k" in text
    digits = re.sub(r"[^\d]", "", text.split("k")[0] if thousands else text)
    if not digits:
        return None
    amount = int(digits)
    if thousands or amount < 1000:
        amount *= 1000
    return amount or None


def onboarding_ral(db: Database) -> tuple[int | None, int | None]:
    """(minimum, target) yearly salary the user declared, as ints or None."""
    return (
        parse_ral_amount(db.get_preference(RAL_MIN_KEY, "") or ""),
        parse_ral_amount(db.get_preference(RAL_TARGET_KEY, "") or ""),
    )


def onboarding_context(db: Database) -> str:
    """Return the filled onboarding answers as ``Label: value`` lines (or "")."""
    lines = []
    for key, label in ONBOARDING_FIELDS:
        value = (db.get_preference(key, "") or "").strip()
        if not value:
            continue
        if key in (RAL_MIN_KEY, RAL_TARGET_KEY):
            # Normalise "35k"/"35.000 €" so the model reads one unambiguous number.
            amount = parse_ral_amount(value)
            value = f"{amount} EUR" if amount else value
        lines.append(f"{label}: {value}")
    return "\n".join(lines)
