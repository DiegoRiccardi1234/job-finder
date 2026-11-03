from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from app.ai_connections import ConnectionRegistry
from app.config import load_settings
from app.db import Database
from app.models import ScanRequest
from app.providers.factory import ProviderManager
from app.scoring_schema import ANSWERED_BY_KEY
from app.services import scanner_service as ss


def test_secrets_are_never_returned_and_legacy_settings_remain(tmp_path: Path) -> None:
    settings = load_settings(tmp_path)
    original = {"custom_base_url": "http://localhost:11434/v1", "custom_api_key": "old-private", "google_api_key": "keep"}
    (settings.data_dir / "local_secrets.json").write_text(json.dumps(original), encoding="utf-8")
    registry = ConnectionRegistry(settings.data_dir)
    registry.save("endpoint-hermes", {"name": "Hermes", "base_url": "http://127.0.0.1:8642/v1",
                                     "enabled": True, "api_key": "new-private", "model": "hermes-agent"})
    public = json.dumps(registry.public_connections(settings))
    assert "new-private" not in public and "old-private" not in public
    assert registry.secret("endpoint-hermes") == "new-private"
    assert registry.secret("legacy-custom") == "old-private"
    assert json.loads((settings.data_dir / "local_secrets.json").read_text()) == original
    assert "new-private" not in registry.path.read_text()


@pytest.mark.parametrize("url", ["http://example.com/v1", "https://user:secret@example.com/v1", "file:///tmp/model", "https://example.com/v1?api_key=secret", "http://localhost:not-a-port/v1", "http://localhost:0/v1"])
def test_rejects_urls_that_leak_credentials_or_use_remote_cleartext(tmp_path: Path, url: str) -> None:
    with pytest.raises(ValueError):
        ConnectionRegistry(tmp_path).save("endpoint-test", {"base_url": url})


def _manager(tmp_path: Path) -> ProviderManager:
    return ProviderManager(load_settings(tmp_path))


class _Provider:
    name = "stub"
    last_usage = None

    def __init__(self, calls: list[str], cid: str, value: Any) -> None:
        self.calls, self.cid, self.value = calls, cid, value

    def select_model(self) -> str:
        return "test-model"

    def complete_json(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(self.cid)
        if isinstance(self.value, Exception):
            raise self.value
        return dict(self.value)

    def chat(self, **kwargs: Any) -> str:
        self.calls.append(self.cid)
        return "text"


def test_route_uses_only_opted_in_fallbacks_and_stamps_actual_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(tmp_path)
    for cid in ("codex", "claude"):
        manager.connections.save(cid, {"enabled": True})
    manager.connections.save_routing({"cv": {"primary": "codex", "fallbacks": ["claude"]}}, manager.settings)
    calls: list[str] = []
    monkeypatch.setattr("app.providers.connection_routing.provider_for", lambda _, cid, **kw: _Provider(calls, cid, RuntimeError("quota") if cid == "codex" else {"content": "ok"}))
    monkeypatch.setattr(manager, "_run_with_failover", lambda **kwargs: pytest.fail("Unexpected legacy API call"))
    result = manager.complete_json("synthetic")
    assert calls == ["codex", "claude"]
    assert result[ANSWERED_BY_KEY] == "claude/test-model"


def test_exhausted_route_does_not_fall_through_to_api_pool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(tmp_path)
    manager.connections.save("codex", {"enabled": True})
    manager.connections.save_routing({"cv": {"primary": "codex"}}, manager.settings)
    monkeypatch.setattr("app.providers.connection_routing.provider_for", lambda *a, **kw: _Provider([], "codex", RuntimeError("quota")))
    monkeypatch.setattr(manager, "_run_with_failover", lambda **kwargs: pytest.fail("Paid API leak"))
    with pytest.raises(RuntimeError, match="quota"):
        manager.complete_json("synthetic")


@pytest.mark.parametrize("task", ["chat", "scoring", "cv"])
def test_codex_only_quota_failure_never_calls_an_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, task: str
) -> None:
    from app.agent_runtime import RuntimeConnectionError

    manager = _manager(tmp_path)
    manager.connections.save("codex", {"enabled": True})
    manager.connections.save_routing(
        {task: {"primary": "codex", "fallbacks": []}}, manager.settings
    )
    calls: list[str] = []

    class QuotaProvider(_Provider):
        def chat(self, **kwargs: Any) -> str:
            self.calls.append(self.cid)
            raise self.value

    def provider_for(_: Any, cid: str, **kwargs: Any) -> QuotaProvider:
        assert cid == "codex" and kwargs["task"] == task
        return QuotaProvider(calls, cid, RuntimeConnectionError("quota_exhausted"))

    monkeypatch.setattr("app.providers.connection_routing.provider_for", provider_for)
    monkeypatch.setattr(manager, "_run_with_failover", lambda **_: pytest.fail("API fallback"))
    with pytest.raises(RuntimeConnectionError) as error:
        if task == "chat":
            manager.chat([{"role": "user", "content": "synthetic"}])
        else:
            manager.complete_json("synthetic", task=task)
    assert error.value.code == "quota_exhausted"
    assert error.value.status_code == 429
    assert calls == ["codex"]


