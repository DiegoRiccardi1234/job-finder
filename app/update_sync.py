"""File-sync primitive used by the standalone updater.

Copies every file from a freshly-extracted bundle directory into the
install directory, skipping a small whitelist of user-owned paths
(``data/``, ``.env``, ``.env.local``). The point is that the SQLite
DB, uploaded CV, API keys, and any environment overrides survive an
update untouched.

This module is import-clean (no side effects) so the unit tests can
exercise ``sync_install_dir`` without going through the rest of the
binary.
"""

from __future__ import annotations

import shutil
from pathlib import Path

# Top-level paths in the install dir that the updater must NEVER overwrite
# or delete. Match by path component, so ``data/searcher.db`` is preserved
# because ``data`` is in this set.
PRESERVE_TOPLEVEL = ("data", ".env", ".env.local")


def _is_preserved(rel_path: Path) -> bool:
    parts = rel_path.parts
    return bool(parts) and parts[0] in PRESERVE_TOPLEVEL


def sync_install_dir(*, source: Path, target: Path) -> int:
    """Copy everything under ``source`` into ``target``.

    Returns the number of files written. Files matching
    ``PRESERVE_TOPLEVEL`` are skipped on the source side so user data
    that happens to ship inside the bundle (it shouldn't, but defensive)
    can never clobber the user's real data.
    """
    if not source.exists():
        raise FileNotFoundError(f"source bundle not found: {source}")
    target.mkdir(parents=True, exist_ok=True)

    written = 0
    for src in source.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(source)
        if _is_preserved(rel):
            continue
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        written += 1
    return written
