"""Marked layouts preserve legacy paths and keep mutable state out of app/."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent_tools import AgentService
from app.config import load_settings
from app.install_layout import installation_root, is_organized, runtime_dir, state_dir, vendor_dir
from scripts import launch_exe


def _organized(root: Path) -> None:
    (root / "app").mkdir()
    (root / "app" / ".jobfinder-layout.json").write_text('{"version":1}', encoding="utf-8")


def test_marked_runtime_resolves_outer_root_before_state_exists(tmp_path: Path) -> None:
    _organized(tmp_path)
    assert installation_root(tmp_path / "app" / "JobFinder.exe") == tmp_path
    assert installation_root(tmp_path / "app" / "Updater.exe") == tmp_path
    assert installation_root(tmp_path / "app") == tmp_path
    assert installation_root(tmp_path) == tmp_path
    assert runtime_dir(tmp_path) == tmp_path / "app"
    assert state_dir(tmp_path) == tmp_path / "state"
    assert vendor_dir(tmp_path) == tmp_path / "app" / "vendor"
    assert not (tmp_path / "state").exists()  # Helpers never create data.


@pytest.mark.parametrize(
    "marker", ["not json", "[]", '{"version":2}', '{"version":true}', '{"version":"1"}']
)
def test_invalid_or_unknown_marker_does_not_enable_layout(tmp_path: Path, marker: str) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / ".jobfinder-layout.json").write_text(marker, encoding="utf-8")
    assert not is_organized(tmp_path)
    with pytest.raises(ValueError, match="Unrecognized"):
        installation_root(tmp_path / "app" / "JobFinder.exe")
    with pytest.raises(ValueError, match="Unrecognized"):
        state_dir(tmp_path)


def test_source_app_folder_and_state_name_alone_are_not_installation_marker(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "state").mkdir()
    (tmp_path / "JobFinder.exe").touch()
    assert not is_organized(tmp_path)
    assert runtime_dir(tmp_path) == tmp_path
    assert state_dir(tmp_path) == tmp_path / "data"
    assert installation_root(tmp_path / "JobFinder.exe") == tmp_path


def test_settings_use_state_directly_and_do_not_create_legacy_data(
    tmp_path: Path, monkeypatch
) -> None:
    _organized(tmp_path)
    writable = tmp_path / "state"
    writable.mkdir()
    (writable / "settings.json").write_text('{"max_annunci":7}', encoding="utf-8")
    (writable / "local_secrets.json").write_text(
        '{"google_api_key":"synthetic-local"}', encoding="utf-8"
    )
    monkeypatch.delenv("SEARCHER_DB_PATH", raising=False)
    settings = load_settings(tmp_path)
    assert settings.workspace_dir == tmp_path
    assert settings.data_dir == writable
    assert settings.db_path == writable / "searcher.db"
    assert settings.google_api_key == "synthetic-local"
    assert settings.max_annunci == 7
    assert not (writable / "data").exists()
    assert not (tmp_path / "data").exists()


def test_organized_dotenv_precedence_is_environment_state_then_legacy_root(
    tmp_path: Path, monkeypatch
) -> None:
    _organized(tmp_path)
    writable = tmp_path / "state"
    writable.mkdir()
    (tmp_path / ".env").write_text(
        "GROQ_API_KEY=synthetic-root\nOVH_API_KEY=synthetic-root-only\nMISTRAL_API_KEY=synthetic-root-mistral\n",
        encoding="utf-8",
    )
    (writable / ".env").write_text(
        "GROQ_API_KEY=synthetic-state\nMISTRAL_API_KEY=synthetic-state-mistral\n", encoding="utf-8"
    )
    for key in ("GROQ_API_KEY", "OVH_API_KEY"):
        # Register restoration even when the original key was absent: the
        # legacy dotenv loader intentionally writes directly to os.environ.
        monkeypatch.setenv(key, "temporary-fixture-sentinel")
        monkeypatch.delenv(key)
    monkeypatch.setenv("MISTRAL_API_KEY", "synthetic-environment")
    settings = load_settings(tmp_path)
    assert settings.groq_api_key == "synthetic-state"
    assert settings.ovh_api_key == "synthetic-root-only"
    assert settings.mistral_api_key == "synthetic-environment"


def test_organized_groq_key_file_and_db_override_remain_supported(
    tmp_path: Path, monkeypatch
) -> None:
    _organized(tmp_path)
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "groq key.txt").write_text("synthetic-file-key", encoding="utf-8")
    override = tmp_path / "isolated" / "override.db"
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("SEARCHER_DB_PATH", str(override))
    settings = load_settings(tmp_path)
    assert settings.groq_key_file == tmp_path / "state" / "groq key.txt"
    assert settings.groq_api_key == "synthetic-file-key"
    assert settings.db_path == override


def test_frozen_launcher_keeps_outer_workspace_and_uses_state_for_update_and_tray(
    tmp_path: Path, monkeypatch
) -> None:
    _organized(tmp_path)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "app" / "JobFinder.exe"))
    monkeypatch.delenv("JOBFINDER_UPDATED", raising=False)
    assert launch_exe._resolve_workspace() == tmp_path
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "update.lock").write_text("synthetic", encoding="utf-8")
    assert launch_exe._update_in_progress(tmp_path)
    conn = sqlite3.connect(tmp_path / "state" / "searcher.db")
    conn.execute("CREATE TABLE preferences (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO preferences VALUES ('ui_language','it')")
    conn.commit()
    conn.close()
    assert launch_exe._ui_language(tmp_path) == "it"


def test_legacy_frozen_launcher_keeps_executable_parent_and_data(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "JobFinder.exe"))
    assert launch_exe._resolve_workspace() == tmp_path
    assert state_dir(tmp_path) == tmp_path / "data"
    assert vendor_dir(tmp_path) == tmp_path / "vendor"


def test_frozen_mcp_setup_points_to_runtime_sibling_and_state_token(
    tmp_path: Path, monkeypatch
) -> None:
    _organized(tmp_path)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "app" / "JobFinder.exe"))
    fake = SimpleNamespace(
        token=lambda: "not-returned", token_file=tmp_path / "state" / "agent" / "mcp-token"
    )
    setup = AgentService.setup(fake, "http://127.0.0.1:8000")
    config = setup["configs"]["claude_desktop"]["mcpServers"]["jobfinder"]
    assert config["command"] == str(tmp_path / "app" / "JobFinderMCP.exe")
    assert str(tmp_path / "state" / "agent" / "mcp-token") in config["args"]
    assert "not-returned" not in json.dumps(setup)
