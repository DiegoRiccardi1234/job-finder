"""Tests for GET /api/providers/{name}/models and ProviderManager.get_models cache."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    (tmp_path / "web").mkdir(exist_ok=True)
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    for key in (
        "CEREBRAS_API_KEY",
        "GROQ_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "OPENROUTER_API_KEY",
    ):
        os.environ.pop(key, None)

    from app.main import create_app

    app = create_app(workspace_dir=tmp_path)
    with TestClient(app) as tc:
        yield tc


def test_unknown_provider_returns_404(client: TestClient) -> None:
    res = client.get("/api/providers/nope/models")
    assert res.status_code == 404
    assert res.json()["detail"] == "unknown_provider"


def test_missing_key_returns_400(client: TestClient) -> None:
    res = client.get("/api/providers/cerebras/models")
    assert res.status_code == 400
    assert res.json()["detail"] == "key_missing"


def test_invalid_key_returns_key_invalid(client: TestClient, monkeypatch) -> None:
    """A key that 401'd must report ``key_invalid`` (not the misleading
    ``key_missing``) so the UI can say 'check your key' vs 'add a key'."""
    from app.providers.cerebras_provider import CerebrasProvider

    monkeypatch.setattr(CerebrasProvider, "key_invalid", True, raising=False)
    res = client.get("/api/providers/cerebras/models")
    assert res.status_code == 400
    assert res.json()["detail"] == "key_invalid"


def test_returns_models_and_recommended(client: TestClient, monkeypatch) -> None:
    from app.providers.cerebras_provider import CerebrasProvider

    monkeypatch.setattr(CerebrasProvider, "is_available", lambda self: True, raising=False)
    monkeypatch.setattr(
        CerebrasProvider,
        "list_models",
        lambda self: ["llama3.1-8b", "qwen-3-235b-a22b-instruct-2507"],
        raising=False,
    )

    res = client.get("/api/providers/cerebras/models")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["provider"] == "cerebras"
    assert body["models"] == ["llama3.1-8b", "qwen-3-235b-a22b-instruct-2507"]
    assert body["recommended"] in body["models"]
    assert body["cached"] is False


def test_cache_hit_within_ttl(client: TestClient, monkeypatch) -> None:
    from app.providers.cerebras_provider import CerebrasProvider

    monkeypatch.setattr(CerebrasProvider, "is_available", lambda self: True, raising=False)
    calls = {"n": 0}

    def fake_list(self: Any) -> list[str]:
        calls["n"] += 1
        return ["m1", "m2"]

    monkeypatch.setattr(CerebrasProvider, "list_models", fake_list, raising=False)

    first = client.get("/api/providers/cerebras/models").json()
    second = client.get("/api/providers/cerebras/models").json()
    assert calls["n"] == 1, "second call should hit the cache"
    assert second["cached"] is True
    assert first["models"] == second["models"]


def test_force_refresh_bypasses_cache(client: TestClient, monkeypatch) -> None:
    from app.providers.cerebras_provider import CerebrasProvider

    monkeypatch.setattr(CerebrasProvider, "is_available", lambda self: True, raising=False)
    calls = {"n": 0}

    def fake_list(self: Any) -> list[str]:
        calls["n"] += 1
        return [f"m-{calls['n']}"]

    monkeypatch.setattr(CerebrasProvider, "list_models", fake_list, raising=False)

    client.get("/api/providers/cerebras/models")
    forced = client.get("/api/providers/cerebras/models?force_refresh=1").json()
    assert calls["n"] == 2
    assert forced["cached"] is False
    assert forced["models"] == ["m-2"]


def test_empty_models_recommended_is_none(client: TestClient, monkeypatch) -> None:
    from app.providers.cerebras_provider import CerebrasProvider

    monkeypatch.setattr(CerebrasProvider, "is_available", lambda self: True, raising=False)
    monkeypatch.setattr(CerebrasProvider, "list_models", lambda self: [], raising=False)

    res = client.get("/api/providers/cerebras/models")
    assert res.status_code == 200
    body = res.json()
    assert body["models"] == []
    assert body["recommended"] is None


def test_provider_manager_get_models_unknown_provider(tmp_path: Path) -> None:
    from app.config import load_settings
    from app.providers.factory import ProviderManager

    settings = load_settings(workspace_dir=tmp_path)
    pm = ProviderManager(settings)
    result = pm.get_models("nonexistent-provider")
    assert result == {"models": [], "recommended": None, "cached": False, "fetched_at": 0.0}


def test_provider_manager_get_models_handles_list_exception(tmp_path: Path, monkeypatch) -> None:
    from app.config import load_settings
    from app.providers.cerebras_provider import CerebrasProvider
    from app.providers.factory import ProviderManager

    monkeypatch.setattr(CerebrasProvider, "is_available", lambda self: True, raising=False)

    def raising_list(self: Any) -> list[str]:
        raise RuntimeError("boom")

    monkeypatch.setattr(CerebrasProvider, "list_models", raising_list, raising=False)

    settings = load_settings(workspace_dir=tmp_path)
    pm = ProviderManager(settings)
    result = pm.get_models("cerebras")
    assert result["models"] == []
    assert result["recommended"] is None
