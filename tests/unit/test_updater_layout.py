"""Updater compatibility with organized releases, legacy installations and staging."""

from __future__ import annotations

import json
import os
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from scripts import updater
from app.update_sync import UpdateInProgressError, claim_update_lock, release_update_lock


def write(path: Path, content: str = "placeholder") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def organized(root: Path) -> None:
    write(root / "app/.jobfinder-layout.json", '{"version":1}')
    write(root / "JobFinder.exe", "old-launcher")
    write(root / "app/JobFinder.exe", "old-runtime")
    write(root / "app/Updater.exe", "old-updater")
    write(root / "app/_internal/python311.dll", "runtime-dll")


@pytest.mark.parametrize("combined", [False, True])
@pytest.mark.parametrize("layout", ["organized", "legacy"])
def test_staged_updater_bootstrap_finds_install_runtime(tmp_path, monkeypatch, combined, layout):
    root = tmp_path / "install"
    if layout == "organized":
        organized(root)
        expected = root / "app/_internal"
    else:
        expected = root / "_internal"
        expected.mkdir(parents=True)
    staged = tmp_path / "staged/Updater.exe"
    write(staged)
    args = ["updater", f"--install-dir={root}"] if combined else ["updater", "--install-dir", str(root)]
    monkeypatch.setattr(updater.sys, "frozen", True, raising=False)
    monkeypatch.setattr(updater.sys, "executable", str(staged))
    monkeypatch.setattr(updater.sys, "argv", args)
    monkeypatch.setattr(updater.sys, "path", list(updater.sys.path))
    updater._bootstrap_internal_path()
    assert updater.sys.path[0] == str(expected.resolve())


@pytest.mark.parametrize("layout", ["organized", "legacy"])
def test_updater_log_and_lock_use_the_existing_user_state(tmp_path, layout):
    root = tmp_path / "install"
    if layout == "organized":
        organized(root)
        expected = root / "state/logs/updater.log"
        argument = root / "app"
    else:
        root.mkdir()
        expected = root / "data/logs/updater.log"
        argument = root
    with updater._open_log(argument) as log:
        log.write("diagnostic")
    assert expected.read_text() == "diagnostic"


@pytest.mark.parametrize("wrapped", [False, True])
def test_bundle_root_does_not_strip_the_organized_app_directory(tmp_path, wrapped):
    extracted = tmp_path / "extract"
    source = extracted / "JobFinder" if wrapped else extracted
    organized(source)
    assert updater._bundle_root(extracted) == source


@pytest.mark.parametrize("member", ["../escape", "/absolute", "C:/drive", "app/file:stream", "..\\escape"])
def test_extract_rejects_unsafe_members_before_writing_program_files(tmp_path, member):
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("JobFinder.exe", "must-not-write")
        bundle.writestr(member, "bad")
    target = tmp_path / "staging"
    with pytest.raises(RuntimeError, match="unsafe path"):
        updater._extract_bundle(archive, target)
    assert not (target / "JobFinder.exe").exists()


