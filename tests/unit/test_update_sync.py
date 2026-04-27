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

    assert (install / "app" / "version.py").read_text(encoding="utf-8") == (
        '__version__ = "0.4.0"'
    )


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
