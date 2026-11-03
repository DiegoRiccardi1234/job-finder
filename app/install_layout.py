"""Pure path helpers for legacy bundles and the organized app/state layout.

No directory is created and no application/configuration module is imported.
An ordinary source checkout containing an ``app`` package is never mistaken
for an installed bundle: only the versioned marker enables this layout.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

LAYOUT_MARKER = ".jobfinder-layout.json"
LAYOUT_VERSION = 1


def is_organized(root: Path) -> bool:
    try:
        marker = json.loads((Path(root) / "app" / LAYOUT_MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (
        isinstance(marker, dict)
        and type(marker.get("version")) is int
        and marker["version"] == LAYOUT_VERSION
    )


def installation_root(executable_or_dir: Path | None = None) -> Path:
    """Resolve a runtime executable/directory to its enclosing installation.

    Accepting a directory also supports updater callers that already computed
    ``Path(sys.executable).parent``. A missing marker preserves legacy behavior.
    """
    target = Path(executable_or_dir or sys.executable).resolve()
    directory = target.parent if target.suffix.lower() == ".exe" or target.is_file() else target
    if directory.name.lower() == "app":
        _validate_marker(directory.parent)
    if directory.name.lower() == "app" and is_organized(directory.parent):
        return directory.parent
    return directory


def runtime_dir(root: Path) -> Path:
    root = Path(root)
    _validate_marker(root)
    return root / "app" if is_organized(root) else root


def state_dir(root: Path) -> Path:
    root = Path(root)
    _validate_marker(root)
    return root / "state" if is_organized(root) else root / "data"


def vendor_dir(root: Path) -> Path:
    return runtime_dir(root) / "vendor"


def _validate_marker(root: Path) -> None:
    if (root / "app" / LAYOUT_MARKER).exists() and not is_organized(root):
        raise ValueError("Unrecognized Job Finder layout; restore its marker before starting")
