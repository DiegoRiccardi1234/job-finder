"""Process-wide desktop-notification registry.

Bridges the system-tray icon (registered from ``scripts/launch_exe`` on the main
thread) and the auto-scan scheduler (a daemon thread deep in the container).
Both sides depend only on this tiny module — no app internals — so there is no
import cycle. When nothing is registered (dev/source runs, or pystray
unavailable) ``notify`` is a safe no-op.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable

_lock = threading.Lock()
_notifier: Callable[[str, str], None] | None = None


def register_notifier(fn: Callable[[str, str], None]) -> None:
    """Install the process notifier (title, message) -> None. Called by the tray."""
    global _notifier
    with _lock:
        _notifier = fn


def notify(title: str, message: str) -> None:
    """Fire a desktop notification, or no-op when no notifier is registered.

    Never raises — a tray/notification hiccup must not break the caller (a scan).
    """
    with _lock:
        fn = _notifier
    if fn is None:
        return
    with contextlib.suppress(Exception):
        fn(title, message)
