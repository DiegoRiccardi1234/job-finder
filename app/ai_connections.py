"""Per-install AI connections and explicit, task-specific fallback routes.

Reading this registry never launches an agent or calls a remote model. Existing
provider settings remain authoritative until the user selects a new route.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.config import LOCAL_SECRETS_FILE, SUPPORTED_PROVIDERS

TASKS = ("chat", "scoring", "cv")
KINDS = ("codex", "claude", "openai_compatible")
_LOCK = threading.RLock()
_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$")


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("ai_connections_config_unreadable") from exc
    if not isinstance(value, dict):
        raise ValueError("ai_connections_config_invalid")
    return value


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


class ConnectionRegistry:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.path = data_dir / "ai_connections.json"
        # A separate private file avoids rewriting legacy keys or racing with
        # the existing provider-keys editor. It is never included in responses.
        self.secrets_path = data_dir / "ai_connection_secrets.json"

    def _configuration(self) -> dict[str, Any]:
        with _LOCK:
            config = _read(self.path)
            connections, routing = config.get("connections", {}), config.get("routing", {})
            if not isinstance(connections, dict) or not isinstance(routing, dict):
                raise ValueError("ai_connections_config_invalid")
            for cid, row in connections.items():
                if (
                    not isinstance(row, dict)
                    or row.get("kind") not in KINDS
                    or not _ID.fullmatch(cid)
                    or cid in SUPPORTED_PROVIDERS
                ):
                    raise ValueError("ai_connections_config_invalid")
            for task, route in routing.items():
                if task not in TASKS or not isinstance(route, dict):
                    raise ValueError("ai_connections_config_invalid")
                primary, fallbacks = route.get("primary"), route.get("fallbacks", [])
                if (
                    (primary is not None and not isinstance(primary, str))
                    or not isinstance(fallbacks, list)
                    or not all(isinstance(cid, str) for cid in fallbacks)
                ):
                    raise ValueError("ai_connections_config_invalid")
            return config

    def connections(self) -> list[dict[str, Any]]:
        config = self._configuration()
        records: list[dict[str, Any]] = [
            {"id": "codex", "name": "Codex", "kind": "codex", "enabled": False, "model": ""},
            {
                "id": "claude",
                "name": "Claude Code",
                "kind": "claude",
                "enabled": False,
                "model": "",
            },
        ]
        saved = config.get("connections", {})
        if isinstance(saved, dict):
            by_id = {item["id"]: item for item in records}
            for cid, item in saved.items():
                if isinstance(item, dict) and _ID.fullmatch(cid):
                    by_id[cid] = {**item, "id": cid}
            records = list(by_id.values())
        # The legacy custom endpoint already is an OpenAI-compatible connection.
        legacy = _read(self.data_dir / LOCAL_SECRETS_FILE)
        if legacy.get("custom_base_url") and not any(c["id"] == "legacy-custom" for c in records):
            records.append(
                {
                    "id": "legacy-custom",
                    "name": "Custom (legacy)",
                    "kind": "openai_compatible",
                    "enabled": True,
                    "base_url": legacy["custom_base_url"],
                    "model": legacy.get("preferred_model", ""),
                    "legacy": True,
                }
            )
        return records

    def get(self, connection_id: str) -> dict[str, Any] | None:
        return next((row for row in self.connections() if row["id"] == connection_id), None)

    def secret(self, connection_id: str) -> str | None:
        with _LOCK:
            keys = _read(self.secrets_path)
            value = keys.get(connection_id)
            if value is None and connection_id == "legacy-custom":
                value = _read(self.data_dir / LOCAL_SECRETS_FILE).get("custom_api_key")
            return str(value) if value else None

    def public_connections(self, settings: Any) -> list[dict[str, Any]]:
        result = []
        for row in self.connections():
            if row.get("legacy"):
                # Old Custom URLs may carry credentials in their user-info or
                # query string. Keep the original only in the private settings.
                parsed = urlsplit(str(row.get("base_url", "")))
                if parsed.username or parsed.password or parsed.query or parsed.fragment:
                    row = {**row, "base_url": "", "status": "legacy_url_contains_credentials"}
            result.append(
                {
                    **{k: v for k, v in row.items() if k != "api_key"},
                    "secret_configured": bool(self.secret(row["id"])),
                    "configured": bool(row.get("enabled"))
                    and (row["kind"] in ("codex", "claude") or bool(row.get("base_url"))),
                    "capabilities": {"chat": True, "json": True},
                }
            )
        for name in SUPPORTED_PROVIDERS:
            configured = bool(getattr(settings, f"{name}_api_key", None))
            if name == "custom":
                configured = bool(getattr(settings, "custom_base_url", None))
            result.append(
                {
                    "id": f"api:{name}",
                    "name": name,
                    "kind": "api",
                    "enabled": True,
                    "configured": configured,
                    "secret_configured": configured,
                    "readonly": True,
                    "model": "",
                }
            )
        return result

    def routing(self) -> dict[str, dict[str, Any]]:
        saved = self._configuration().get("routing", {})
        return {
            task: {
                "primary": (saved.get(task) or {}).get("primary"),
                "fallbacks": list((saved.get(task) or {}).get("fallbacks", [])),
            }
            for task in TASKS
        }

    def route(self, task: str) -> list[str]:
        row = self.routing().get(task, {})
        primary = row.get("primary")
        return [str(primary), *row.get("fallbacks", [])] if primary else []

    def save(self, connection_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not _ID.fullmatch(connection_id):
            raise ValueError("invalid_connection_id")
        if connection_id in SUPPORTED_PROVIDERS:
            raise ValueError("reserved_api_provider_id")
        with _LOCK:
            previous = self.get(connection_id) or {}
            kind = payload.get("kind", previous.get("kind", "openai_compatible"))
            if kind not in KINDS:
                raise ValueError("invalid_connection_kind")
            if connection_id in ("codex", "claude") and kind != connection_id:
                raise ValueError("native_connection_kind_immutable")
            if kind in ("codex", "claude") and connection_id != kind:
                raise ValueError("duplicate_native_connection")
            name = str(payload.get("name", previous.get("name", connection_id))).strip()
            model = str(payload.get("model", previous.get("model", "")) or "").strip()
            if (
                not name
                or len(name) > 100
                or len(model) > 200
                or any(c in model for c in "\r\n\x00")
            ):
                raise ValueError("invalid_connection_fields")
            row = {
                "id": connection_id,
                "name": name,
                "kind": kind,
                "model": model,
                "enabled": bool(payload.get("enabled", previous.get("enabled", False))),
            }
            if kind == "openai_compatible":
                base = (
                    str(payload.get("base_url", previous.get("base_url", ""))).strip().rstrip("/")
                )
                try:
                    parsed = urlsplit(base)
                    port = parsed.port
                except ValueError:
                    raise ValueError("invalid_connection_url") from None
                if port is not None and not 1 <= port <= 65535:
                    raise ValueError("invalid_connection_url")
                if (
                    parsed.scheme not in ("http", "https")
                    or not parsed.hostname
                    or parsed.username
                    or parsed.password
                    or parsed.query
                    or parsed.fragment
                ):
                    raise ValueError("invalid_connection_url")
                if parsed.scheme == "http" and parsed.hostname not in (
                    "localhost",
                    "127.0.0.1",
                    "::1",
                ):
                    raise ValueError("remote_connection_requires_https")
                row["base_url"] = base
            config = _read(self.path)
            config.setdefault("connections", {})[connection_id] = row
            if "api_key" in payload and payload["api_key"] is not None:
                key = str(payload["api_key"]).strip()
                if len(key) > 8192 or any(c in key for c in "\r\n\x00"):
                    raise ValueError("invalid_connection_key")
                keys = _read(self.secrets_path)
                if key:
                    keys[connection_id] = key
                else:
                    keys.pop(connection_id, None)
                _write(self.secrets_path, keys)
            _write(self.path, config)
            return row

    def delete(self, connection_id: str) -> None:
        with _LOCK:
            if any(connection_id in self.route(task) for task in TASKS):
                raise ValueError("connection_in_use")
            config = _read(self.path)
            config.setdefault("connections", {}).pop(connection_id, None)
            # Native defaults remain visible and can be configured again.
            if connection_id in ("codex", "claude"):
                config["connections"][connection_id] = {
                    "id": connection_id,
                    "name": "Codex" if connection_id == "codex" else "Claude Code",
                    "kind": connection_id,
                    "enabled": False,
                    "model": "",
                }
            if connection_id == "legacy-custom":
                raise ValueError("legacy_connection_managed_in_provider_settings")
            keys = _read(self.secrets_path)
            keys.pop(connection_id, None)
            _write(self.secrets_path, keys)
            _write(self.path, config)

    def save_routing(self, payload: dict[str, Any], settings: Any) -> dict[str, Any]:
        if set(payload) - set(TASKS):
            raise ValueError("invalid_routing_task")
        with _LOCK:
            known = {row["id"]: row for row in self.public_connections(settings)}
            routing = self.routing()
            for task, choice in payload.items():
                if not isinstance(choice, dict) or set(choice) - {"primary", "fallbacks"}:
                    raise ValueError("invalid_route")
                primary = choice.get("primary") or None
                fallbacks = choice.get("fallbacks", [])
                if not isinstance(fallbacks, list) or len(fallbacks) > 16:
                    raise ValueError("invalid_fallbacks")
                ids = ([primary] if primary else []) + fallbacks
                if (not primary and fallbacks) or len(ids) != len(set(ids)):
                    raise ValueError("invalid_fallbacks")
                if any(
                    not isinstance(cid, str) or cid not in known or not known[cid]["enabled"]
                    for cid in ids
                ):
                    raise ValueError("connection_unavailable")
                routing[task] = {"primary": primary, "fallbacks": fallbacks}
            config = _read(self.path)
            config["routing"] = routing
            _write(self.path, config)
            return routing
