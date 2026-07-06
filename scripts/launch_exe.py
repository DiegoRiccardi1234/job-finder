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


def _stream_ok(stream: object) -> bool:
    """True when ``stream`` can be written to without raising.

    ``None`` (a windowed build's streams) is not ok. A real stream whose OS
    handle is dead — a ``console=True`` exe relaunched with ``DETACHED_PROCESS``
    keeps a valid ``fileno()`` but its handle is invalid, so ``os.fstat`` fails —
    is not ok. A stream with no OS ``fileno`` (``StringIO``, pytest capture) has
    no handle to be invalid, so it is fine and must be left alone.
    """
    if stream is None:
        return False
    fileno = getattr(stream, "fileno", None)
    if fileno is None:
        return True
    try:
        fd = fileno()
    except Exception:
        return True
    try:
        os.fstat(fd)
    except OSError:
        return False
    return True


def _harden_stdio() -> None:
    """Rebind broken stdout/stderr to os.devnull so a startup write can't crash.

    The frozen JobFinder ships ``console=False`` (no terminal, ever). Launched
    without a valid console — a windowed double-click yields ``None`` streams,
    and any leftover console spawned ``DETACHED_PROCESS`` yields dead handles —
    the first ``print``/uvicorn log write would raise ``OSError`` and kill the
    process before uvicorn binds the port (the "Restart 95%" hang). Redirect to
    devnull; real app events still reach ``data/logs/app.log`` via the file
    handler in ``app.log.configure_logging``.
    """
    for name in ("stdout", "stderr"):
        if not _stream_ok(getattr(sys, name, None)):
            # Intentionally long-lived: this sink replaces the process stream for
            # the whole run, so no context manager. noqa: SIM115.
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115


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
    _harden_stdio()
    workspace = _resolve_workspace()
    (workspace / "data").mkdir(parents=True, exist_ok=True)

    # Tell app.main to mount itself against this workspace before we import it.
    os.environ["JOBFINDER_WORKSPACE"] = str(workspace)

    import uvicorn

    from app.main import app

    print(f"Job Finder — http://{HOST}:{PORT} (workspace: {workspace})")
    threading.Thread(target=_open_browser_when_ready, daemon=True).start()
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
