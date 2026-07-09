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

# Held for the whole process lifetime so the single-instance mutex stays owned;
# Windows releases it automatically when the process exits.
_SINGLE_INSTANCE_HANDLE: object = None


def _update_in_progress(workspace: Path) -> bool:
    """True when a fresh ``data/update.lock`` shows an updater is mid-flight.

    During a self-update the running app exits so the updater can overwrite
    ``JobFinder.exe``. If the user relaunches the exe in that window, the new
    process re-locks the running image and the updater's file copy fails with
    ``PermissionError`` (the real "stuck at 95%" update seen in updater.log).
    The backend writes ``update.lock`` at update start and the updater clears it
    around the swap, so a fresh lock means: don't start — let the updater finish
    and relaunch the new version itself. That relaunch carries
    ``JOBFINDER_UPDATED=1`` and skips this check (avoids a relaunch race).
    """
    if os.environ.get("JOBFINDER_UPDATED"):
        return False
    lock = workspace / "data" / "update.lock"
    try:
        age = time.time() - lock.stat().st_mtime
    except OSError:
        return False
    # Generous window: a ~185 MB download on a slow line can exceed the backend's
    # 180 s double-spawn TTL (measured ~210 s), so bound wider here. Older than
    # this = the updater almost certainly died; start anyway rather than lock the
    # user out.
    return age < 900


def _acquire_single_instance() -> bool:
    """Windows named-mutex guard. True when we are the first/only instance.

    A second live ``JobFinder.exe`` holds a section lock on the running image;
    launching one while an update stages breaks the updater's copy. The owning
    process keeps the handle for its lifetime; a duplicate returns fast so it can
    exit and release its lock. Never blocks startup on a guard failure.
    """
    global _SINGLE_INSTANCE_HANDLE
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, "Global\\JobFinder_singleton")
        if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            return False
        _SINGLE_INSTANCE_HANDLE = handle  # keep alive; released on process exit
        return True
    except Exception:
        return True


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
    # A self-update relaunch already has the user's tab open: runBundleUpdate()
    # in web/modules/update.js polls /api/health and reloads that tab in place
    # once the new server is back, so opening another tab here just leaves a
    # duplicate (the "two tabs after update" report). Skip it on the
    # updater-triggered relaunch — the updater sets JOBFINDER_UPDATED=1. If the
    # user closed the tab mid-update, the tray "Open" entry still reopens it.
    if os.environ.get("JOBFINDER_UPDATED"):
        return
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


_TRAY_LABELS = {
    "en": {"open": "Open Job Finder", "quit": "Quit"},
    "it": {"open": "Apri Job Finder", "quit": "Esci"},
    "es": {"open": "Abrir Job Finder", "quit": "Salir"},
    "fr": {"open": "Ouvrir Job Finder", "quit": "Quitter"},
    "de": {"open": "Job Finder öffnen", "quit": "Beenden"},
}


def _ui_language(workspace: Path) -> str:
    """Read the persisted UI language from the DB (read-only), default 'en'."""
    db = workspace / "data" / "searcher.db"
    try:
        import sqlite3

        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1.0)
        try:
            row = con.execute("SELECT value FROM preferences WHERE key='ui_language'").fetchone()
        finally:
            con.close()
        lang = (row[0] if row else "en") or "en"
        return lang if lang in _TRAY_LABELS else "en"
    except Exception:
        return "en"


def _run_tray(workspace: Path) -> None:
    """Show a system-tray icon (Open / Quit); blocks the main thread until Quit.

    pystray owns the main thread's Win32 message loop, so uvicorn runs on a
    daemon thread. If pystray/Pillow can't load, block forever instead so the
    server keeps serving and the in-app Quit button still works. Also registers
    the icon's native notification with app.notify so the scheduler can toast.
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

    def _label(key: str) -> str:
        # Callable menu text → follows an in-app language change without restart.
        return _TRAY_LABELS[_ui_language(workspace)][key]

    def _open(_icon: object, _item: object) -> None:
        webbrowser.open(URL)

    def _quit(icon: object, _item: object) -> None:
        icon.stop()
        os._exit(0)

    menu = pystray.Menu(
        pystray.MenuItem(lambda _i: _label("open"), _open, default=True),
        pystray.MenuItem(lambda _i: _label("quit"), _quit),
    )
    icon = pystray.Icon("JobFinder", _image(), "Job Finder", menu)

    def _setup(ic: object) -> None:
        ic.visible = True
        from app.notify import register_notifier

        # pystray's signature is notify(message, title) — swap to (title, message).
        register_notifier(lambda title, msg: ic.notify(msg, title))

    icon.run(setup=_setup)


def main() -> int:
    _harden_stdio()
    workspace = _resolve_workspace()
    (workspace / "data").mkdir(parents=True, exist_ok=True)

    if getattr(sys, "frozen", False) and sys.platform == "win32":
        # Don't fight an in-progress self-update: a relaunched exe here would
        # re-lock JobFinder.exe and break the updater's file copy. The updater
        # relaunches the new version itself when it finishes.
        if _update_in_progress(workspace):
            return 0
        # Single-instance: focus the existing window (reopen the browser) and
        # exit fast so this process doesn't hold a second lock on the exe image.
        if not _acquire_single_instance():
            if not os.environ.get("JOBFINDER_NO_BROWSER"):
                webbrowser.open(URL)
            return 0

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
        _run_tray(workspace)
    else:
        uvicorn.run(app, host=HOST, port=PORT, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