def test_cv_prose_retry_keeps_cv_route_and_ignores_legacy_api_model_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.generation import generate_with_profile

    manager = _manager(tmp_path)
    for cid in ("codex", "claude"):
        manager.connections.save(cid, {"enabled": True})
    manager.connections.save_routing({
        "cv": {"primary": "codex", "fallbacks": []},
        "chat": {"primary": "claude", "fallbacks": []},
    }, manager.settings)
    calls: list[str] = []

    def provider_for(_: Any, cid: str, **kwargs: Any) -> _Provider:
        assert cid == "codex" and kwargs["task"] == "cv"
        return _Provider(calls, cid, ValueError("Nessun JSON trovato"))

    monkeypatch.setattr("app.providers.connection_routing.provider_for", provider_for)
    monkeypatch.setattr(manager, "_run_with_failover", lambda **_: pytest.fail("API fallback"))
    result = generate_with_profile(
        manager, "cv_review", "Synthetic CV", {},
        **manager.pin_kwargs("legacy-api-model"),
    )
    assert result == "text"
    assert calls == ["codex", "codex"]


def test_cv_quota_error_does_not_retry_as_prose_or_call_apis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.agent_runtime import RuntimeConnectionError
    from app.services.generation import generate_with_profile

    manager = _manager(tmp_path)
    manager.connections.save("codex", {"enabled": True})
    manager.connections.save_routing(
        {"cv": {"primary": "codex", "fallbacks": []}}, manager.settings
    )
    calls: list[str] = []
    monkeypatch.setattr("app.providers.connection_routing.provider_for", lambda *_, **kw:
                        _Provider(calls, "codex", RuntimeConnectionError("quota_exhausted")))
    monkeypatch.setattr(manager, "chat", lambda *_, **kw: pytest.fail("Unexpected second turn"))
    monkeypatch.setattr(manager, "_run_with_failover", lambda **_: pytest.fail("API fallback"))
    with pytest.raises(RuntimeConnectionError) as error:
        generate_with_profile(manager, "cv_improve", "Synthetic CV", {})
    assert error.value.code == "quota_exhausted"
    assert calls == ["codex"]


def test_routes_are_independent_and_legacy_mode_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(tmp_path)
    manager.connections.save("codex", {"enabled": True})
    manager.connections.save_routing({"scoring": {"primary": "codex"}}, manager.settings)
    calls: list[str] = []
    monkeypatch.setattr("app.providers.connection_routing.provider_for", lambda *a, **kw: _Provider(calls, "codex", {"punteggio": 8}))
    monkeypatch.setattr(manager, "_run_with_failover", lambda **kwargs: "legacy-chat")
    assert manager.chat([]) == "legacy-chat"
    assert manager.complete_json("synthetic", **ss._scoring_call_kwargs(manager))["punteggio"] == 8
    assert calls == ["codex"]


