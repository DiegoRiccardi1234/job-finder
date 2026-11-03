"""Release transports must remain readable by an updater predating app/state."""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import pytest

from app.install_layout import installation_root, state_dir
from app.update_sync import sync_install_dir
from scripts.package_windows import write_release_archives


def write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def bundles(tmp_path: Path) -> tuple[Path, Path]:
    legacy, portable = tmp_path / "raw", tmp_path / "portable"
    for path in ("JobFinder.exe", "Updater.exe", "JobFinderMCP.exe", "_internal/app/data/provider_limits.json", "vendor/tesseract/tesseract.exe"):
        write(legacy / path, b"runtime:" + path.encode())
    shutil.copytree(legacy, portable / "app")
    write(portable / "app/.jobfinder-layout.json", b'{"version":1}')
    write(portable / "JobFinder.exe", b"thin-launcher")
    write(portable / "LEGGIMI.txt", b"Help")
    for source in (legacy, portable):
        for name in ("state/searcher.db", "data/searcher.db", ".env", ".env.local"):
            write(source / name, b"PRIVATE_USER_DATA")
    return legacy, portable


def test_archives_separate_portable_layout_from_historical_update_transport(tmp_path):
    legacy, portable = bundles(tmp_path)
    compatibility, clean = write_release_archives(legacy, portable, tmp_path / "output")
    assert compatibility.name.endswith("windows.zip")
    assert not clean.name.endswith("windows.zip")
    with zipfile.ZipFile(compatibility) as archive:
        assert archive.read("JobFinder/JobFinder.exe").startswith(b"runtime:")
        assert "JobFinder/_internal/app/data/provider_limits.json" in archive.namelist()
        assert not any(name.startswith("JobFinder/app/") for name in archive.namelist())
    with zipfile.ZipFile(clean) as archive:
        assert archive.read("JobFinder/JobFinder.exe") == b"thin-launcher"
        assert archive.read("JobFinder/app/vendor/tesseract/tesseract.exe").startswith(b"runtime:")
        assert "JobFinder/state/" in archive.namelist()
    for path in (compatibility, clean):
        with zipfile.ZipFile(path) as archive:
            assert not any(b"PRIVATE_USER_DATA" in archive.read(name) for name in archive.namelist())


def test_old_naive_updater_keeps_reading_the_existing_database(tmp_path):
    legacy, portable = bundles(tmp_path)
    compatibility, _ = write_release_archives(legacy, portable, tmp_path / "output")
    target = tmp_path / "installed"
    write(target / "JobFinder.exe", b"old-executable")
    write(target / "data/searcher.db", b"EXISTING USER ARCHIVE")
    with zipfile.ZipFile(compatibility) as archive:
        archive.extractall(tmp_path / "extracted")
    # Model the pre-layout consumer: copy root program files while preserving
    # data. It does not know the new app/state mapping implementation.
    extracted = tmp_path / "extracted/JobFinder"
    for source in extracted.rglob("*"):
        relative = source.relative_to(extracted)
        if source.is_file() and relative.parts[0] not in {"data", ".env", ".env.local"}:
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    root = installation_root(target / "JobFinder.exe")
    assert state_dir(root) / "searcher.db" == target / "data/searcher.db"
    assert (state_dir(root) / "searcher.db").read_bytes() == b"EXISTING USER ARCHIVE"
    assert not (target / "state").exists()


def test_new_updater_reads_compatibility_asset_into_organized_installation(tmp_path):
    legacy, portable = bundles(tmp_path)
    compatibility, _ = write_release_archives(legacy, portable, tmp_path / "output")
    target = tmp_path / "organized-install"
    write(target / "JobFinder.exe", b"existing-launcher")
    write(target / "app/.jobfinder-layout.json", b'{"version":1}')
    write(target / "state/searcher.db", b"EXISTING USER ARCHIVE")
    with zipfile.ZipFile(compatibility) as archive:
        archive.extractall(tmp_path / "extracted")
    sync_install_dir(source=tmp_path / "extracted/JobFinder", target=target)
    assert (target / "JobFinder.exe").read_bytes() == b"existing-launcher"
    assert (target / "app/JobFinder.exe").read_bytes().startswith(b"runtime:")
    assert (target / "state/searcher.db").read_bytes() == b"EXISTING USER ARCHIVE"
    assert not (target / "data").exists()


def test_compatibility_asset_cannot_accidentally_use_portable_source(tmp_path):
    _, portable = bundles(tmp_path)
    with pytest.raises(ValueError, match="flat"):
        write_release_archives(portable, portable, tmp_path / "output")
