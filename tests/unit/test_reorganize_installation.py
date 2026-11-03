"""Migration proofs run only on disposable fixtures, never installed data."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pytest

from scripts import reorganize_installation as migration


@pytest.fixture
def bundles(tmp_path: Path, monkeypatch):
    candidate, target, backup = (tmp_path / name for name in ("candidate", "installed", "backups"))
    report = tmp_path / "report.json"
    (candidate / "app" / "_internal").mkdir(parents=True)
    (candidate / "app" / "_internal" / "new.dll").write_bytes(b"new-runtime")
    (candidate / "app" / ".jobfinder-layout.json").write_text('{"version":1}', encoding="utf-8")
    for name in ("JobFinder.exe", "Updater.exe", "JobFinderMCP.exe"):
        (candidate / "app" / name).write_bytes(b"new-" + name.encode())
    (candidate / "JobFinder.exe").write_bytes(b"thin-launcher")
    (candidate / "LEGGIMI.txt").write_text("new instructions", encoding="utf-8")
    (candidate / "state").mkdir()
    (candidate / "state" / "synthetic-secret.json").write_text('{"fixture":true}', encoding="utf-8")
    (candidate / "app" / "vendor").mkdir()
    (candidate / "app" / "vendor" / "new-vendor.bin").write_bytes(
        b"candidate-vendor-must-not-replace-user-vendor"
    )

    (target / "_internal").mkdir(parents=True)
    (target / "_internal" / "old.dll").write_bytes(b"old-runtime")
    (target / "data").mkdir()
    (target / "data" / "uploads").mkdir()
    (target / "data" / "uploads" / "personal-cv.md").write_text(
        "synthetic CV fixture", encoding="utf-8"
    )
    (target / "data" / "local_secrets.json").write_text(
        '{"key":"synthetic-private"}', encoding="utf-8"
    )
    (target / ".env").write_text("KEY=synthetic-root-setting\n", encoding="utf-8")
    (target / "groq key.txt").write_text("synthetic-groq-key", encoding="utf-8")
    (target / "user-notes.txt").write_text("retain original outside root", encoding="utf-8")
    (target / "vendor" / "tesseract").mkdir(parents=True)
    (target / "vendor" / "tesseract" / "tesseract.exe").write_bytes(b"existing-user-vendor")
    for name in ("JobFinder.exe", "Updater.exe", "LEGGIMI.txt"):
        (target / name).write_bytes(b"old-" + name.encode())
    connection = sqlite3.connect(target / "data" / "searcher.db")
    connection.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, title TEXT)")
    connection.executemany("INSERT INTO jobs(title) VALUES (?)", [("one",), ("two",)])
    connection.execute("CREATE TABLE preferences (key TEXT, value TEXT)")
    connection.execute("INSERT INTO preferences VALUES ('locale','it')")
    connection.commit()
    connection.close()

    @contextmanager
    def fake_mutex():
        yield

    monkeypatch.setattr(migration, "installation_mutex", fake_mutex)
    return candidate, target, backup, report


def test_migration_preserves_data_vendor_sqlite_and_every_retired_original(bundles) -> None:
    candidate, target, backup_root, report = bundles
    before = migration._manifest(target)
    candidate_before = migration._manifest(candidate)
    data = migration._manifest(target / "data")
    vendor = migration._manifest(target / "vendor")
    result = migration.reorganize_installation(candidate, target, backup_root, report)
    backup = Path(result["backup"])
    assert result["ok"] is True
    assert {path.name for path in target.iterdir()} == {
        "JobFinder.exe",
        "LEGGIMI.txt",
        "app",
        "state",
    }
    assert migration._manifest(backup / "snapshot") == before
    assert migration._manifest(candidate) == candidate_before
    assert migration._manifest(target / "app" / "vendor") == vendor
    assert not (target / "app" / "vendor" / "new-vendor.bin").exists()
    assert not (target / "state" / "synthetic-secret.json").exists()
    assert not (target / "state" / "data").exists()
    assert {key: migration._manifest(target / "state")[key] for key in data} == data
    assert (target / "state" / ".env").read_text() == "KEY=synthetic-root-setting\n"
    assert (target / "state" / "groq key.txt").read_text() == "synthetic-groq-key"
    assert (backup / "retired-root" / "JobFinder.exe").read_bytes() == b"old-JobFinder.exe"
    assert (backup / "retired-root" / "_internal" / "old.dll").read_bytes() == b"old-runtime"
    assert (backup / "retired-root" / "user-notes.txt").is_file()
    assert (backup / "searcher-consistent.db").is_file()
    assert result["baseline_counts"] == result["final_counts"] == {"jobs": 2, "preferences": 1}
    assert result["original_data_sha256_before"] == result["original_data_sha256_after"]
    assert result["vendor_sha256_before"] == result["vendor_sha256_after"]
    assert json.loads(report.read_text())["state_hashes_preserved"] is True


def test_mutex_refuses_open_application_before_backup_or_mutation(bundles, monkeypatch) -> None:
    candidate, target, backup, report = bundles
    before = migration._manifest(target)

    @contextmanager
    def busy_mutex():
        raise migration.MigrationError("job_finder_is_running")
        yield

    monkeypatch.setattr(migration, "installation_mutex", busy_mutex)
    with pytest.raises(migration.MigrationError, match="job_finder_is_running"):
        migration.reorganize_installation(candidate, target, backup, report)
    assert migration._manifest(target) == before
    assert not backup.exists()


def test_partial_root_file_copy_rolls_back_data_and_program_without_deleting_originals(
    bundles, monkeypatch
) -> None:
    candidate, target, backup, report = bundles
    before = migration._manifest(target)
    copy = migration.shutil.copy2

    def fail_partial(source, destination, *args, **kwargs):
        if Path(destination) == target / "LEGGIMI.txt":
            Path(destination).write_bytes(b"partially-written-new-file")
            raise OSError("synthetic copy failure")
        return copy(source, destination, *args, **kwargs)

    monkeypatch.setattr(migration.shutil, "copy2", fail_partial)
    with pytest.raises(migration.MigrationError, match="rolled_back"):
        migration.reorganize_installation(candidate, target, backup, report)
    assert migration._manifest(target) == before
    saved = json.loads(report.read_text())
    assert saved["rollback_complete"] is True
    assert (Path(saved["backup"]) / "snapshot" / "data" / "searcher.db").is_file()
    assert (
        Path(saved["backup"]) / "failed-attempt" / "program" / "LEGGIMI.txt"
    ).read_bytes() == b"partially-written-new-file"


def test_report_failure_also_rolls_back_verified_installation(bundles, monkeypatch) -> None:
    candidate, target, backup, report = bundles
    before = migration._manifest(target)
    writer = migration._write_report

    def fail_success_report(path, value):
        if value["ok"]:
            raise OSError("synthetic report failure")
        writer(path, value)

    monkeypatch.setattr(migration, "_write_report", fail_success_report)
    with pytest.raises(migration.MigrationError, match="rolled_back"):
        migration.reorganize_installation(candidate, target, backup, report)
    assert migration._manifest(target) == before


def test_fixed_dangling_report_tmp_is_ignored_without_corrupting_migrated_database(bundles) -> None:
    candidate, target, backup, report = bundles
    old_temporary = report.with_name(report.name + ".tmp")
    try:
        old_temporary.symlink_to(target / "state" / "searcher.db")
    except OSError:
        pytest.skip("File symlinks are unavailable for this Windows account")
    assert old_temporary.is_symlink() and not old_temporary.exists()
    result = migration.reorganize_installation(candidate, target, backup, report)
    assert result["ok"] is True
    assert old_temporary.is_symlink()
    assert old_temporary.readlink() == target / "state" / "searcher.db"
    assert (target / "state" / "searcher.db").read_bytes().startswith(b"SQLite format 3")
    assert json.loads(report.read_text())["ok"] is True


def test_fixed_report_tmp_hardlink_cannot_overwrite_database_after_successful_checks(
    bundles,
) -> None:
    candidate, target, backup, report = bundles
    old_temporary = report.with_name(report.name + ".tmp")
    # Hardlinks need no Windows symlink privilege and retain the same database
    # inode after data/ is renamed to state/: the old fixed-name writer would
    # overwrite SQLite with JSON after all preservation checks had passed.
    os.link(target / "data" / "searcher.db", old_temporary)
    result = migration.reorganize_installation(candidate, target, backup, report)
    assert result["ok"] is True
    assert old_temporary.samefile(target / "state" / "searcher.db")
    assert old_temporary.read_bytes().startswith(b"SQLite format 3")
    assert migration._database_counts(target / "state" / "searcher.db") == {
        "jobs": 2,
        "preferences": 1,
    }


def test_exclusive_report_creation_does_not_overwrite_or_cleanup_an_existing_collision(
    tmp_path, monkeypatch
) -> None:
    from types import SimpleNamespace

    report = tmp_path / "report.json"
    report.write_text("previous-report", encoding="utf-8")
    victim = tmp_path / "original.txt"
    victim.write_text("preserve-original", encoding="utf-8")
    collision = tmp_path / "report.json.fixture.tmp"
    os.link(victim, collision)
    monkeypatch.setattr(migration.uuid, "uuid4", lambda: SimpleNamespace(hex="fixture"))
    with pytest.raises(FileExistsError):
        migration._write_report(report, {"ok": True})
    assert victim.read_text() == collision.read_text() == "preserve-original"
    assert report.read_text() == "previous-report"


def test_report_serialization_failure_cleans_only_its_own_partial_file(tmp_path) -> None:
    report = tmp_path / "report.json"
    old_temporary = tmp_path / "report.json.tmp"
    old_temporary.write_text("unrelated-sentinel", encoding="utf-8")
    with pytest.raises(TypeError):
        migration._write_report(report, {"unsupported": object()})
    assert not report.exists()
    assert list(tmp_path.glob("report.json.*.tmp")) == []
    assert old_temporary.read_text() == "unrelated-sentinel"


@pytest.mark.parametrize(
    "relationship",
    [
        "same",
        "candidate-in-target",
        "target-in-candidate",
        "backup-in-target",
        "backup-ancestor",
        "report-in-target",
    ],
)
def test_rejects_overlapping_paths_before_any_backup(bundles, relationship: str) -> None:
    candidate, target, backup, report = bundles
    if relationship == "same":
        candidate = target
    elif relationship == "candidate-in-target":
        candidate = target / "nested"
    elif relationship == "target-in-candidate":
        target = candidate / "nested"
    elif relationship == "backup-in-target":
        backup = target / "backups"
    elif relationship == "backup-ancestor":
        backup = target.parent
    else:
        report = target / "migration.json"
    with pytest.raises(migration.MigrationError, match="overlap"):
        migration.reorganize_installation(candidate, target, backup, report)


def test_existing_state_name_is_not_overwritten(bundles) -> None:
    candidate, target, backup, report = bundles
    (target / "state").mkdir()
    with pytest.raises(migration.MigrationError, match="not_flat"):
        migration.reorganize_installation(candidate, target, backup, report)


def test_conflicting_root_config_is_retained_and_requires_resolution(bundles) -> None:
    candidate, target, backup, report = bundles
    (target / "data" / ".env").write_text("KEY=another-synthetic-setting\n", encoding="utf-8")
    before = migration._manifest(target)
    with pytest.raises(migration.MigrationError, match="state_file_collision"):
        migration.reorganize_installation(candidate, target, backup, report)
    assert migration._manifest(target) == before
    assert not backup.exists()


def test_invalid_marker_or_nested_candidate_state_is_refused(bundles) -> None:
    candidate, target, backup, report = bundles
    (candidate / "app" / "data").mkdir()
    with pytest.raises(migration.MigrationError, match="nested_state"):
        migration.reorganize_installation(candidate, target, backup, report)


def test_redirected_entries_are_refused_without_following_them(bundles, monkeypatch) -> None:
    candidate, target, backup, report = bundles
    redirected = target / "data" / "uploads"
    original = migration._redirected
    monkeypatch.setattr(migration, "_redirected", lambda path: path == redirected or original(path))
    with pytest.raises(migration.MigrationError, match="redirected_path"):
        migration.reorganize_installation(candidate, target, backup, report)
    assert redirected.is_dir()
    assert not backup.exists()


@pytest.mark.skipif(os.name != "nt", reason="Win32 HANDLE contract")
def test_existing_unowned_launcher_mutex_is_refused_even_if_wait_would_succeed(monkeypatch) -> None:
    from types import SimpleNamespace

    closed, waits = [], []
    large_handle = 0x100009876

    def existing_mutex(*args):
        migration.ctypes.set_last_error(183)
        return large_handle

    kernel = SimpleNamespace(
        CreateMutexW=existing_mutex,
        WaitForSingleObject=lambda *args: waits.append(args) or 0,
        ReleaseMutex=lambda handle: pytest.fail("Existing launcher mutex must not be released"),
        CloseHandle=lambda handle: closed.append(handle),
    )
    monkeypatch.setattr(migration.ctypes, "WinDLL", lambda *args, **kwargs: kernel)
    with pytest.raises(migration.MigrationError, match="job_finder_is_running"):
        with migration.installation_mutex():
            pytest.fail("Existing unowned object must block migration")
    assert waits == []
    assert closed == [large_handle]


@pytest.mark.skipif(os.name != "nt", reason="Win32 HANDLE contract")
def test_mutex_closes_full_width_handle_when_wait_fails(monkeypatch) -> None:
    from ctypes import wintypes
    from types import SimpleNamespace

    large_handle = 0x100001234
    closed, released = [], []
    kernel = SimpleNamespace(
        CreateMutexW=lambda *args: large_handle,
        WaitForSingleObject=lambda *args: 0xFFFFFFFF,
        ReleaseMutex=lambda handle: released.append(handle),
        CloseHandle=lambda handle: closed.append(handle),
    )
    monkeypatch.setattr(migration.ctypes, "WinDLL", lambda *args, **kwargs: kernel)
    with pytest.raises(migration.MigrationError, match="mutex_unavailable"):
        with migration.installation_mutex():
            pytest.fail("Failed wait must never enter migration")
    assert kernel.CreateMutexW.restype is wintypes.HANDLE
    assert kernel.WaitForSingleObject.argtypes[0] is wintypes.HANDLE
    assert closed == [large_handle]
    assert released == []


@pytest.mark.skipif(os.name != "nt", reason="Win32 handle ownership proof")
def test_real_win32_mutex_remains_held_until_context_exit(monkeypatch) -> None:
    # Dedicated test-only name: never contend with the user's installed app.
    monkeypatch.setattr(
        migration, "_MUTEX_NAME", r"Local\JobFinder_migration_fixture_" + str(os.getpid())
    )
    # Re-entering a Windows mutex in the same thread is recursive, so use a
    # second thread to prove contention instead of depending on GetLastError.
    import threading

    errors = []
    with migration.installation_mutex():

        def contender():
            try:
                with migration.installation_mutex():
                    errors.append("unexpected acquisition")
            except migration.MigrationError as exc:
                errors.append(str(exc))

        thread = threading.Thread(target=contender)
        thread.start()
        thread.join(timeout=3)
        assert errors == ["job_finder_is_running"]
    with migration.installation_mutex():
        pass
