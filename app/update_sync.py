"""File-sync primitive used by the standalone updater.

Copies every file from a freshly-extracted bundle directory into the
install directory, skipping a small whitelist of user-owned paths
(``state/``, ``data/``, ``vendor/``, ``.env``, ``.env.local``). The point is that the SQLite
DB, uploaded CV, API keys, and any environment overrides survive an
update untouched.

This module is import-clean (no side effects) so the unit tests can
exercise ``sync_install_dir`` without going through the rest of the
binary.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.install_layout import LAYOUT_MARKER, is_organized

# Top-level paths in the install dir that the updater must NEVER overwrite
# or delete. Match by path component, so ``data/searcher.db`` is preserved
# because ``data`` is in this set.
PRESERVE_TOPLEVEL = ("state", "data", "vendor", ".env", ".env.local")


def _is_preserved(rel_path: Path, *, preserve_vendor: bool = True) -> bool:
    parts = rel_path.parts
    return (
        bool(parts)
        and parts[0].casefold() in PRESERVE_TOPLEVEL
        and (preserve_vendor or parts[0].casefold() != "vendor")
    )


def _install_mappings(source: Path, target: Path) -> Iterator[tuple[Path, Path]]:
    """Map release program files without migrating an existing user's layout.

    Organized releases have a launcher at their root and the actual runtime
    under app/. An old installation must keep its flat runtime; conversely, a
    flat release must never overwrite an organized installation's launcher.
    """
    source_organized = is_organized(source)
    target_empty = not target.exists() or not any(target.iterdir())
    target_organized = is_organized(target) or (target_empty and source_organized)
    for src in source.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(source)
        if _is_preserved(rel, preserve_vendor=source_organized):
            continue
        vendor_file = rel.parts[0].casefold() == "vendor"
        if source_organized:
            if rel.parts[0].casefold() == "app":
                runtime_rel = Path(*rel.parts[1:])
                if _is_preserved(runtime_rel, preserve_vendor=False):
                    continue
                vendor_file = runtime_rel.parts[0].casefold() == "vendor"
                dest_rel = Path("app") / runtime_rel if target_organized else runtime_rel
            else:
                if not target_organized and rel.name.casefold() == "jobfinder.exe":
                    continue  # The legacy runtime comes from source/app/JobFinder.exe.
                dest_rel = rel
        else:
            dest_rel = Path("app") / rel if target_organized else rel
        destination = target / dest_rel
        if vendor_file and destination.exists():
            continue  # Ship new bundled tools, never replace a user's runtime.
        yield src, destination


class UpdateInProgressError(RuntimeError):
    def __init__(self, age_seconds: int, ttl_seconds: int) -> None:
        super().__init__("update_already_in_progress")
        self.age_seconds = age_seconds
        self.ttl_seconds = ttl_seconds


_UPDATE_LOCK_MUTEX = threading.Lock()


@contextmanager
def _update_lock_guard(path: Path) -> Iterator[None]:
    """Serialize stale takeover and release, including between app/updater processes.

    This short-lived OS lock is released by Windows/POSIX on process exit. The
    separate capability lock persists while the updater runs from staging.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    guard = path.with_name(path.name + ".guard")
    with _UPDATE_LOCK_MUTEX, guard.open("a+b") as handle:
        if handle.seek(0, 2) == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def claim_update_lock(path: Path, version: str, ttl_seconds: int = 180) -> str:
    """Atomically acquire a persistent updater lock; return its private owner ID."""
    with _update_lock_guard(path):
        if path.exists():
            age = max(0, int(time.time() - path.stat().st_mtime))
            if age < ttl_seconds:
                raise UpdateInProgressError(age, ttl_seconds)
            path.unlink()  # Stale takeover is serialized with every cooperating releaser.
        owner = secrets.token_urlsafe(32)
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise UpdateInProgressError(0, ttl_seconds) from None
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump({"owner": owner, "pid": os.getpid(), "version": version}, output)
        return owner


