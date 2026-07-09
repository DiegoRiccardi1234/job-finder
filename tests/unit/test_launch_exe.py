"""Tests for the frozen entrypoint ``scripts.launch_exe``.

Regression coverage for the self-update relaunch crash: JobFinder.exe built
``console=True`` and relaunched by the updater with ``DETACHED_PROCESS`` lands
with ``sys.stdout``/``sys.stderr`` bound to invalid OS handles. The first write
(``print`` at startup, then uvicorn's logging) raised ``OSError [WinError 6]``
and killed the process *after* importing the app but *before* uvicorn bound the
port — so the update modal hung at "Restart 95%" forever. ``_harden_stdio``
rebinds broken/None streams so startup can never die on a stdout write.
"""

from __future__ import annotations

import sys
from pathlib import Path

from scripts.launch_exe import _harden_stdio, _open_browser_when_ready, main


class _DeadStdout:
    """A console stream over an invalid OS handle.

    Writing raises and ``os.fstat(fileno())`` fails — exactly the state a
    ``console=True`` exe lands in when relaunched with ``DETACHED_PROCESS``.
    """

    def write(self, *_args: object, **_kwargs: object) -> int:
        raise OSError(6, "The handle is invalid")

    def flush(self) -> None:
        raise OSError(6, "The handle is invalid")

    def fileno(self) -> int:
        return -1  # os.fstat(-1) raises → detectable as a dead fd


def test_harden_stdio_replaces_none_streams(monkeypatch) -> None:
    """A windowed (console=False) build sets stdout/stderr to None."""
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    _harden_stdio()

    # Neither of these may raise now.
    print("goes to devnull")
    sys.stdout.write("x")
    sys.stdout.flush()
    sys.stderr.write("y")
    sys.stderr.flush()


def test_harden_stdio_replaces_dead_handle(monkeypatch) -> None:
    """The DETACHED_PROCESS console case: real stream, invalid handle."""
    monkeypatch.setattr(sys, "stdout", _DeadStdout())
    monkeypatch.setattr(sys, "stderr", _DeadStdout())

    _harden_stdio()

    sys.stdout.write("x")
    sys.stdout.flush()
    sys.stderr.write("y")
    sys.stderr.flush()


def test_harden_stdio_keeps_working_stream(monkeypatch, tmp_path: Path) -> None:
    """A usable stream (real fd, fstat ok) must be left untouched."""
    with open(tmp_path / "out.txt", "w", encoding="utf-8") as f:
        monkeypatch.setattr(sys, "stdout", f)
        _harden_stdio()
        assert sys.stdout is f


def test_open_browser_skipped_on_update_relaunch(monkeypatch) -> None:
    """After a self-update the updater relaunches with JOBFINDER_UPDATED=1; the
    existing tab reloads itself, so opening another tab here would duplicate it.
    ``_open_browser_when_ready`` must return without calling ``webbrowser.open``.
    """
    import scripts.launch_exe as le

    opened: list[str] = []
    monkeypatch.setattr(le.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setenv("JOBFINDER_UPDATED", "1")
    monkeypatch.delenv("JOBFINDER_NO_BROWSER", raising=False)

    _open_browser_when_ready()

    assert opened == []


def test_main_survives_dead_stdout(monkeypatch, tmp_path: Path) -> None:
    """The end-to-end regression: main() must reach uvicorn.run even when the
    process was handed dead stdout/stderr (updater relaunch), instead of dying
    on the startup print()."""
    import scripts.launch_exe as le

    # A valid workspace so a fresh ``app.main`` import (if this test runs before
    # any other that imports it) can mount StaticFiles against tmp_path/web.
    (tmp_path / "web").mkdir(exist_ok=True)
    (tmp_path / "data").mkdir(exist_ok=True)

    monkeypatch.setattr(le, "_resolve_workspace", lambda: tmp_path)
    monkeypatch.setattr(le, "_open_browser_when_ready", lambda: None)
    monkeypatch.setenv("JOBFINDER_WORKSPACE", str(tmp_path))

    ran: dict[str, bool] = {}

    def fake_run(_app: object, **_kwargs: object) -> None:
        ran["called"] = True

    monkeypatch.setattr("uvicorn.run", fake_run)
    monkeypatch.setattr(sys, "stdout", _DeadStdout())
    monkeypatch.setattr(sys, "stderr", _DeadStdout())

    rc = main()

    assert ran.get("called") is True
    assert rc == 0
