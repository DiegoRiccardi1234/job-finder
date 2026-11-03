"""Arrange a flat PyInstaller bundle as JobFinder.exe, LEGGIMI, app/ and state/."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_PRIVATE_TOPLEVEL = {"data", "state", ".env", ".env.local", "LEGGIMI.txt"}


def organize_bundle(source: Path, target: Path) -> Path:
    source, target = source.resolve(), target.resolve()
    if source == target or source in target.parents or target in source.parents:
        raise ValueError("Source and output must be separate bundle directories")
    if not (source / "JobFinder.exe").is_file() or not (source / "_internal").is_dir():
        raise ValueError("Expected a flat PyInstaller bundle")
    if target.exists() and any(target.iterdir()):
        raise ValueError("Output bundle must be empty; existing files are never removed")
    compiler = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
    if not compiler.is_file():
        raise RuntimeError("The Windows .NET Framework C# compiler is required")
    app_dir = target / "app"
    app_dir.mkdir(parents=True)
    (target / "state").mkdir()
    for entry in source.iterdir():
        if entry.name in _PRIVATE_TOPLEVEL:
            continue
        destination = app_dir / entry.name
        if entry.is_dir():
            shutil.copytree(entry, destination)
        else:
            shutil.copy2(entry, destination)
    (app_dir / ".jobfinder-layout.json").write_text(json.dumps({"version": 1}) + "\n", encoding="utf-8")
    subprocess.run([str(compiler), "/nologo", "/target:winexe", "/reference:System.Windows.Forms.dll",
                    f"/win32icon:{ROOT / 'web' / 'jobfinder.ico'}",
                    f"/out:{target / 'JobFinder.exe'}", str(ROOT / "scripts" / "windows_launcher.cs")],
                   check=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    shutil.copy2(ROOT / "templates" / "LEGGIMI.txt", target / "LEGGIMI.txt")
    if {p.name for p in target.iterdir()} != {"JobFinder.exe", "LEGGIMI.txt", "app", "state"}:
        raise RuntimeError("Unexpected public bundle layout")
    return target


def write_release_archives(legacy: Path, organized: Path, output: Path) -> tuple[Path, Path]:
    """Keep the old updater's *windows.zip transport flat; offer a clean portable ZIP.

    An old updater cannot migrate data/ when it copies an organized release.
    Naming the portable asset differently also keeps that updater from choosing it.
    Both transports omit user state even when packaging a tested candidate.
    """
    legacy, organized, output = (path.resolve() for path in (legacy, organized, output))
    if not (legacy / "_internal").is_dir() or (legacy / "app" / ".jobfinder-layout.json").exists():
        raise ValueError("The updater-compatible archive requires a flat runtime")
    marker = json.loads((organized / "app" / ".jobfinder-layout.json").read_text(encoding="utf-8"))
    if not isinstance(marker, dict) or type(marker.get("version")) is not int or marker["version"] != 1:
        raise ValueError("The portable archive requires an organized runtime")
    for source in (legacy, organized):
        if output == source or output.is_relative_to(source):
            raise ValueError("Archive output must be outside its source bundle")
        if not (source / "JobFinder.exe").is_file():
            raise ValueError("Missing bundle executable")
    output.mkdir(parents=True, exist_ok=True)
    archives = (output / "JobFinder-windows.zip", output / "JobFinder-portable.zip")
    for source, archive_path in zip((legacy, organized), archives, strict=True):
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            if source == organized:
                archive.writestr("JobFinder/state/", "")
            for entry in source.rglob("*"):
                relative = entry.relative_to(source)
                if relative.parts[0] in {"data", "state", ".env", ".env.local"}:
                    continue
                if entry.is_file():
                    archive.write(entry, Path("JobFinder") / relative)
    return archives


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    args = parser.parse_args()
    print(organize_bundle(args.source, args.target))


if __name__ == "__main__":
    main()
