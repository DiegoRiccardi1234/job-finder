"""Install a verified local bundle while preserving all runtime data.

No network, application launch, account changes or runtime installation. The
caller must close Job Finder first and verify the installed app afterwards.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--backup-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    candidate, target, backup_root = (p.resolve() for p in (args.candidate, args.target, args.backup_root))
    if candidate == target or target in backup_root.parents or candidate in backup_root.parents:
        raise RuntimeError("Unsafe bundle/backup paths")
    if not (candidate / "JobFinderMCP.exe").is_file() or not (target / "JobFinder.exe").is_file():
        raise RuntimeError("Expected candidate and existing installed bundle")
    for path in (args.target, target / "_internal", target / "data"):
        if path.is_symlink():
            raise RuntimeError("Refusing a redirected installation path")
    if (target / "data" / "update.lock").exists():
        raise RuntimeError("An existing updater lock must be resolved first")
    tag = datetime.now().strftime("%Y%m%d-%H%M%S") + "-agent-pilot"
    backup = backup_root / tag
    backup_root.mkdir(parents=True, exist_ok=True)
    # Backup before writing even the transient update lock. A VACUUM snapshot
    # remains independently restorable if SQLite WAL files were present.
    shutil.copytree(target, backup)
    database = target / "data" / "searcher.db"
    with sqlite3.connect(database, timeout=20) as connection:
        connection.execute("VACUUM INTO ?", (str(backup / "searcher-consistent.db"),))
        baseline = {table: connection.execute(f"SELECT COUNT(1) FROM {table}").fetchone()[0]
                    for table in ("jobs", "job_actions", "candidate_profiles", "preferences")}
    preserve = {str(p.relative_to(target)): digest(p) for p in target.rglob("*")
                if p.is_file() and (p.relative_to(target).parts[0] in {"data", "vendor"} or p.name == ".env")}
    lock = target / "data" / "update.lock"
    lock.write_text(json.dumps({"owner": tag}), encoding="utf-8")
    try:
        for name in ("JobFinder.exe", "Updater.exe", "JobFinderMCP.exe"):
            shutil.copy2(candidate / name, target / name)
        shutil.copytree(candidate / "_internal", target / "_internal", dirs_exist_ok=True)
        files = [p for p in candidate.rglob("*") if p.is_file() and p.relative_to(candidate).parts[0] != "data"]
        for source in files:
            destination = target / source.relative_to(candidate)
            if not destination.is_file() or digest(source) != digest(destination):
                raise RuntimeError("Installed bundle differs from verified candidate")
        if any(digest(target / name) != expected for name, expected in preserve.items()):
            raise RuntimeError("Runtime data changed during file installation")
    except Exception:
        # Restore program files, never overwrite the user's data directory.
        for name in ("JobFinder.exe", "Updater.exe"):
            shutil.copy2(backup / name, target / name)
        shutil.copytree(backup / "_internal", target / "_internal", dirs_exist_ok=True)
        raise
    finally:
        if lock.exists() and json.loads(lock.read_text())["owner"] == tag:
            lock.unlink()
    result = {"ok": True, "target": str(target), "backup": str(backup),
              "baseline_counts": baseline, "runtime_files_preserved": len(preserve),
              "verified_program_files": len(files)}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
