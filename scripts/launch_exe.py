"""Standalone launcher used by PyInstaller-built JobFinder.exe.

Determines a writable workspace next to the executable, opens the
default browser when uvicorn is ready, and starts the FastAPI app.
"""

from __future__ import annotations

import os
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path


HOST = "127.0.0.1"
PORT = int(os.environ.get("PORT", "8000"))
URL = f"http://{HOST}:{PORT}"


def _resolve_workspace() -> Path:
    """Return the dir where the user's data/, .env, settings live.

    Frozen (PyInstaller bundle): parent of the executable.
    Source run: project root (parent of ``scripts/``).
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _open_browser_when_ready() -> None:
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"{URL}/api/health", timeout=0.4).read()
            webbrowser.open(URL)
            return
        except Exception:
            time.sleep(0.4)
    print(f"Server slow to come up. Open {URL} manually in your browser.")


def main() -> int:
    workspace = _resolve_workspace()
    (workspace / "data").mkdir(parents=True, exist_ok=True)

    # Tell app.main to mount itself against this workspace before we import it.
    os.environ["JOBFINDER_WORKSPACE"] = str(workspace)

    import uvicorn  # noqa: PLC0415 — import after env so app.main sees it
    from app.main import app  # noqa: PLC0415

    print(f"Job Finder — http://{HOST}:{PORT} (workspace: {workspace})")
    threading.Thread(target=_open_browser_when_ready, daemon=True).start()
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
