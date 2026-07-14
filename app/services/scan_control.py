"""Coordinates the single in-flight scan.

A non-blocking lock ensures a manual scan and the auto-scan (or two browser
tabs) can't run concurrently — overlapping runs each call ``begin_scan`` and
double-spend the (free-tier) LLM quota. A cancel flag lets the UI stop a run
promptly instead of the server churning on after the user clicked stop.
"""

from __future__ import annotations

import contextlib
import threading


class ScanControl:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancel = threading.Event()

    def try_begin(self) -> bool:
        """Acquire the single-scan slot. Returns False if one is already running.
        Clears any stale cancel flag on success. Always pair with ``end()``."""
        if self._lock.acquire(blocking=False):
            self._cancel.clear()
            return True
        return False

    def end(self) -> None:
        self._cancel.clear()
        if self._lock.locked():
            with contextlib.suppress(RuntimeError):  # already released elsewhere
                self._lock.release()

    def cancel(self) -> None:
        self._cancel.set()

    def is_cancelled(self) -> bool:
        return self._cancel.is_set()

    @property
    def running(self) -> bool:
        return self._lock.locked()