def test_connection_in_route_cannot_be_deleted(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.connections.save("claude", {"enabled": True})
    manager.connections.save_routing({"chat": {"primary": "claude"}}, manager.settings)
    with pytest.raises(ValueError, match="in_use"):
        manager.connections.delete("claude")


def test_connection_ids_cannot_shadow_legacy_api_provider_pins(tmp_path: Path) -> None:
    registry = ConnectionRegistry(tmp_path)
    with pytest.raises(ValueError, match="reserved_api_provider_id"):
        registry.save("google", {"name": "Fake Google", "base_url": "http://localhost:8642/v1"})


def test_private_legacy_url_credentials_are_not_returned(tmp_path: Path) -> None:
    settings = load_settings(tmp_path)
    (settings.data_dir / "local_secrets.json").write_text(json.dumps({
        "custom_base_url": "https://user:private-password@example.com/v1?api_key=private-query"}), encoding="utf-8")
    text = json.dumps(ConnectionRegistry(settings.data_dir).public_connections(settings))
    assert "private-password" not in text and "private-query" not in text


def test_cancelled_api_candidate_cannot_start_a_new_native_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(tmp_path)
    manager.connections.save("codex", {"enabled": True})
    manager.connections.save_routing({"scoring": {"primary": "api:google", "fallbacks": ["codex"]}}, manager.settings)

    def cancel_during_api(**kwargs: Any) -> Any:
        manager.cancel_connections("scoring")
        raise RuntimeError("provider failed after Stop")

    monkeypatch.setattr(manager, "_run_with_failover", cancel_during_api)
    monkeypatch.setattr("app.providers.connection_routing.provider_for", lambda *a, **kw: pytest.fail("native started after cancellation"))
    with pytest.raises(RuntimeError) as exc:
        manager.complete_json("synthetic", task="scoring")
    assert getattr(exc.value, "code", "") == "cancelled"


def test_native_instances_survive_model_edits_and_cancel_is_task_scoped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.providers.connection_routing import provider_for

    manager = _manager(tmp_path)
    manager.connections.save("codex", {"enabled": True, "model": "model-a"})

    class Native:
        cancelled = False
        def cancel_active(self):
            self.cancelled = True

    monkeypatch.setattr("app.agent_runtime.create_runtime_provider", lambda *a, **kw: Native())
    scoring = provider_for(manager, "codex", task="scoring")
    chat = provider_for(manager, "codex", task="chat")
    manager.connections.save("codex", {"model": "model-b"})
    assert provider_for(manager, "codex", task="scoring") is scoring
    manager.cancel_connections("scoring")
    assert scoring.cancelled and not chat.cancelled


def test_malformed_registry_is_reported_as_configuration_error(tmp_path: Path) -> None:
    registry = ConnectionRegistry(tmp_path)
    registry.path.write_text('{"routing": []}', encoding="utf-8")
    with pytest.raises(ValueError, match="ai_connections_config_invalid"):
        registry.routing()


def test_constructor_failure_still_uses_explicit_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(tmp_path)
    for cid in ("codex", "claude"):
        manager.connections.save(cid, {"enabled": True})
    manager.connections.save_routing({"cv": {"primary": "codex", "fallbacks": ["claude"]}}, manager.settings)
    calls: list[str] = []
    def build(_, cid, **kwargs):
        if cid == "codex":
            raise ValueError("cannot construct provider")
        return _Provider(calls, cid, {"content": "ok"})
    monkeypatch.setattr("app.providers.connection_routing.provider_for", build)
    assert manager.complete_json("synthetic")["content"] == "ok"
    assert calls == ["claude"]


def test_collect_only_never_touches_provider_or_retention(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_settings(tmp_path)
    settings.delay_tra_ricerche = 0
    db = Database(settings.db_path)
    db.set_preference("daily_request_limit", "1")
    text = "Python SQL frontend React REST API integrazione dati junior in team. " * 8
    monkeypatch.setattr(ss, "scrape_jobs", lambda **_: pd.DataFrame([{
        "title": "Python developer junior", "company": "Synthetic Co", "description": text,
        "location": "Torino", "site": "indeed", "job_url": "https://example.com/job/1",
    }]))
    monkeypatch.setattr(ss.quota, "blocks_scan", lambda _: pytest.fail("collect mode must not check inference quota"))
    monkeypatch.setattr(ss, "apply_post_scan_lifecycle", lambda **_: pytest.fail("collect mode must not archive"))

    class NoInference:
        def __getattr__(self, name: str) -> Any:
            if name in {"has_connection_route", "settings"}:
                raise AttributeError(name)
            pytest.fail(f"collect-only touched provider method {name}")

    try:
        events = list(ss.run_scan(db, settings, NoInference(), ScanRequest(
            search_terms=["Python junior"], locations=["Torino"], sites=["indeed"]), analysis_mode="collect"))
        collected = [e for e in events if e.get("status") == "collected"]
        assert len(collected) == 1
        assert events[-1]["totale_analizzati"] == 0
        assert events[-1]["archiviati"] == 0
        assert db.get_job(collected[0]["job_id"])["punteggio_ai"] is None
    finally:
        db.close()