@pytest.mark.parametrize("release", ["organized", "legacy"])
def test_update_restarts_outer_launcher_and_preserves_state(tmp_path, monkeypatch, release):
    root = tmp_path / "install"
    organized(root)
    write(root / "state/searcher.db", "USER ARCHIVE")
    write(root / "state/update.lock", "123\n2.5.0\n")
    write(root / "data/update.lock", "LEGACY RESIDUAL MUST STAY")
    archive = tmp_path / "new.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        if release == "organized":
            bundle.writestr("JobFinder/JobFinder.exe", "new-launcher")
            bundle.writestr("JobFinder/app/.jobfinder-layout.json", '{"version":1}')
            bundle.writestr("JobFinder/app/JobFinder.exe", "new-runtime")
            bundle.writestr("JobFinder/state/searcher.db", "DO NOT COPY")
        else:
            bundle.writestr("JobFinder/JobFinder.exe", "new-runtime")
            bundle.writestr("JobFinder/data/searcher.db", "DO NOT COPY")
    starts = []
    monkeypatch.setattr(updater.sys, "argv", [
        "updater", "--install-dir", str(root / "app"), "--parent-pid", "123", "--zip", str(archive),
    ])
    monkeypatch.setattr(updater, "_wait_for_pid", lambda pid: None)
    monkeypatch.setattr(updater.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(updater.subprocess, "Popen", lambda args, **kwargs: starts.append((args, kwargs)))
    assert updater.main() == 0
    assert starts[0][0] == [str(root / "JobFinder.exe")]
    assert starts[0][1]["cwd"] == str(root)
    assert starts[0][1]["env"]["JOBFINDER_UPDATED"] == "1"
    assert (root / "app/JobFinder.exe").read_text() == "new-runtime"
    assert (root / "JobFinder.exe").read_text() == ("new-launcher" if release == "organized" else "old-launcher")
    assert (root / "state/searcher.db").read_text() == "USER ARCHIVE"
    assert not (root / "state/update.lock").exists()
    assert (root / "data/update.lock").read_text() == "LEGACY RESIDUAL MUST STAY"
    assert (root / "state/logs/updater.log").is_file()


def test_update_router_stages_inner_runtime_and_passes_outer_install_root(tmp_path, monkeypatch):
    from app.routers import system

    root = tmp_path / "install"
    organized(root)
    container = SimpleNamespace(workspace_dir=root, settings=SimpleNamespace(data_dir=root / "state"))
    app = FastAPI()
    app.include_router(system.build_router(container))
    monkeypatch.setattr(system.sys, "frozen", True, raising=False)
    monkeypatch.setattr(system.sys, "executable", str(root / "app/JobFinder.exe"))
    monkeypatch.setattr(system, "get_version_info", lambda **kwargs: {
        "current": "2.4.0", "latest": "2.5.0", "update_available": True,
    })
    stage = tmp_path / "staged"
    stage.mkdir()
    monkeypatch.setattr(system.tempfile, "mkdtemp", lambda **kwargs: str(stage))
    starts = []
    monkeypatch.setattr(system.subprocess, "Popen", lambda args, **kwargs: starts.append((args, kwargs)))
    monkeypatch.setattr(system.threading, "Timer", lambda *args, **kwargs: SimpleNamespace(start=lambda: None))
    with TestClient(app, base_url="http://127.0.0.1:8123") as client:
        response = client.post("/api/update/start")
        assert response.status_code == 202
        assert (stage / "Updater.exe").read_text() == "old-updater"
        assert (stage / "_internal/python311.dll").read_text() == "runtime-dll"
        command = starts[0][0]
        assert command[command.index("--install-dir") + 1] == str(root)
        owner = command[command.index("--lock-owner") + 1]
        assert json.loads((root / "state/update.lock").read_text())["owner"] == owner
        assert (root / "state/update.lock").is_file()
        assert not (root / "data/update.lock").exists()
        write(root / "state/logs/updater.log", "EVENT " + json.dumps({"event": "replace_done"}) + "\n")
        assert client.get("/api/update/progress").json()["step"] == "restart"
        assert client.delete("/api/update/lock").json()["cleared"]


def test_concurrent_lock_claim_has_only_one_winner(tmp_path):
    lock = tmp_path / "state/update.lock"
    barrier = threading.Barrier(2)

    def claim():
        barrier.wait(timeout=5)
        try:
            return claim_update_lock(lock, "2.5.0")
        except UpdateInProgressError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: claim(), range(2)))
    owners = [owner for owner in results if owner is not None]
    assert len(owners) == 1
    assert json.loads(lock.read_text())["owner"] == owners[0]


def test_stale_lock_takeover_cannot_be_cleared_by_its_previous_owner(tmp_path):
    lock = tmp_path / "state/update.lock"
    original = claim_update_lock(lock, "2.5.0")
    os.utime(lock, (1, 1))
    successor = claim_update_lock(lock, "2.5.1")
    assert successor != original
    assert not release_update_lock(lock, original)
    assert json.loads(lock.read_text())["owner"] == successor
    assert not release_update_lock(lock, legacy_parent_pid=os.getpid())
    assert release_update_lock(lock, successor)
    assert not lock.exists()


@pytest.mark.parametrize("replace_owner", [False, True])
def test_updater_spawn_failure_releases_only_its_own_lock(tmp_path, monkeypatch, replace_owner):
    from app.routers import system

    root = tmp_path / "install"
    organized(root)
    state = root / "state"
    container = SimpleNamespace(workspace_dir=root, settings=SimpleNamespace(data_dir=state))
    endpoint = next(route.endpoint for route in system.build_router(container).routes if route.path == "/api/update/start")
    monkeypatch.setattr(system.sys, "frozen", True, raising=False)
    monkeypatch.setattr(system.sys, "executable", str(root / "app/JobFinder.exe"))
    monkeypatch.setattr(system, "get_version_info", lambda **kwargs: {
        "current": "2.4.0", "latest": "2.5.0", "update_available": True,
    })
    stage = tmp_path / "staging"
    stage.mkdir()
    monkeypatch.setattr(system.tempfile, "mkdtemp", lambda **kwargs: str(stage))
    successor = []

    def fail_spawn(*args, **kwargs):
        if replace_owner:
            owner = json.loads((state / "update.lock").read_text())["owner"]
            assert release_update_lock(state / "update.lock", owner)
            successor.append(claim_update_lock(state / "update.lock", "successor"))
        raise OSError("simulated staged executable failure")

    monkeypatch.setattr(system.subprocess, "Popen", fail_spawn)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        endpoint()
    assert error.value.status_code == 500
    if replace_owner:
        assert json.loads((state / "update.lock").read_text())["owner"] == successor[0]
    else:
        assert not (state / "update.lock").exists()
