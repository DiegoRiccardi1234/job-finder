"""Subscription-backed, tool-free completions through native agent runtimes.

Credentials stay in the native clients. This module never reads auth files,
starts a login, connects to the shared daemon, or falls back to a billed API.
"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
import tomllib
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from app.providers.base import LLMProvider, TruncatedCompletionError

RuntimeKind = Literal["codex", "claude"]
_STATUS_TIMEOUT = 20
_MAX_OUTPUT = 8 * 1024 * 1024
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
_JSON_ENVELOPE = {
    "type": "object",
    "properties": {"json": {"type": "string"}},
    "required": ["json"],
    "additionalProperties": False,
}
_SAFE_MESSAGES = {
    "not_installed": "Runtime non installato: installare il client ufficiale.",
    "not_authenticated": "Accedere al client ufficiale con il proprio abbonamento.",
    "subscription_required": "Serve l'accesso tramite abbonamento, non una chiave API.",
    "quota_exhausted": "Quota dell'abbonamento temporaneamente esaurita.",
    "timeout": "Il runtime ha superato il tempo massimo della richiesta.",
    "cancelled": "Richiesta al runtime annullata.",
    "protocol_error": "Risposta del runtime non utilizzabile; verificare il client ufficiale.",
    "tools_disabled": "Il runtime ha richiesto uno strumento non consentito.",
    "busy": "Questa connessione sta già elaborando una richiesta.",
}


class RuntimeConnectionError(RuntimeError):
    """Safe, classified error suitable for an API response or usage log."""

    def __init__(self, code: str) -> None:
        self.code = code if code in _SAFE_MESSAGES else "protocol_error"
        self.status_code = {
            "not_authenticated": 401,
            "subscription_required": 403,
            "quota_exhausted": 429,
        }.get(self.code)
        super().__init__(_SAFE_MESSAGES[self.code])


def _safe_error(value: Any) -> RuntimeConnectionError:
    # Examine native errors in memory, but never propagate their text: it can
    # contain a request, local path, account identifier or credential.
    text = json.dumps(value, ensure_ascii=True, default=str).lower()
    if any(
        t in text
        for t in (
            "usage_limit",
            "usagelimit",
            "ratelimit",
            "rate_limit",
            "429",
            "quota",
            "sessionbudgetexceeded",
            "hit your limit",
            "usage limit",
            "rate limit",
        )
    ):
        return RuntimeConnectionError("quota_exhausted")
    if any(
        t in text
        for t in (
            "unauthorized",
            "not logged",
            "not authenticated",
            "authentication",
            "401",
            "login required",
            "please run /login",
        )
    ):
        return RuntimeConnectionError("not_authenticated")
    if any(t in text for t in ("cancelled", "canceled", "interrupted")):
        return RuntimeConnectionError("cancelled")
    return RuntimeConnectionError("protocol_error")


def _child_env() -> dict[str, str]:
    """Preserve the OS environment, remove billing and runtime injection knobs."""
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        name = key.upper()
        if name in {"CODEX_HOME", "CLAUDE_CONFIG_DIR"}:
            env[key] = value
            continue
        if name.startswith(("OPENAI_", "ANTHROPIC_", "CODEX_", "CLAUDE_")):
            continue
        if any(
            token in name
            for token in (
                "API_KEY",
                "AUTH_TOKEN",
                "ACCESS_TOKEN",
                "BEARER_TOKEN",
                "SESSION_TOKEN",
                "SECRET_ACCESS_KEY",
            )
        ):
            continue
        if name in {"NODE_OPTIONS", "NODE_PATH", "RUST_LOG", "RUST_BACKTRACE", "PYTHONPATH"}:
            continue
        env[key] = value
    env.update({"NO_COLOR": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"})
    return env


def _find_cli(kind: RuntimeKind) -> str | None:
    # Prefer native executables: killing one owns its lifetime, and no Windows
    # .cmd or PowerShell wrapper can reinterpret model IDs or launch a daemon.
    native = shutil.which(f"{kind}.exe") if os.name == "nt" else shutil.which(kind)
    if native:
        return native
    shim = shutil.which(kind)
    if not shim:
        return None
    path = Path(shim)
    if path.suffix.lower() not in {".cmd", ".bat", ".ps1"}:
        return str(path)
    if kind == "codex":
        package = path.parent / "node_modules" / "@openai" / "codex"
        roots = [package / "vendor", package / "node_modules" / "@openai"]
        for root in roots:
            matches = sorted(root.glob("**/bin/codex.exe")) if root.is_dir() else []
            if matches:
                return str(matches[0])
    return None


def _spawn(args: list[str], cwd: str) -> subprocess.Popen[str]:
    try:
        return subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=cwd,
            env=_child_env(),
            shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except OSError:
        raise RuntimeConnectionError("not_installed") from None


def _close(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        with suppress(OSError):
            process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            with suppress(OSError):
                process.kill()
            process.wait(timeout=3)
    for stream in (process.stdin, process.stdout):
        if stream is not None:
            stream.close()


def _run(
    args: list[str],
    cwd: str,
    timeout: float,
    prompt: str = "",
    *,
    on_spawn: Callable[[subprocess.Popen[str]], None] | None = None,
    allow_failure: bool = False,
) -> dict[str, Any]:
    process = _spawn(args, cwd)
    try:
        if on_spawn is not None:
            on_spawn(process)
        try:
            stdout, _ = process.communicate(input=prompt, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeConnectionError("timeout") from None
        if len(stdout) > _MAX_OUTPUT:
            raise RuntimeConnectionError("protocol_error")
        try:
            payload = json.loads(stdout)
        except (ValueError, TypeError):
            raise RuntimeConnectionError("protocol_error") from None
        if not isinstance(payload, dict):
            raise RuntimeConnectionError("protocol_error")
        if process.returncode and not payload.get("is_error") and not allow_failure:
            raise _safe_error(payload)
        return payload
    finally:
        _close(process)


def _codex_config() -> dict[str, Any]:
    settings: dict[str, Any] = {
        "model_provider": "openai",
        "forced_login_method": "chatgpt",
        "web_search": "disabled",
        "approval_policy": "never",
        "sandbox_mode": "read-only",
        "features.shell_tool": False,
        "features.unified_exec": False,
        "features.multi_agent": False,
        "features.multi_agent_v2": False,
        "features.apps": False,
        "features.plugins": False,
        "features.hooks": False,
        "features.skill_mcp_dependency_install": False,
        "features.skill_search": False,
        "features.skip_host_skill_discovery": True,
        "features.shell_snapshot": False,
        "features.memories": False,
        "features.code_mode": False,
        "features.code_mode_host": False,
        "features.computer_use": False,
        "project_doc_max_bytes": 0,
        "developer_instructions": "",
        "model_reasoning_effort": "low",
        "history.persistence": "none",
        "analytics.enabled": False,
        "notify": [],
    }
    # CLI overrides merge tables: setting mcp_servers={} alone would NOT clear
    # inherited servers. Read only config names, never auth.json or secret values.
    config_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    try:
        source = tomllib.loads((config_home / "config.toml").read_text(encoding="utf-8"))
    except FileNotFoundError:
        source = {}
    except (OSError, ValueError):
        raise RuntimeConnectionError("protocol_error") from None
    for table in [source, *source.get("profiles", {}).values()]:
        if isinstance(table, dict):
            for name in table.get("mcp_servers", {}):
                if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
                    raise RuntimeConnectionError("protocol_error")
                settings[f"mcp_servers.{name}.enabled"] = False
    return settings


class _CodexSession:
    def __init__(self, executable: str, cwd: str, timeout: float) -> None:
        args = [executable, "app-server", "--listen", "stdio://"]
        for key, value in _codex_config().items():
            args.extend(["-c", f"{key}={json.dumps(value)}"])
        self.process = _spawn(args, cwd)
        self.deadline = time.monotonic() + timeout
        self.events: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self.pending: list[dict[str, Any]] = []
        self.next_id = 0
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self) -> None:
        try:
            assert self.process.stdout is not None
            size = 0
            for line in self.process.stdout:
                size += len(line)
                if size > _MAX_OUTPUT:
                    break
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict):
                    self.events.put(event)
        except (OSError, ValueError):
            pass  # The owner can close the stream while cancelling.
        finally:
            self.events.put(None)

    def send(self, message: dict[str, Any]) -> None:
        try:
            assert self.process.stdin is not None
            self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
        except (OSError, ValueError):
            raise RuntimeConnectionError("protocol_error") from None

    def event(self) -> dict[str, Any]:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeConnectionError("timeout")
        try:
            event = self.events.get(timeout=remaining)
        except queue.Empty:
            raise RuntimeConnectionError("timeout") from None
        if event is None:
            raise RuntimeConnectionError("protocol_error")
        if "method" in event and "id" in event:
            # A native server request is never approved, even if inherited
            # managed configuration unexpectedly provides an extra tool.
            self.send({"id": event["id"], "error": {"code": -32601, "message": "Tools disabled"}})
            raise RuntimeConnectionError("tools_disabled")
        return event

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.next_id += 1
        request_id = self.next_id
        self.send({"id": request_id, "method": method, "params": params})
        while True:
            event = self.event()
            if event.get("id") != request_id:
                self.pending.append(event)
                continue
            if "error" in event:
                raise _safe_error(event["error"])
            result = event.get("result", {})
            if not isinstance(result, dict):
                raise RuntimeConnectionError("protocol_error")
            return result

    def initialize(self) -> dict[str, Any]:
        self.request("initialize", {"clientInfo": {"name": "job_finder", "version": "1.0.0"}})
        self.send({"method": "initialized", "params": {}})
        account = self.request("account/read", {"refreshToken": False}).get("account")
        if not isinstance(account, dict):
            raise RuntimeConnectionError("not_authenticated")
        if account.get("type") != "chatgpt":
            raise RuntimeConnectionError("subscription_required")
        return account

    def close(self) -> None:
        _close(self.process)
        self.reader.join(timeout=1)


@contextmanager
def _codex_session(
    executable: str,
    cwd: str,
    timeout: float,
    on_spawn: Callable[[subprocess.Popen[str]], None] | None = None,
) -> Iterator[_CodexSession]:
    session = _CodexSession(executable, cwd, timeout)
    try:
        if on_spawn is not None:
            on_spawn(session.process)
        yield session
    finally:
        session.close()


def _claude_args(executable: str) -> list[str]:
    return [
        executable,
        "--setting-sources",
        "",
        "--safe-mode",
        "--strict-mcp-config",
        "--tools",
        "",
        "--disable-slash-commands",
        "--no-chrome",
    ]


def _claude_account(
    executable: str,
    cwd: str,
    on_spawn: Callable[[subprocess.Popen[str]], None] | None = None,
    timeout: float = _STATUS_TIMEOUT,
) -> dict[str, Any]:
    account = _run(
        [*_claude_args(executable), "auth", "status", "--json"],
        cwd,
        timeout,
        on_spawn=on_spawn,
        allow_failure=True,
    )
    if not account.get("loggedIn"):
        raise RuntimeConnectionError("not_authenticated")
    if account.get("authMethod") not in {"claude.ai", "oauth_token"}:
        raise RuntimeConnectionError("subscription_required")
    return account


def get_runtime_status(kind: RuntimeKind) -> dict[str, Any]:
    """Non-inference native auth probe. Does not initiate or refresh login."""
    if kind not in {"codex", "claude"}:
        raise ValueError("Runtime non supportato")
    executable = _find_cli(kind)
    status: dict[str, Any] = {
        "installed": executable is not None,
        "version": None,
        "authenticated": False,
        "auth_method": None,
        "plan": None,
        "quota": None,
        "checked_at": datetime.now(UTC).isoformat(),
        "error": None,
    }
    if executable is None:
        status["error"] = "not_installed"
        return status
    with tempfile.TemporaryDirectory(prefix="job-finder-agent-", ignore_cleanup_errors=True) as cwd:
        try:
            process = _spawn([executable, "--version"], cwd)
            try:
                output, _ = process.communicate(timeout=_STATUS_TIMEOUT)
                match = re.search(r"\b\d+\.\d+\.\d+(?:[-+][\w.-]+)?", output)
                status["version"] = match.group(0) if match else None
            finally:
                _close(process)
            if kind == "codex":
                with _codex_session(executable, cwd, _STATUS_TIMEOUT) as session:
                    account = session.initialize()
                    status.update(
                        authenticated=True, auth_method="chatgpt", plan=account.get("planType")
                    )
                    try:
                        limits = session.request("account/rateLimits/read", {})
                        status["quota"] = _quota_summary(limits.get("rateLimits"))
                    except RuntimeConnectionError:
                        pass  # Unknown quota is distinct from zero quota.
            else:
                account = _claude_account(executable, cwd)
                status.update(
                    authenticated=True,
                    auth_method=account.get("authMethod"),
                    plan=account.get("subscriptionType"),
                )
        except subprocess.TimeoutExpired:
            status["error"] = "timeout"
        except RuntimeConnectionError as exc:
            status["error"] = exc.code
    return status


def _quota_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    out: dict[str, Any] = {}
    for key in ("primary", "secondary"):
        window = value.get(key)
        if isinstance(window, dict):
            out[key] = {
                k: window[k]
                for k in ("usedPercent", "windowDurationMins", "resetsAt")
                if isinstance(window.get(k), (int, float))
            }
    return out or None


class NativeRuntimeProvider(LLMProvider):
    def __init__(
        self, kind: RuntimeKind, connection_id: str, model: str | None, timeout_seconds: int
    ) -> None:
        self.kind = kind
        self.name = connection_id
        self._selected_model = model
        self.timeout_seconds = timeout_seconds
        self.last_usage = None
        self._process_guard = threading.Lock()
        self._active_process: subprocess.Popen[str] | None = None
        self._cancelled = threading.Event()
        self._cancel_generation = 0
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(connection_id, threading.Lock())

    def cancel_active(self) -> None:
        """Cancel this provider and reject pending work until reset_cancel()."""
        self._cancelled.set()
        with self._process_guard:
            self._cancel_generation += 1
            process = self._active_process
            if process is not None and process.poll() is None:
                with suppress(OSError):
                    process.terminate()

    def reset_cancel(self) -> None:
        """Called by the manager when starting an explicitly new operation."""
        if not self._lock.locked():
            self._cancelled.clear()

    def _own_process(self, process: subprocess.Popen[str]) -> None:
        with self._process_guard:
            self._active_process = process
            if self._cancelled.is_set():
                raise RuntimeConnectionError("cancelled")

    def is_available(self) -> bool:
        return bool(get_runtime_status(self.kind)["authenticated"])

    def list_models(self) -> list[str]:
        executable = _find_cli(self.kind)
        if executable is None:
            return []
        try:
            with tempfile.TemporaryDirectory(
                prefix="job-finder-agent-", ignore_cleanup_errors=True
            ) as cwd:
                if self.kind == "claude":
                    _claude_account(executable, cwd)
                    # Native CLI aliases, not an account entitlement claim.
                    return list(
                        dict.fromkeys(
                            [self._selected_model or "default", "sonnet", "opus", "haiku"]
                        )
                    )
                with _codex_session(executable, cwd, _STATUS_TIMEOUT) as session:
                    session.initialize()
                    result = session.request("model/list", {"limit": 100, "includeHidden": False})
                    return [
                        str(m["model"])
                        for m in result.get("data", [])
                        if isinstance(m, dict) and m.get("model")
                    ]
        except RuntimeConnectionError:
            return []

    def select_model(self, preferred_model: str | None = None) -> str:
        self._selected_model = preferred_model or self._selected_model or "default"
        return self._selected_model

    def complete_text(self, prompt: str, model: str | None = None, max_tokens: int = 700) -> str:
        return self.chat([{"role": "user", "content": prompt}], model, max_tokens)

    def chat(
        self, messages: list[dict[str, str]], model: str | None = None, max_tokens: int = 700
    ) -> str:
        return self._complete(messages, model, max_tokens, None)[0]

    def complete_json(
        self,
        prompt: str,
        model: str | None = None,
        max_tokens: int = 700,
        *,
        output_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        schema = output_schema or _JSON_ENVELOPE
        if output_schema is None:
            prompt += '\nReturn {"json":"<the requested complete JSON object serialized as a string>"}. Preserve every requested key and array item inside that string.'
        text, structured = self._complete(
            [{"role": "user", "content": prompt}], model, max_tokens, schema
        )
        try:
            parsed = structured if structured is not None else json.loads(text)
            if output_schema is None:
                parsed = json.loads(parsed["json"])
        except (ValueError, TypeError, KeyError):
            raise RuntimeConnectionError("protocol_error") from None
        if not isinstance(parsed, dict):
            raise RuntimeConnectionError("protocol_error")
        return cast(dict[str, Any], parsed)

    def _complete(
        self,
        messages: list[dict[str, str]],
        model: str | None,
        max_tokens: int,
        schema: dict[str, Any] | None,
    ) -> tuple[str, dict[str, Any] | None]:
        deadline = time.monotonic() + self.timeout_seconds
        generation = self._cancel_generation
        while True:
            if self._cancelled.is_set() or generation != self._cancel_generation:
                raise RuntimeConnectionError("cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeConnectionError("timeout")
            if self._lock.acquire(timeout=min(0.1, remaining)):
                break
        self.last_usage = None
        try:
            if self._cancelled.is_set():
                raise RuntimeConnectionError("cancelled")
            executable = _find_cli(self.kind)
            if executable is None:
                raise RuntimeConnectionError("not_installed")
            resolved_model = model or self._selected_model
            if resolved_model == "default":
                resolved_model = None
            prompt = json.dumps({"messages": messages}, ensure_ascii=False)
            if len(prompt) > 2 * 1024 * 1024:
                raise ValueError("Richiesta troppo grande")
            instructions = f"Act as a text completion service. Follow the roles in the provided messages. Use only the supplied data, without tools. Aim for at most {max_tokens} output tokens; complete the requested JSON even if longer."
            with tempfile.TemporaryDirectory(
                prefix="job-finder-agent-", ignore_cleanup_errors=True
            ) as cwd:
                if self.kind == "claude":
                    _claude_account(
                        executable,
                        cwd,
                        self._own_process,
                        min(_STATUS_TIMEOUT, self._remaining(deadline)),
                    )
                    return self._claude(
                        executable,
                        cwd,
                        prompt,
                        instructions,
                        resolved_model,
                        schema,
                        self._remaining(deadline),
                    )
                return self._codex(
                    executable,
                    cwd,
                    prompt,
                    instructions,
                    resolved_model,
                    schema,
                    self._remaining(deadline),
                )
        except RuntimeConnectionError:
            if self._cancelled.is_set():
                raise RuntimeConnectionError("cancelled") from None
            raise
        finally:
            with self._process_guard:
                self._active_process = None
            self._lock.release()

    def _remaining(self, deadline: float) -> float:
        if self._cancelled.is_set():
            raise RuntimeConnectionError("cancelled")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeConnectionError("timeout")
        return remaining

    def _claude(
        self,
        executable: str,
        cwd: str,
        prompt: str,
        instructions: str,
        model: str | None,
        schema: dict[str, Any] | None,
        timeout: float,
    ) -> tuple[str, dict[str, Any] | None]:
        args = [
            *_claude_args(executable),
            "-p",
            "--output-format",
            "json",
            "--no-session-persistence",
            "--system-prompt",
            instructions,
        ]
        if model:
            args.extend(["--model", model])
        if schema is not None:
            args.extend(["--json-schema", json.dumps(schema)])
        payload = _run(args, cwd, timeout, prompt, on_spawn=self._own_process)
        if payload.get("stop_reason") in {"max_tokens", "max_output_tokens", "length"}:
            raise TruncatedCompletionError(model or "default")
        if payload.get("is_error") or payload.get("subtype") not in {None, "success"}:
            if payload.get("subtype") in {
                "error_max_turns",
                "error_max_budget_usd",
                "error_max_structured_output_retries",
            }:
                raise TruncatedCompletionError(model or "default")
            raise _safe_error(payload)
        usage = payload.get("usage", {})
        if isinstance(usage, dict):
            try:
                prompt_tokens = sum(
                    int(usage.get(key) or 0)
                    for key in (
                        "input_tokens",
                        "cache_creation_input_tokens",
                        "cache_read_input_tokens",
                    )
                )
            except (TypeError, ValueError):
                prompt_tokens = 0
            self._set_usage(prompt_tokens, usage.get("output_tokens", 0))
        structured = payload.get("structured_output")
        text = str(payload.get("result") or "").strip()
        if not text and not isinstance(structured, dict):
            raise RuntimeConnectionError("protocol_error")
        return text, structured if isinstance(structured, dict) else None

    def _codex(
        self,
        executable: str,
        cwd: str,
        prompt: str,
        instructions: str,
        model: str | None,
        schema: dict[str, Any] | None,
        timeout: float,
    ) -> tuple[str, dict[str, Any] | None]:
        with _codex_session(executable, cwd, timeout, self._own_process) as session:
            session.initialize()
            params: dict[str, Any] = {
                "cwd": cwd,
                "ephemeral": True,
                "modelProvider": "openai",
                "approvalPolicy": "never",
                "sandbox": "read-only",
                "baseInstructions": instructions,
                "developerInstructions": "Use no tools. Return only the requested answer.",
            }
            if model:
                params["model"] = model
            thread = session.request("thread/start", params).get("thread", {})
            thread_id = thread.get("id")
            if not thread_id:
                raise RuntimeConnectionError("protocol_error")
            turn_params: dict[str, Any] = {
                "threadId": thread_id,
                "input": [{"type": "text", "text": prompt}],
                "summary": "none",
            }
            if schema is not None:
                turn_params["outputSchema"] = schema
            turn = session.request("turn/start", turn_params).get("turn", {})
            turn_id = turn.get("id")
            text = ""
            while True:
                event = session.pending.pop(0) if session.pending else session.event()
                data = event.get("params", {})
                if data.get("threadId") not in {None, thread_id} or data.get("turnId") not in {
                    None,
                    turn_id,
                }:
                    continue
                method = event.get("method")
                if method == "thread/tokenUsage/updated":
                    usage = data.get("tokenUsage", {}).get("last", {})
                    self._set_usage(usage.get("inputTokens", 0), usage.get("outputTokens", 0))
                elif method == "item/completed":
                    item = data.get("item", {})
                    if item.get("type") == "agentMessage":
                        if item.get("phase") != "commentary":
                            text = str(item.get("text") or "")
                    elif item.get("type") not in {"reasoning", "userMessage", "plan"}:
                        raise RuntimeConnectionError("tools_disabled")
                elif method == "turn/completed":
                    completed = data.get("turn", {})
                    error = completed.get("error") or {}
                    if (
                        isinstance(error, dict)
                        and error.get("codexErrorInfo") == "contextWindowExceeded"
                    ):
                        raise TruncatedCompletionError(model or "default")
                    if completed.get("status") != "completed":
                        raise _safe_error(completed)
                    if not text:
                        for item in completed.get("items", []):
                            if item.get("type") == "agentMessage":
                                text = str(item.get("text") or "")
                    if not text.strip():
                        raise RuntimeConnectionError("protocol_error")
                    return text.strip(), None

    def _set_usage(self, prompt: Any, completion: Any) -> None:
        try:
            input_tokens, output_tokens = int(prompt or 0), int(completion or 0)
        except (TypeError, ValueError):
            return
        self.last_usage = {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }


def create_runtime_provider(
    kind: RuntimeKind, connection_id: str, model: str | None = None, timeout_seconds: int = 300
) -> LLMProvider:
    if kind not in {"codex", "claude"}:
        raise ValueError("Runtime non supportato")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", connection_id):
        raise ValueError("Identificatore connessione non valido")
    if not 1 <= timeout_seconds <= 1800:
        raise ValueError("Timeout non valido")
    return NativeRuntimeProvider(kind, connection_id, model, timeout_seconds)
