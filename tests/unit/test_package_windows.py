from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import package_windows as package


def test_package_groups_helpers_and_ocr_but_never_ships_user_state(tmp_path, monkeypatch):
    source = tmp_path / "raw"
    (source / "_internal" / "app" / "data").mkdir(parents=True)
    (source / "_internal" / "app" / "data" / "provider_limits.json").write_text("{}")
    (source / "vendor").mkdir()
    (source / "vendor" / "ocr.exe").write_bytes(b"OCR")
    (source / "data").mkdir()
    (source / "data" / "searcher.db").write_bytes(b"PRIVATE")
    (source / ".env").write_text("PRIVATE=secret")
    for name in ("JobFinder.exe", "JobFinderMCP.exe", "Updater.exe"):
        (source / name).write_bytes(name.encode())
    compiler = tmp_path / "Windows" / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
    compiler.parent.mkdir(parents=True)
    compiler.touch()
    monkeypatch.setenv("WINDIR", str(tmp_path / "Windows"))
    calls = []
    def compile_fake(command, **kwargs):
        calls.append(command)
        output = next(arg[5:] for arg in command if arg.startswith("/out:"))
        Path(output).write_bytes(b"LAUNCHER")
    monkeypatch.setattr(package.subprocess, "run", compile_fake)
    target = package.organize_bundle(source, tmp_path / "portable")
    assert {p.name for p in target.iterdir()} == {"JobFinder.exe", "LEGGIMI.txt", "app", "state"}
    assert (target / "JobFinder.exe").read_bytes() == b"LAUNCHER"
    assert (target / "app" / "JobFinder.exe").read_bytes() == b"JobFinder.exe"
    assert (target / "app" / "vendor" / "ocr.exe").read_bytes() == b"OCR"
    assert (target / "app" / "_internal" / "app" / "data" / "provider_limits.json").exists()
    assert json.loads((target / "app" / ".jobfinder-layout.json").read_text()) == {"version": 1}
    assert not list((target / "state").iterdir())
    assert not (target / "app" / "data").exists()
    assert not (target / "app" / ".env").exists()
    assert len(calls) == 1
    assert f"/win32icon:{package.ROOT / 'web' / 'jobfinder.ico'}" in calls[0]
    assert (package.ROOT / 'web' / 'jobfinder.ico').is_file()


def test_package_never_removes_existing_target_contents(tmp_path):
    source, target = tmp_path / "raw", tmp_path / "existing"
    (source / "_internal").mkdir(parents=True)
    (source / "JobFinder.exe").touch()
    target.mkdir()
    sentinel = target / "keep.txt"
    sentinel.write_text("Keep me")
    with pytest.raises(ValueError, match="empty"):
        package.organize_bundle(source, target)
    assert sentinel.read_text() == "Keep me"
