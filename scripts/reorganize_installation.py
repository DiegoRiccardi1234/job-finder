"""Reversibly migrate an offline flat installation to the verified app/state layout.

No application is launched, no original is deleted, and no candidate state is
imported. Keep the returned backup until the separately authorized live check.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.install_layout import is_organized

_ROOT_ENTRIES = {"JobFinder.exe", "LEGGIMI.txt", "app", "state"}
_ROOT_STATE_FILES = (".env", ".env.local", "groq key.txt", "cv.md")
_MUTEX_NAME = r"Global\JobFinder_singleton"


class MigrationError(RuntimeError):
    """Stable error category, never the contents of a user file."""


@contextmanager
def installation_mutex() -> Iterator[None]:
    """Hold the same named mutex as the app, using pointer-sized Win32 handles."""
    if os.name != "nt":
        raise MigrationError("windows_mutex_required")
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel.ReleaseMutex.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    ctypes.set_last_error(0)
    handle = kernel.CreateMutexW(None, False, _MUTEX_NAME)
    already_exists = ctypes.get_last_error() == 183
    if not handle:
        raise MigrationError("mutex_unavailable")
    acquired = False
    try:
        # The application launcher keeps the HANDLE alive without acquiring
        # mutex ownership. WaitForSingleObject alone would incorrectly succeed
        # on that existing-but-unowned object and permit an unsafe migration.
        if already_exists:
            raise MigrationError("job_finder_is_running")
        wait = kernel.WaitForSingleObject(handle, 0)
        if wait == 258:  # WAIT_TIMEOUT: an application instance owns it.
            raise MigrationError("job_finder_is_running")
        if wait not in (0, 128):  # WAIT_OBJECT_0 / WAIT_ABANDONED
            raise MigrationError("mutex_unavailable")
        acquired = True
        yield
    finally:
        if acquired:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


def _redirected(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except FileNotFoundError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _resolve_checked(path: Path) -> Path:
    absolute = path.expanduser().absolute()
    if any(_redirected(part) for part in (absolute, *absolute.parents)):
        raise MigrationError("redirected_path")
    return absolute.resolve()


def _assert_tree_safe(root: Path) -> None:
    if _redirected(root):
        raise MigrationError("redirected_path")
    for directory, directories, files in os.walk(root, followlinks=False):
        for name in (*directories, *files):
            path = Path(directory) / name
            if _redirected(path):
                raise MigrationError("redirected_path")
            if not path.is_dir() and not path.is_file():
                raise MigrationError("unsupported_file_type")


def _related(first: Path, second: Path) -> bool:
    return first == second or first in second.parents or second in first.parents


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _manifest(root: Path) -> dict[str, str]:
    _assert_tree_safe(root)
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): _digest(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _manifest_digest(manifest: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(manifest, sort_keys=True).encode("utf-8")).hexdigest()


def _copy_entry(source: Path, destination: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, destination)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _copy_database(source: Path, destination: Path) -> Path:
    destination.mkdir(parents=True)
    for suffix in ("", "-wal", "-shm"):
        path = source.with_name(source.name + suffix)
        if path.is_file():
            shutil.copy2(path, destination / path.name)
    return destination / source.name


def _database_counts(database: Path, *, vacuum_destination: Path | None = None) -> dict[str, int]:
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=20)
    try:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise MigrationError("database_integrity_failed")
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        counts = {
            name: int(
                connection.execute(
                    'SELECT COUNT(*) FROM "' + name.replace('"', '""') + '"'
                ).fetchone()[0]
            )
            for (name,) in tables
        }
        if vacuum_destination is not None:
            connection.execute("VACUUM INTO ?", (str(vacuum_destination),))
        return counts
    finally:
        connection.close()


def _write_report(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    owned = False
    try:
        # Exclusive creation refuses even a dangling link or an unexpected
        # pre-existing file. Never follow or clean up a fixed report.tmp path.
        with temporary.open("x", encoding="utf-8") as stream:
            owned = True
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        temporary.replace(path)
    finally:
        if owned:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)


def _preflight(candidate: Path, target: Path, backup_root: Path, report: Path) -> None:
    paths = (candidate, target, backup_root)
    if any(
        _related(first, second)
        for index, first in enumerate(paths)
        for second in paths[index + 1 :]
    ):
        raise MigrationError("overlapping_paths")
    if any(
        report == source or source in report.parents or report in source.parents
        for source in (candidate, target)
    ):
        raise MigrationError("report_overlaps_installation")
    if not candidate.is_dir() or not target.is_dir():
        raise MigrationError("missing_bundle_directory")
    _assert_tree_safe(candidate)
    _assert_tree_safe(target)
    if backup_root.exists():
        _assert_tree_safe(backup_root)
    if target.drive.lower() != backup_root.drive.lower():
        raise MigrationError("backup_must_share_target_volume")
    ancestor = backup_root
    while not ancestor.exists():
        ancestor = ancestor.parent
    if target.stat().st_dev != ancestor.stat().st_dev:
        raise MigrationError("backup_must_share_target_volume")
    if {path.name for path in candidate.iterdir()} != _ROOT_ENTRIES or not is_organized(candidate):
        raise MigrationError("candidate_layout_invalid")
    for name in ("JobFinder.exe", "Updater.exe", "JobFinderMCP.exe"):
        if not (candidate / "app" / name).is_file():
            raise MigrationError("candidate_program_missing")
    if (
        not (candidate / "app" / "_internal").is_dir()
        or not (candidate / "JobFinder.exe").is_file()
        or not (candidate / "LEGGIMI.txt").is_file()
    ):
        raise MigrationError("candidate_program_missing")
    if any((candidate / "app" / name).exists() for name in ("data", "state")):
        raise MigrationError("candidate_contains_nested_state")
    if (
        (target / "app").exists()
        or (target / "state").exists()
        or not (target / "JobFinder.exe").is_file()
        or not (target / "_internal").is_dir()
    ):
        raise MigrationError("target_is_not_flat_installation")
    if not (target / "data" / "searcher.db").is_file():
        raise MigrationError("installed_database_missing")
    if (target / "data" / "update.lock").exists():
        raise MigrationError("update_lock_present")
    for name in _ROOT_STATE_FILES:
        old, existing = target / name, target / "data" / name
        if old.exists() and (
            not old.is_file()
            or (existing.exists() and (not existing.is_file() or _digest(old) != _digest(existing)))
        ):
            raise MigrationError("state_file_collision")


def reorganize_installation(
    candidate: Path, target: Path, backup_root: Path, report: Path
) -> dict[str, Any]:
    candidate, target, backup_root, report = (
        _resolve_checked(path) for path in (candidate, target, backup_root, report)
    )
    _preflight(candidate, target, backup_root, report)
    with installation_mutex():
        original = _manifest(target)
        data_before = _manifest(target / "data")
        vendor_before = _manifest(target / "vendor")
        candidate_before = _manifest(candidate)
        tag = datetime.now(UTC).strftime("%Y%m%d-%H%M%S") + "-organized-" + uuid.uuid4().hex[:8]
        backup = backup_root / tag
        backup.mkdir(parents=True)
        snapshot, retired = backup / "snapshot", backup / "retired-root"
        shutil.copytree(target, snapshot)
        if _manifest(snapshot) != original or _manifest(target) != original:
            raise MigrationError("full_backup_verification_failed")
        database_copy = _copy_database(snapshot / "data" / "searcher.db", backup / "sqlite-source")
        baseline_counts = _database_counts(
            database_copy, vacuum_destination=backup / "searcher-consistent.db"
        )
        if _database_counts(backup / "searcher-consistent.db") != baseline_counts:
            raise MigrationError("database_snapshot_verification_failed")

        # Stage only program material. Candidate state (including synthetic
        # setup sessions/DBs) is ignored. Existing vendor wins byte for byte.
        incoming = backup / "incoming-app"
        incoming.mkdir()
        for entry in (candidate / "app").iterdir():
            if entry.name == "vendor":
                continue
            _copy_entry(entry, incoming / entry.name)
        vendor_source = (
            target / "vendor" if (target / "vendor").exists() else candidate / "app" / "vendor"
        )
        if vendor_source.exists():
            _copy_entry(vendor_source, incoming / "vendor")
        expected_app = _manifest(incoming)
        expected_state = dict(data_before)
        for name in _ROOT_STATE_FILES:
            if (target / name).is_file():
                expected_state[name] = _digest(target / name)
        retired.mkdir()
        moves: list[tuple[Path, Path]] = []
        installed: list[Path] = []
        extra_state: list[Path] = []
        result: dict[str, Any] = {
            "ok": False,
            "target": str(target),
            "backup": str(backup),
            "baseline_counts": baseline_counts,
        }
        try:
            if _manifest(target) != original or _manifest(candidate) != candidate_before:
                raise MigrationError("files_changed_during_preparation")
            _assert_tree_safe(target)
            for entry in list(target.iterdir()):
                if entry.name == "data":
                    continue
                destination = retired / entry.name
                entry.rename(destination)
                moves.append((entry, destination))
            (target / "data").rename(target / "state")
            moves.append((target / "data", target / "state"))
            for name in _ROOT_STATE_FILES:
                source, destination = retired / name, target / "state" / name
                if source.is_file() and not destination.exists():
                    extra_state.append(destination)
                    shutil.copy2(source, destination)
            incoming.rename(target / "app")
            installed.append(target / "app")
            for name in ("JobFinder.exe", "LEGGIMI.txt"):
                installed.append(target / name)
                shutil.copy2(candidate / name, target / name)
            if {entry.name for entry in target.iterdir()} != _ROOT_ENTRIES:
                raise MigrationError("installed_root_layout_invalid")
            if not is_organized(target) or _manifest(target / "app") != expected_app:
                raise MigrationError("installed_program_verification_failed")
            final_state = _manifest(target / "state")
            if final_state != expected_state:
                raise MigrationError("installed_state_verification_failed")
            final_vendor = _manifest(target / "app" / "vendor")
            if (target / "vendor").exists() or (
                (snapshot / "vendor").exists()
                and _manifest(target / "app" / "vendor") != vendor_before
            ):
                raise MigrationError("installed_vendor_verification_failed")
            for name in ("JobFinder.exe", "LEGGIMI.txt"):
                if _digest(candidate / name) != _digest(target / name):
                    raise MigrationError("installed_launcher_verification_failed")
            verification_db = _copy_database(
                target / "state" / "searcher.db", backup / "sqlite-verification"
            )
            final_counts = _database_counts(verification_db)
            if final_counts != baseline_counts:
                raise MigrationError("installed_database_counts_changed")
            result.update(
                ok=True,
                layout="organized",
                state_hashes_preserved=True,
                vendor_hashes_preserved=True,
                original_data_sha256_before=_manifest_digest(data_before),
                original_data_sha256_after=_manifest_digest(
                    {name: final_state[name] for name in data_before}
                ),
                complete_state_sha256=_manifest_digest(final_state),
                vendor_sha256_before=_manifest_digest(vendor_before),
                vendor_sha256_after=_manifest_digest(final_vendor),
                vendor_source="installed" if (snapshot / "vendor").exists() else "candidate",
                original_data_files=len(data_before),
                final_state_files=len(expected_state),
                vendor_files=len(vendor_before),
                verified_program_files=len(expected_app),
                final_counts=final_counts,
                snapshot=str(snapshot),
                retired_root=str(retired),
                sqlite_snapshot=str(backup / "searcher-consistent.db"),
            )
            _write_report(report, result)
            return result
        except Exception as exc:
            failed = backup / "failed-attempt"
            rollback_complete = False
            try:
                for path in reversed(extra_state):
                    if path.exists():
                        destination = failed / "extra-state" / path.name
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        path.rename(destination)
                for path in reversed(installed):
                    if path.exists():
                        destination = failed / "program" / path.name
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        path.rename(destination)
                for old, current in reversed(moves):
                    if current.exists() and not old.exists():
                        current.rename(old)
                rollback_complete = _manifest(target) == original
            except Exception:
                rollback_complete = False
            result.update(ok=False, error=type(exc).__name__, rollback_complete=rollback_complete)
            with suppress(OSError):
                _write_report(report, result)
            raise MigrationError(
                "migration_failed_rolled_back"
                if rollback_complete
                else "migration_failed_manual_recovery_required"
            ) from None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("candidate", "target", "backup-root", "report"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        result = reorganize_installation(args.candidate, args.target, args.backup_root, args.report)
    except (MigrationError, OSError, sqlite3.Error) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": str(exc) if isinstance(exc, MigrationError) else type(exc).__name__,
                }
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
