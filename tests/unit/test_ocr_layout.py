from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app import cv_ingest


@pytest.mark.parametrize("organized", [False, True])
def test_frozen_tesseract_uses_vendor_next_to_real_runtime(
    tmp_path: Path, monkeypatch, organized: bool
) -> None:
    program = tmp_path / "app" if organized else tmp_path
    program.mkdir(exist_ok=True)
    if organized:
        (program / ".jobfinder-layout.json").write_text('{"version":1}', encoding="utf-8")
    binary = program / "vendor" / "tesseract" / "tesseract.exe"
    binary.parent.mkdir(parents=True)
    binary.touch()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(program / "JobFinder.exe"))
    monkeypatch.delenv("JOBFINDER_TESSERACT_CMD", raising=False)
    assert cv_ingest._resolve_tesseract_cmd() == str(binary)


def test_tesseract_explicit_override_still_wins_over_bundled_vendor(
    tmp_path: Path, monkeypatch
) -> None:
    override = tmp_path / "custom-tesseract.exe"
    override.touch()
    monkeypatch.setenv("JOBFINDER_TESSERACT_CMD", str(override))
    assert cv_ingest._resolve_tesseract_cmd() == str(override)


def test_source_tesseract_lookup_uses_source_root_not_python_executable(
    tmp_path: Path, monkeypatch
) -> None:
    binary = tmp_path / "vendor" / "tesseract" / "tesseract.exe"
    binary.parent.mkdir(parents=True)
    binary.touch()
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(cv_ingest, "__file__", str(tmp_path / "app" / "cv_ingest.py"))
    monkeypatch.delenv("JOBFINDER_TESSERACT_CMD", raising=False)
    assert cv_ingest._resolve_tesseract_cmd() == str(binary)


@pytest.mark.parametrize("suffix", ["Library/bin", "bin", ""])
def test_organized_poppler_supports_portable_directory_layouts(
    tmp_path: Path, monkeypatch, suffix: str
) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / ".jobfinder-layout.json").write_text('{"version":1}', encoding="utf-8")
    portable = tmp_path / "app" / "vendor" / "poppler" / suffix
    portable.mkdir(parents=True)
    (portable / "pdftoppm.exe").touch()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "app" / "JobFinder.exe"))
    assert cv_ingest._resolve_poppler_path() == str(portable)