def release_update_lock(
    path: Path, owner: str | None = None, *, legacy_parent_pid: int | None = None
) -> bool:
    """Release our own lock only; an old updater must not delete a successor's lock."""
    with _update_lock_guard(path):
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return False
        try:
            value = json.loads(raw)
        except ValueError:
            value = None
        if owner:
            owned = isinstance(value, dict) and value.get("owner") == owner
        else:
            # Compatibility with a legacy router's `pid\nversion\n` lock.
            owned = (
                value is None
                and legacy_parent_pid is not None
                and (raw.splitlines()[0] if raw.splitlines() else "") == str(legacy_parent_pid)
            )
        if not owned:
            return False
        path.unlink()
        return True


def _is_current_executable(dst: Path) -> bool:
    """True when ``dst`` is the EXE running this Python process.

    Defense-in-depth: the launcher already copies Updater.exe to %TEMP%
    before spawn so the install-dir copy is unlocked, but if that fails
    silently we still must not try to overwrite our own running binary.
    """
    try:
        return dst.resolve() == Path(sys.executable).resolve()
    except OSError:
        return False


_COPY_RETRY_DELAYS = (1.0, 2.0, 4.0, 8.0, 16.0)


def _copy_with_retry(src: Path, dst: Path) -> None:
    """Copy ``src`` to ``dst`` with retries on PermissionError.

    Windows holds file locks via antivirus scans (Defender on a fresh
    extract can take 10-20 s) or just-exited processes whose handles
    are still draining. Without retries, the updater aborts on the
    first transient lock and leaves the install dir half-updated.
    Retry up to 5 times with 1s/2s/4s/8s/16s backoff (~31 s total)
    so the final error message includes the specific file that stayed
    locked, which is invaluable for diagnosing antivirus interference.
    """
    for delay in _COPY_RETRY_DELAYS:
        try:
            shutil.copy2(src, dst)
            return
        except PermissionError:
            time.sleep(delay)
    try:
        shutil.copy2(src, dst)
    except PermissionError as exc:
        # Augment the error with the specific destination path so logs
        # tell us which file stayed locked across all retries (typically
        # JobFinder.exe itself or a recent child it spawned).
        raise PermissionError(
            exc.errno,
            f"{exc.strerror} (locked after {len(_COPY_RETRY_DELAYS)} retries): {dst}",
        ) from exc


def sync_install_dir(*, source: Path, target: Path) -> int:
    """Copy program files, preserving organized/legacy target layout and state.

    Returns the number of files written. Files matching
    ``PRESERVE_TOPLEVEL`` are skipped on the source side so user data
    that happens to ship inside the bundle (it shouldn't, but defensive)
    can never clobber the user's real data.
    """
    if not source.exists():
        raise FileNotFoundError(f"source bundle not found: {source}")
    if not source.is_dir():
        raise NotADirectoryError(f"source bundle is not a directory: {source}")
    for root in (source, target):
        if (root / "app" / LAYOUT_MARKER).exists() and not is_organized(root):
            raise ValueError(f"unrecognized installation layout: {root}")
    source_root, target_root = source.resolve(), target.resolve()
    if (
        source_root == target_root
        or source_root in target_root.parents
        or target_root in source_root.parents
    ):
        raise ValueError("source and target must be separate directory trees")

    # Resolve mappings before creating target, or a new organized destination
    # would appear as a pre-existing legacy installation during detection.
    mappings = list(_install_mappings(source, target))
    for src, dst in mappings:
        if src.is_symlink() or not src.resolve().is_relative_to(source_root):
            raise ValueError(f"source file escapes the bundle: {src}")
        if not dst.resolve().is_relative_to(target_root):
            raise ValueError(f"destination escapes the installation: {dst}")
    target.mkdir(parents=True, exist_ok=True)

    written = 0
    for src, dst in mappings:
        if _is_current_executable(dst):
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        _copy_with_retry(src, dst)
        written += 1
    return written
