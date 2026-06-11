"""Build the standalone Windows bundle.

Usage:
    python scripts/build_exe.py

Produces:
    dist/JobFinder/                    # PyInstaller --onedir output
    dist/JobFinder/vendor/tesseract/   # bundled Tesseract OCR (~50 MB)
    dist/JobFinder-windows.zip         # zipped distributable
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "JobFinder.spec"
DIST = ROOT / "dist"
BUILD = ROOT / "build"


def _locate_tesseract() -> Path | None:
    """Find an installed Tesseract directory to copy into the bundle."""
    candidates = [
        Path(r"C:\Program Files\Tesseract-OCR"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR",
        Path(r"C:\ProgramData\chocolatey\lib\tesseract\tools"),
    ]
    for path in candidates:
        if (path / "tesseract.exe").is_file():
            return path
    found = shutil.which("tesseract") or shutil.which("tesseract.exe")
    if found:
        return Path(found).parent
    return None


_REQUIRED_LANGS = ("eng", "ita", "spa", "fra", "deu", "osd")
_TESSDATA_FAST_BASE = "https://github.com/tesseract-ocr/tessdata_fast/raw/main/"


def _ensure_languages(tessdata_dir: Path) -> None:
    """Download any missing required language packs into ``tessdata_dir``.

    The default Tesseract installer (winget / choco silent) only ships ``eng``
    + ``osd``; Italian CVs need ``ita.traineddata`` (~2.6 MB). We download into
    the bundle so end users don't need to install language packs themselves.
    """
    import urllib.request

    tessdata_dir.mkdir(parents=True, exist_ok=True)
    for lang in _REQUIRED_LANGS:
        dest = tessdata_dir / f"{lang}.traineddata"
        if dest.exists():
            continue
        url = f"{_TESSDATA_FAST_BASE}{lang}.traineddata"
        print(f"downloading {lang}.traineddata...")
        req = urllib.request.Request(url, headers={"User-Agent": "JobFinder-build/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            dest.write_bytes(resp.read())
        size_mb = dest.stat().st_size / 1024 / 1024
        print(f"  -> {dest} ({size_mb:.1f} MB)")


def _bundle_tesseract(bundle_dir: Path) -> None:
    """Copy tesseract.exe + tessdata + DLL siblings into ``bundle/vendor/tesseract``.

    The end-user zip ships with OCR ready to run; ``cv_ingest._resolve_tesseract_cmd``
    looks here first before falling back to system PATH.
    """
    src = _locate_tesseract()
    target = bundle_dir / "vendor" / "tesseract"
    if not src:
        print(
            "WARNING: Tesseract not found on this machine. Skipping OCR bundle.\n"
            "Image CV uploads will fail unless the user installs Tesseract themselves.",
            file=sys.stderr,
        )
        return
    print(f"bundling Tesseract from {src} -> {target}")
    target.mkdir(parents=True, exist_ok=True)
    # Copy the entire install dir (binary, DLLs, tessdata, configs).
    for item in src.iterdir():
        dest = target / item.name
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dest)
    _ensure_languages(target / "tessdata")


def main() -> int:
    if not SPEC.exists():
        print(f"missing spec: {SPEC}", file=sys.stderr)
        return 1
    for path in (DIST, BUILD):
        if path.exists():
            shutil.rmtree(path)
    print("running PyInstaller...")
    subprocess.check_call(
        ["pyinstaller", str(SPEC), "--noconfirm"],
        cwd=ROOT,
    )
    bundle = DIST / "JobFinder"
    if not bundle.exists():
        print("PyInstaller did not produce dist/JobFinder/", file=sys.stderr)
        return 2
    _bundle_tesseract(bundle)
    # Quick-start guide next to JobFinder.exe for non-developer users.
    readme_src = ROOT / "scripts" / "bundle_LEGGIMI.txt"
    if readme_src.exists():
        shutil.copy2(readme_src, bundle / "LEGGIMI.txt")
        print(f"bundled quick-start guide -> {bundle / 'LEGGIMI.txt'}")
    zip_path = DIST / "JobFinder-windows.zip"
    print(f"creating {zip_path.name}...")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in bundle.rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(bundle.parent))
    size_mb = zip_path.stat().st_size // (1024 * 1024)
    print(f"done: {zip_path} ({size_mb} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
