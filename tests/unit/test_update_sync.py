"""Unit tests for ``app.update_sync.sync_install_dir``.

These verify the contract the standalone updater relies on:

- ``data/`` and ``.env`` files in the install dir survive the sync.
- Application code under ``app/`` and ``web/`` is replaced by the
  bundle's version.
- Brand new files in the bundle are written to the install dir.
- Source bundle is never asked to ship its own ``data/`` (defensive
  skip on the source side too).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.update_sync import sync_install_dir


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _organized(root: Path, version: str = "new") -> None:
    _write(root / "app" / ".jobfinder-layout.json", '{"version":1}')
    _write(root / "JobFinder.exe", f"{version}-launcher")
    _write(root / "app" / "JobFinder.exe", f"{version}-runtime")
    _write(root / "app" / "Updater.exe", f"{version}-updater")
    _write(root / "app" / "JobFinderMCP.exe", f"{version}-mcp")
    _write(root / "app" / "_internal" / "app" / "data" / "provider_limits.json", version)
    _write(root / "LEGGIMI.txt", f"{version}-instructions")


def test_sync_preserves_data_dir(tmp_path: Path) -> None:
    install = tmp_path / "install"
    bundle = tmp_path / "bundle"
    _write(install / "data" / "searcher.db", "USER-DB-CONTENT")
    _write(install / "data" / "local_secrets.json", '{"openai_api_key": "sk-real"}')
    _write(bundle / "JobFinder.exe", "new-exe-bytes")

    sync_install_dir(source=bundle, target=install)

    assert (install / "data" / "searcher.db").read_text(encoding="utf-8") == "USER-DB-CONTENT"
    assert (install / "data" / "local_secrets.json").read_text(encoding="utf-8") == (
        '{"openai_api_key": "sk-real"}'
    )


def test_sync_replaces_app_files(tmp_path: Path) -> None:
    install = tmp_path / "install"
    bundle = tmp_path / "bundle"
    _write(install / "app" / "version.py", '__version__ = "0.3.0"')
    _write(bundle / "app" / "version.py", '__version__ = "0.4.0"')

    sync_install_dir(source=bundle, target=install)

    assert (install / "app" / "version.py").read_text(encoding="utf-8") == ('__version__ = "0.4.0"')


def test_sync_writes_brand_new_files(tmp_path: Path) -> None:
    install = tmp_path / "install"
    bundle = tmp_path / "bundle"
    _write(bundle / "web" / "modules" / "newmod.js", "console.log('hi')")

    written = sync_install_dir(source=bundle, target=install)

    assert written == 1
    assert (install / "web" / "modules" / "newmod.js").exists()


def test_sync_never_overwrites_dotenv(tmp_path: Path) -> None:
    install = tmp_path / "install"
    bundle = tmp_path / "bundle"
    _write(install / ".env", "GROQ_API_KEY=user-real-key")
    # Defensive: even if the bundle accidentally shipped a .env, ignore it.
    _write(bundle / ".env", "GROQ_API_KEY=bundle-default")

    sync_install_dir(source=bundle, target=install)

    assert (install / ".env").read_text(encoding="utf-8") == "GROQ_API_KEY=user-real-key"


def test_sync_skips_data_subtree_in_source(tmp_path: Path) -> None:
    """If the bundle accidentally contains data/, it must not clobber install."""
    install = tmp_path / "install"
    bundle = tmp_path / "bundle"
    _write(install / "data" / "searcher.db", "USER")
    _write(bundle / "data" / "demo.db", "BUNDLE-DEFAULT")
    _write(bundle / "JobFinder.exe", "exe")

    sync_install_dir(source=bundle, target=install)

    # User's DB intact; bundle's data/ never copied.
    assert (install / "data" / "searcher.db").read_text(encoding="utf-8") == "USER"
    assert not (install / "data" / "demo.db").exists()


def test_sync_raises_when_source_missing(tmp_path: Path) -> None:
    install = tmp_path / "install"
    bundle = tmp_path / "does-not-exist"
    with pytest.raises(FileNotFoundError):
        sync_install_dir(source=bundle, target=install)


def test_sync_retries_on_permission_error(tmp_path: Path, monkeypatch) -> None:
    """Transient PermissionError on copy should be retried, not fatal.

    Windows briefly holds file locks via antivirus / process exit, so the
    updater needs to wait and retry rather than abort the whole sync.
    """
    bundle = tmp_path / "bundle"
    install = tmp_path / "install"
    (bundle).mkdir()
    (bundle / "JobFinder.exe").write_text("v2")

    import app.update_sync as update_sync

    real_copy = update_sync.shutil.copy2
    call_count = {"n": 0}

    def flaky_copy2(src, dst, *args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise PermissionError(13, "Permission denied (simulated)")
        return real_copy(src, dst, *args, **kwargs)

    monkeypatch.setattr(update_sync.shutil, "copy2", flaky_copy2)
    monkeypatch.setattr(update_sync, "_COPY_RETRY_DELAYS", (0.0, 0.0, 0.0))

    written = sync_install_dir(source=bundle, target=install)
    assert written == 1
    assert call_count["n"] == 3
    assert (install / "JobFinder.exe").read_text() == "v2"


def test_sync_skips_current_executable(tmp_path: Path, monkeypatch) -> None:
    """Never try to overwrite the binary running the updater.

    Windows holds an exclusive lock on a running EXE. The launcher copies
    Updater.exe to %TEMP% to dodge this, but if that ever fails this guard
    keeps the sync from aborting partway through with PermissionError.
    """
    install = tmp_path / "install"
    bundle = tmp_path / "bundle"
    _write(bundle / "Updater.exe", "new-updater-bytes")
    _write(bundle / "JobFinder.exe", "new-jobfinder-bytes")
    _write(install / "Updater.exe", "old-updater-bytes")

    import app.update_sync as update_sync

    monkeypatch.setattr(update_sync.sys, "executable", str(install / "Updater.exe"))

    written = sync_install_dir(source=bundle, target=install)

    assert written == 1
    assert (install / "JobFinder.exe").read_text() == "new-jobfinder-bytes"
    assert (install / "Updater.exe").read_text() == "old-updater-bytes"


def test_sync_eventually_raises_after_all_retries(tmp_path: Path, monkeypatch) -> None:
    """If file stays locked past all retries, we propagate the error."""
    bundle = tmp_path / "bundle"
    install = tmp_path / "install"
    (bundle).mkdir()
    (bundle / "JobFinder.exe").write_text("v2")

    import app.update_sync as update_sync

    def always_locked(*_args, **_kwargs):
        raise PermissionError(13, "Permission denied (always)")

    monkeypatch.setattr(update_sync.shutil, "copy2", always_locked)
    monkeypatch.setattr(update_sync, "_COPY_RETRY_DELAYS", (0.0, 0.0, 0.0))

    with pytest.raises(PermissionError):
        sync_install_dir(source=bundle, target=install)


def test_organized_update_replaces_program_files_but_preserves_both_state_trees(tmp_path):
    bundle, install = tmp_path / "bundle", tmp_path / "install"
    _organized(bundle)
    _organized(install, "old")
    for prefix in (Path("state"), Path("data"), Path("app/data"), Path("app/state")):
        _write(install / prefix / "searcher.db", "USER DB")
        _write(bundle / prefix / "searcher.db", "BUNDLED DB MUST NOT COPY")
    for prefix in (Path(""), Path("app")):
        _write(install / prefix / ".env", "USER ENV")
        _write(bundle / prefix / ".env", "BUNDLED ENV MUST NOT COPY")
        _write(install / prefix / "vendor" / "codex.exe", "USER RUNTIME")
        _write(bundle / prefix / "vendor" / "codex.exe", "BUNDLED RUNTIME MUST NOT COPY")
    sync_install_dir(source=bundle, target=install)
    assert (install / "JobFinder.exe").read_text() == "new-launcher"
    assert (install / "app/JobFinder.exe").read_text() == "new-runtime"
    assert (install / "app/Updater.exe").read_text() == "new-updater"
    assert (install / "app/_internal/app/data/provider_limits.json").read_text() == "new"
    for prefix in (Path("state"), Path("data"), Path("app/data"), Path("app/state")):
        assert (install / prefix / "searcher.db").read_text() == "USER DB"
    for prefix in (Path(""), Path("app")):
        assert (install / prefix / ".env").read_text() == "USER ENV"
        assert (install / prefix / "vendor/codex.exe").read_text() == "USER RUNTIME"


def test_flat_release_updates_organized_runtime_without_touching_launcher_or_state(tmp_path):
    bundle, install = tmp_path / "bundle", tmp_path / "install"
    _organized(install, "old")
    _write(install / "state/searcher.db", "USER STATE")
    _write(bundle / "JobFinder.exe", "flat-runtime")
    _write(bundle / "Updater.exe", "flat-updater")
    _write(bundle / "_internal/python.dll", "flat-dll")
    _write(bundle / "data/searcher.db", "ACCIDENTAL DB")
    _write(bundle / "vendor/claude.exe", "BUNDLED RUNTIME")
    sync_install_dir(source=bundle, target=install)
    assert (install / "JobFinder.exe").read_text() == "old-launcher"
    assert (install / "app/JobFinder.exe").read_text() == "flat-runtime"
    assert (install / "app/Updater.exe").read_text() == "flat-updater"
    assert (install / "app/_internal/python.dll").read_text() == "flat-dll"
    assert (install / "state/searcher.db").read_text() == "USER STATE"
    assert not (install / "app/data/searcher.db").exists()
    assert (install / "app/vendor/claude.exe").read_text() == "BUNDLED RUNTIME"


def test_organized_release_is_flattened_for_legacy_install_without_migrating_data(tmp_path):
    bundle, install = tmp_path / "bundle", tmp_path / "install"
    _organized(bundle)
    _write(install / "JobFinder.exe", "old-flat-runtime")
    _write(install / "data/searcher.db", "LEGACY USER DB")
    _write(install / ".env", "LEGACY ENV")
    _write(install / "vendor/codex.exe", "LEGACY CLI")
    _write(bundle / "state/searcher.db", "ACCIDENTAL DB")
    _write(bundle / "app/data/searcher.db", "ACCIDENTAL DB")
    _write(bundle / "app/vendor/codex.exe", "ACCIDENTAL CLI")
    sync_install_dir(source=bundle, target=install)
    assert (install / "JobFinder.exe").read_text() == "new-runtime"
    assert (install / "Updater.exe").read_text() == "new-updater"
    assert (install / "JobFinderMCP.exe").read_text() == "new-mcp"
    assert (install / "_internal/app/data/provider_limits.json").read_text() == "new"
    assert (install / "data/searcher.db").read_text() == "LEGACY USER DB"
    assert (install / "vendor/codex.exe").read_text() == "LEGACY CLI"
    assert (install / ".env").read_text() == "LEGACY ENV"
    assert not (install / "state").exists()
    assert not (install / "app/JobFinder.exe").exists()


@pytest.mark.parametrize("exists", [False, True])
def test_fresh_destination_adopts_organized_layout(tmp_path, exists):
    bundle, install = tmp_path / "bundle", tmp_path / "install"
    _organized(bundle)
    _write(bundle / "app/vendor/tesseract/tesseract.exe", "BUNDLED OCR")
    if exists:
        install.mkdir()
    sync_install_dir(source=bundle, target=install)
    assert (install / "app/JobFinder.exe").read_text() == "new-runtime"
    assert (install / "JobFinder.exe").read_text() == "new-launcher"
    assert (install / "app/vendor/tesseract/tesseract.exe").read_text() == "BUNDLED OCR"


def test_vendor_updates_add_missing_tools_but_never_overwrite_user_files(tmp_path):
    bundle, install = tmp_path / "bundle", tmp_path / "install"
    _organized(bundle)
    _organized(install, "old")
    _write(install / "app/vendor/codex/codex.exe", "USER CLI")
    _write(bundle / "app/vendor/codex/codex.exe", "BUNDLED CLI")
    _write(bundle / "app/vendor/tesseract/tesseract.exe", "NEW OCR")
    sync_install_dir(source=bundle, target=install)
    assert (install / "app/vendor/codex/codex.exe").read_text() == "USER CLI"
    assert (install / "app/vendor/tesseract/tesseract.exe").read_text() == "NEW OCR"


def test_invalid_layout_is_rejected_before_any_existing_program_changes(tmp_path):
    bundle, install = tmp_path / "bundle", tmp_path / "install"
    _organized(bundle)
    _write(bundle / "app/.jobfinder-layout.json", '{"version":900}')
    _write(install / "JobFinder.exe", "USER OLD RUNTIME")
    with pytest.raises(ValueError, match="unrecognized"):
        sync_install_dir(source=bundle, target=install)
    assert (install / "JobFinder.exe").read_text() == "USER OLD RUNTIME"


def test_nested_source_and_target_are_rejected_before_copy(tmp_path):
    source = tmp_path / "bundle"
    _write(source / "JobFinder.exe", "runtime")
    with pytest.raises(ValueError, match="separate directory"):
        sync_install_dir(source=source, target=source / "nested-install")
    assert not (source / "nested-install").exists()
