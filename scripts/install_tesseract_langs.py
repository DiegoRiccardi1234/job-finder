"""Install missing Tesseract language packs (ita, eng) into system + bundle.

Run from project root with the venv Python:
    .venv/Scripts/python scripts/install_tesseract_langs.py

Used both locally (after installing Tesseract via winget) and in CI before
``build_exe.py`` so the bundled OCR can read Italian + English CVs.
"""

from __future__ import annotations

import os
import sys
import urllib.request
from pathlib import Path

LANGS = ["ita", "eng", "osd"]
TESSDATA_BASE = "https://github.com/tesseract-ocr/tessdata_fast/raw/main/"


def _candidate_tessdata_dirs() -> list[Path]:
    return [
        Path(r"C:\Program Files\Tesseract-OCR\tessdata"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tessdata"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR" / "tessdata",
    ]


def _download(lang: str) -> bytes:
    url = f"{TESSDATA_BASE}{lang}.traineddata"
    req = urllib.request.Request(url, headers={"User-Agent": "JobFinder-build/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return bytes(resp.read())


def main() -> int:
    targets: list[Path] = []
    for d in _candidate_tessdata_dirs():
        if d.exists():
            targets.append(d)
    if not targets:
        print("No Tesseract tessdata dir found. Install Tesseract first.", file=sys.stderr)
        return 1
    for lang in LANGS:
        # Skip if already present in every target.
        if all((t / f"{lang}.traineddata").exists() for t in targets):
            print(f"{lang}: already installed in {len(targets)} dir(s)")
            continue
        print(f"{lang}: downloading...")
        data = _download(lang)
        for t in targets:
            dest = t / f"{lang}.traineddata"
            try:
                dest.write_bytes(data)
                print(f"  -> {dest} ({len(data) / 1024 / 1024:.1f} MB)")
            except PermissionError as exc:
                print(f"  ! {dest} (permission denied — run as admin or skip): {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
