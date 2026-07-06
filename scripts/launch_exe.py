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
    # Automated tests launch the exe just to probe /api/health and then kill it;
    # opening the default browser leaves a dead tab. Let them opt out.
    if os.environ.get("JOBFINDER_NO_BROWSER"):
        return
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"{URL}/api/health", timeout=0.4).read()
            webbrowser.open(URL)
            return
        except Exception:
            time.sleep(0.4)
    print(f"Server slow to come up. Open {URL} manually in your browser.")


def _run_tray() -> None:
    """Show a system-tray icon (Open / Quit); blocks the main thread until Quit.

    pystray owns the main thread's Win32 message loop, so uvicorn runs on a
    daemon thread. If pystray/Pillow can't load, block forever instead so the
    server keeps serving and the in-app Quit button still works.
    """
    try:
        import pystray
        from PIL import Image, ImageDraw
    except Exception:
        threading.Event().wait()
        return

    def _image() -> Image.Image:
        # Indigo briefcase, matching web/favicon.svg.
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([10, 22, 54, 52], radius=6, fill=(99, 102, 241, 255))
        d.rectangle([26, 16, 38, 24], outline=(99, 102, 241, 255), width=4)
        d.rectangle([10, 33, 54, 37], fill=(255, 255, 255, 110))
        return img

    def _open(_icon: object, _item: object) -> None:
        webbrowser.open(URL)

    def _quit(icon: object, _item: object) -> None:
        icon.stop()
        os._exit(0)

    menu = pystray.Menu(
        pystray.MenuItem("Open Job Finder", _open, default=True),
        pystray.MenuItem("Quit", _quit),
    )
    pystray.Icon("JobFinder", _image(), "Job Finder", menu).run()


def main() -> int:
    _harden_stdio()
    workspace = _resolve_workspace()
    (workspace / "data").mkdir(parents=True, exist_ok=True)

    # Tell app.main to mount itself against this workspace before we import it.
    os.environ["JOBFINDER_WORKSPACE"] = str(workspace)

    import uvicorn

    from app.main import app

    print(f"Job Finder — {URL} (workspace: {workspace})")
    threading.Thread(target=_open_browser_when_ready, daemon=True).start()

    # Frozen Windows build is windowless (console=False) — no terminal to close.
    # Run uvicorn on a daemon thread and give the main thread to a tray icon
    # (Open / Quit). Dev/source runs keep the simple blocking server (no tray).
    if getattr(sys, "frozen", False) and sys.platform == "win32":
        server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=PORT, log_level="info"))
        threading.Thread(target=server.run, name="uvicorn", daemon=True).start()
        _run_tray()
    else:
        uvicorn.run(app, host=HOST, port=PORT, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
