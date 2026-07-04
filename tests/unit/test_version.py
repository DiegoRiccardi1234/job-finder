"""Tests for ``app.version`` (parse/compare/update-check) and the bundle-update
direction guard in the system router.

The downgrade scenario these protect: a local build ahead of the newest GitHub
release (e.g. 1.5.0 committed but not yet tagged, latest release v1.4.2). The
banner logic must stay silent and ``POST /api/update/start`` must refuse to
"update" to the older bundle.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.version as version_mod
from app.version import _parse_version, get_version_info


@pytest.fixture(autouse=True)
def _reset_version_cache() -> Any:
    version_mod._cache["fetched_at"] = 0.0
    version_mod._cache["data"] = None
    yield
    version_mod._cache["fetched_at"] = 0.0
    version_mod._cache["data"] = None


# ---------------------------------------------------------------- _parse_version


def test_parse_version_strips_v_prefix() -> None:
    assert _parse_version("v1.4.2") == (1, 4, 2)
    assert _parse_version("1.5.0") == (1, 5, 0)


def test_parse_version_comparison_is_numeric_not_lexicographic() -> None:
    assert _parse_version("v1.10.0") > _parse_version("v1.9.9")
    assert _parse_version("v1.4.9") < _parse_version("v1.5.0")


def test_parse_version_garbage_returns_zero() -> None:
    assert _parse_version("") == (0,)
    assert _parse_version("nightly") == (0,)


# ------------------------------------------------------------- get_version_info


def _mock_release(monkeypatch: pytest.MonkeyPatch, tag: str | None) -> None:
    release = None if tag is None else {"tag_name": tag, "html_url": "u", "body": "notes"}
    monkeypatch.setattr(version_mod, "_fetch_latest_release", lambda: release)


def test_older_release_is_not_an_update(monkeypatch: pytest.MonkeyPatch) -> None:
    """Local ahead of GitHub (untagged dev build): no update, no downgrade offer."""
    _mock_release(monkeypatch, "v0.0.1")
    info = get_version_info(force_refresh=True)
    assert info["update_available"] is False
    assert info["latest"] == "v0.0.1"
    assert info["checked"] is True


def test_newer_release_is_an_update(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_release(monkeypatch, "v999.0.0")
    info = get_version_info(force_refresh=True)
    assert info["update_available"] is True


def test_same_release_is_not_an_update(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_release(monkeypatch, f"v{version_mod.__version__}")
    info = get_version_info(force_refresh=True)
    assert info["update_available"] is False


def test_fetch_failure_degrades_gracefully(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_release(monkeypatch, None)
    info = get_version_info(force_refresh=True)
    assert info["update_available"] is False
    assert info["latest"] is None
    assert info["checked"] is False


def test_cache_serves_second_call(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def fetch() -> dict[str, Any]:
        calls.append(1)
        return {"tag_name": "v0.0.1"}

    monkeypatch.setattr(version_mod, "_fetch_latest_release", fetch)
    get_version_info(force_refresh=True)
    get_version_info()
    assert len(calls) == 1


# ------------------------------------------- POST /api/update/start direction guard


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    (tmp_path / "web").mkdir(exist_ok=True)
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    for key in ("CEREBRAS_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY"):
        os.environ.pop(key, None)

    from app.main import create_app

    app = create_app(workspace_dir=tmp_path)
    with TestClient(app) as tc:
        yield tc


def test_bundle_update_refuses_downgrade(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Frozen build with local version AHEAD of the latest release: 409, never spawn."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    _mock_release(monkeypatch, "v0.0.1")

    res = client.post("/api/update/start")
    assert res.status_code == 409
    assert "No newer version" in res.json()["detail"]


def test_bundle_update_refused_in_dev_mode(client: Any) -> None:
    res = client.post("/api/update/start")
    assert res.status_code == 409
