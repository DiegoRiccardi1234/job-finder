r"""Exercise a candidate frozen bundle on synthetic data, never the installed DB.

PowerShell: .venv\Scripts\python.exe scripts\verify_agent_bundle.py --bundle <candidate>
Add --live-codex to exercise subscription completions explicitly (uses plan quota).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx
from mcp import Client
from mcp.client.stdio import StdioServerParameters

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db import Database
from app.install_layout import is_organized, runtime_dir, state_dir


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8124)
    parser.add_argument("--live-codex", action="store_true")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--expected-version")
    args = parser.parse_args()
    bundle = args.bundle.resolve()
    # Candidate verification is intentionally not an installed-app updater.
    data_dir = state_dir(bundle)
    if bundle == Path(r"C:\Diego\All Finder\JobFinder") or (data_dir.exists() and any(data_dir.iterdir())):
        raise RuntimeError("Use a fresh candidate bundle without an existing data directory")
    data_dir.mkdir(exist_ok=True)
    db = Database(data_dir / "searcher.db")
    profile_id = db.save_candidate_profile(
        source_name="synthetic-verification", markdown="Synthetic candidate. Computer Science bachelor. Python, SQL, REST APIs. Based in Torino. Junior looking for mentorship.",
        summary={"skills": ["Python", "SQL"], "preferred_roles": ["application support junior"], "base_city": "Torino"},
    )
    db.set_active_profile(profile_id)
    job_id, _, _ = db.upsert_job({"titolo": "Junior application support", "azienda": "Synthetic Company",
        "sede": "Torino", "link": "https://example.invalid/synthetic-job", "fonte": "synthetic",
        "descrizione": "Junior application support in Torino. SQL queries, Python automation and REST integration. Mentorship and paid training. " * 5})
    db.close()
    env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY") and k not in {
        "CUSTOM_BASE_URL", "GLM_BASE_URL", "CLOUDFLARE_BASE_URL", "SEARCHER_DB_PATH", "JOBFINDER_WORKSPACE"}}
    env.update({"PORT": str(args.port), "JOBFINDER_NO_BROWSER": "1", "JOBFINDER_PAUSE_BACKGROUND": "1"})
    base = f"http://127.0.0.1:{args.port}"
    report: dict = {"bundle": str(bundle), "synthetic_only": True, "live_codex": args.live_codex,
                   "organized_layout": is_organized(bundle)}
    process = subprocess.Popen([str(bundle / "JobFinder.exe")], cwd=bundle, env=env,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        with httpx.Client(base_url=base, trust_env=False, timeout=330) as client:
            deadline = time.monotonic() + 90
            while True:
                try:
                    response = client.get("/api/health", timeout=2)
                    if response.is_success:
                        break
                except httpx.HTTPError:
                    pass
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("Candidate did not become ready; inspect candidate data/logs")
                time.sleep(.25)
            report["health"] = response.status_code
            if args.expected_version:
                version = client.get("/api/version")
                version.raise_for_status()
                assert version.json()["current"] == args.expected_version
                report["version"] = args.expected_version
            session = client.post("/api/agent/ui/session", headers={"Origin": base, "X-JobFinder-UI": "1"})
            session.raise_for_status()
            headers = {"Origin": base, "X-JobFinder-CSRF": session.json()["csrf_token"]}
            setup = client.post("/api/agent/setup", headers=headers)
            setup.raise_for_status()
            token_file = Path(setup.json()["token_file"])
            report["setup_configs"] = sorted(setup.json()["configs"])

            async def stdio() -> None:
                parameters = StdioServerParameters(command=str(runtime_dir(bundle) / "JobFinderMCP.exe"),
                    args=["--url", base, "--token-file", str(token_file)])
                async with Client(parameters, read_timeout_seconds=30) as mcp:
                    names = {tool.name for tool in (await mcp.list_tools()).tools}
                    assert len(names) == 10
                    context = await mcp.call_tool("jobfinder_get_context", {})
                    assert not context.is_error
                    note = {"job_id": job_id, "action": "note", "note": "Synthetic MCP acceptance", "request_id": "bundle-note-1"}
                    first = await mcp.call_tool("jobfinder_manage_job", note)
                    second = await mcp.call_tool("jobfinder_manage_job", note)
                    assert not first.is_error and first.structured_content == second.structured_content
                    prepared = await mcp.call_tool("jobfinder_prepare_analysis", {"job_ids": [job_id]})
                    assert not prepared.is_error
                    report["stdio_tools"] = len(names)
                    report["stdio_read_write_idempotency"] = True
            asyncio.run(stdio())
            if args.live_codex:
                saved = client.put("/api/ai/connections/codex", headers=headers, json={"enabled": True})
                saved.raise_for_status()
                routed = client.put("/api/ai/routing", headers=headers, json={task: {"primary": "codex", "fallbacks": []} for task in ("chat", "scoring", "cv")})
                routed.raise_for_status()
                diagnostic = client.get("/api/ai/runtimes/codex/status")
                diagnostic.raise_for_status()
                report["codex_authenticated"] = diagnostic.json().get("authenticated")
                test = client.post("/api/ai/connections/codex/test", headers=headers)
                test.raise_for_status()
                assert test.json()["ok"], test.json()
                report["codex_synthetic_test"] = test.json()
                score = client.post(f"/api/jobs/{job_id}/analyze")
                score.raise_for_status()
                job = client.get(f"/api/jobs/{job_id}").json()
                # The API may wrap details; keep only an acceptance boolean in report.
                record = job.get("job", job)
                assert record.get("punteggio_ai") is not None, score.json()
                report["codex_frozen_scoring"] = True
                chat = client.post("/api/chat", json={"message": "Reply with a short explanation of the synthetic saved job, using only saved data.", "session_id": "bundle-smoke"})
                chat.raise_for_status()
                assert chat.json().get("answer") and not chat.json().get("degraded", False)
                report["codex_frozen_chat"] = True
                improved = client.post("/api/profile/cv-improve", params={"lang": "en"})
                improved.raise_for_status()
                assert len(improved.json().get("cv_improved", "")) > 50
                report["codex_frozen_cv"] = True
            report["ok"] = True
            client.post("/api/system/shutdown")
        process.wait(timeout=20)
        with sqlite3.connect((data_dir / "searcher.db").as_uri() + "?mode=ro", uri=True) as saved:
            original = saved.execute("SELECT markdown FROM candidate_profiles WHERE id=?", (profile_id,)).fetchone()
            assert original and original[0].startswith("Synthetic candidate.")
        report["profile_preserved"] = True
    finally:
        if process.poll() is None:
            with contextlib.suppress(httpx.HTTPError):
                httpx.post(base + "/api/system/shutdown", timeout=5, trust_env=False)
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    process.terminate()
                process.wait(timeout=10)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
