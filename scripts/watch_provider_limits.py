#!/usr/bin/env python
"""Notice when a provider changes what it gives away.

Run weekly by ``.github/workflows/provider-watch.yml``. Deliberately modest
about what it can know:

* the OpenRouter model catalogue IS public JSON, so which models are free today
  is a fact this script can read and diff;
* the free-tier RATE LIMITS are not. Google moved theirs behind a logged-in
  console, and they are per project anyway — the same model is 500 requests a
  day for one user and 50 for another. Anything claiming otherwise would be
  guessing, and the app already learns its real ceiling by watching its own
  calls (app/services/rate_limits.py).

So this does two honest things: it diffs the public catalogue, and it keeps a
fingerprint of each provider's pricing/limits page so a change can be reported
as "go and look" rather than invented. Exit code 1 means something moved.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any

SNAPSHOT = Path(__file__).resolve().parents[1] / "app" / "data" / "provider_watch.json"
UA = "Mozilla/5.0 (compatible; JobFinderProviderWatch/1.0)"

#: Pages whose wording decides what we ship as defaults. Fingerprinted, never
#: parsed: a heading move must not become a fake limit.
WATCHED_PAGES = {
    "google_pricing": "https://ai.google.dev/gemini-api/docs/pricing",
    "google_rate_limits": "https://ai.google.dev/gemini-api/docs/rate-limits",
    "openrouter_limits": "https://openrouter.ai/docs/api-reference/limits",
    "groq_limits": "https://console.groq.com/docs/rate-limits",
    "cerebras_pricing": "https://www.cerebras.ai/pricing",
}


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8", "replace")


def _fingerprint(html: str) -> str:
    """A hash of the readable text, so a changed build id is not a change."""
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


#: A page fingerprint is worth a human's attention only once it had held still
#: for this many weekly runs. Measured over twelve snapshots (Aug-Nov 2026): the
#: Google and OpenRouter pages, and Cerebras too, re-rendered almost every week
#: with nothing a reader would call a change, and every one of those weeks
#: opened an issue — fourteen of them, open, all saying the same thing. A page
#: that changes every week is not telling anyone anything.
STABLE_RUNS_BEFORE_ALERT = 3

#: Where a model id would have to appear for its withdrawal to matter.
_CODE_ROOTS = ("app", "web/modules")


def _referenced_in_code(model_ids: list[str]) -> list[str]:
    """The withdrawn free models the app itself names somewhere."""
    root = SNAPSHOT.parents[2]
    text = []
    for base in _CODE_ROOTS:
        for path in (root / base).rglob("*"):
            if path.suffix in {".py", ".js", ".json"} and path.resolve() != SNAPSHOT:
                try:
                    text.append(path.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    continue
    blob = "\n".join(text)
    return [m for m in model_ids if m in blob]


def free_openrouter_models() -> list[str]:
    data = json.loads(_get("https://openrouter.ai/api/v1/models"))
    return sorted(
        m["id"] for m in data.get("data", []) if str(m.get("id", "")).endswith(":free")
    )


def main() -> int:
    previous: dict[str, Any] = {}
    if SNAPSHOT.exists():
        previous = json.loads(SNAPSHOT.read_text(encoding="utf-8"))

    current: dict[str, Any] = {"openrouter_free_models": [], "pages": {}, "stable_runs": {}}
    problems: list[str] = []

    try:
        current["openrouter_free_models"] = free_openrouter_models()
    except Exception as exc:  # noqa: BLE001
        problems.append(f"could not read the OpenRouter catalogue: {exc}")
        current["openrouter_free_models"] = previous.get("openrouter_free_models", [])

    for name, url in WATCHED_PAGES.items():
        try:
            current["pages"][name] = _fingerprint(_get(url))
        except Exception as exc:  # noqa: BLE001
            problems.append(f"could not read {name} ({url}): {exc}")
            current["pages"][name] = previous.get("pages", {}).get(name, "")

    # Two lists: what deserves a person (changes) and what is only worth a line
    # in the log (notes). Only the first one opens an issue.
    changes: list[str] = []
    notes: list[str] = []
    before = set(previous.get("openrouter_free_models") or [])
    after = set(current["openrouter_free_models"])
    if before and after != before:
        gone = sorted(before - after)
        new = sorted(after - before)
        if new:
            notes.append(f"OpenRouter: {len(new)} new free model(s): {', '.join(new)}")
        if gone:
            notes.append(f"OpenRouter: {len(gone)} free model(s) withdrawn: {', '.join(gone)}")
            used = _referenced_in_code(gone)
            if used:
                changes.append(
                    f"OpenRouter withdrew free model(s) the app names: {', '.join(used)}"
                )

    stable_before = previous.get("stable_runs") or {}
    for name, digest in current["pages"].items():
        old = (previous.get("pages") or {}).get(name)
        held = int(stable_before.get(name, 0) or 0)
        if old and digest and old != digest:
            if held >= STABLE_RUNS_BEFORE_ALERT:
                changes.append(
                    f"{name}: the page changed after {held} quiet weeks — {WATCHED_PAGES[name]}"
                )
            else:
                notes.append(f"{name}: re-rendered again (not stable, no alert)")
            current["stable_runs"][name] = 0
        else:
            current["stable_runs"][name] = held + 1 if old else 0

    SNAPSHOT.write_text(
        json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    for line in problems:
        print(f"WARN {line}")
    for line in notes:
        print(f"NOTE {line}")
    if not changes:
        print("Nothing that needs a person.")
        return 0
    print("CHANGES")
    for line in changes:
        print(f"- {line}")
    print()
    print(
        "These are pointers, not new limits: check the page, then edit "
        "app/data/provider_limits.json by hand if a number really changed."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
