"""Shared fixtures for unit tests.

Includes a lightweight fake ProviderManager that does not require any LLM SDK,
plus a scratch workspace factory for services that need settings/db.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.config import AppSettings, load_settings
from app.db import Database


class FakeProviderManager:
    """Drop-in replacement for app.providers.factory.ProviderManager in tests."""

    def __init__(
        self,
        chat_response: str = '{"answer": "ok", "action": null}',
        json_response: dict[str, Any] | None = None,
        raise_on_chat: bool = False,
        raise_on_json: bool = False,
    ) -> None:
        self.chat_response = chat_response
        self.json_response = json_response or {"summary": "stub"}
        self.raise_on_chat = raise_on_chat
        self.raise_on_json = raise_on_json
        self.active_provider_name = "fake"
        self.active_model = "fake-model"
        self.chat_calls: list[dict[str, Any]] = []
        self.json_calls: list[dict[str, Any]] = []

    def chat(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 700,
        provider_name: str | None = None,
        model_name: str | None = None,
    ) -> str:
        self.chat_calls.append(
            {"messages": messages, "max_tokens": max_tokens, "provider": provider_name}
        )
        if self.raise_on_chat:
            raise RuntimeError("fake provider chat failure")
        return self.chat_response

    def complete_json(
        self,
        prompt: str,
        max_tokens: int = 700,
        provider_name: str | None = None,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        self.json_calls.append({"prompt": prompt, "max_tokens": max_tokens})
        if self.raise_on_json:
            raise RuntimeError("fake provider json failure")
        return dict(self.json_response)

    def metadata(self) -> dict[str, Any]:
        return {
            "active_provider": self.active_provider_name,
            "active_model": self.active_model,
            "available": True,
            "providers": {},
        }


@pytest.fixture
def fake_provider() -> FakeProviderManager:
    return FakeProviderManager()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "settings.json").write_text(
        json.dumps({"max_annunci": 5, "hours_old": 72}), encoding="utf-8"
    )
    return tmp_path


@pytest.fixture
def settings(workspace: Path) -> AppSettings:
    return load_settings(workspace)


@pytest.fixture
def db(settings: AppSettings) -> Database:
    d = Database(settings.db_path)
    yield d
    d.close()
