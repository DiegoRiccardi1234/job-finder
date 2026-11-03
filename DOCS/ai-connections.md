# AI connections and subscription-backed runtimes

Available from **v2.5.0**. Subscription connections and MCP setup are optional,
per-installation choices. New users select their own provider or runtime;
updates do not enable subscriptions, import someone else's configuration or
register a global MCP server automatically.

Job Finder can keep using its existing API providers or route an individual
task to Codex, Claude Code, or a named OpenAI-compatible endpoint. Chat,
scoring, and CV/generation have independent primary connections and ordered
fallbacks. Existing installations keep their previous routing until the user
chooses a connection in **Settings → AI → AI connections and assistants**.

## Subscription clients

Install and sign into the official client using its own authentication flow.
Job Finder detects the client and runs a dedicated, tool-free background
process. A terminal need not remain open. Credentials stay with the native
client; Job Finder neither copies tokens nor controls desktop chat windows.
An installed client or cached account plan does not prove current model access.
The explicit synthetic test verifies a completion on the selected connection.

For a new installation, first install/sign into the official runtime with your
own account, enable its connection in Job Finder, and explicitly save it as the
primary connection for the tasks you want. Leave the other tasks on their
existing API settings if preferred. Signing into a runtime does not configure
MCP, and adding MCP does not change Job Finder's AI routing.

- Codex uses App Server over stdio, not the shared development daemon.
- Claude Code uses its programmatic mode. An eligible current Claude Code
  subscription is required; an ordinary free Claude account is insufficient.
- Runtime diagnostics do not infer a remaining quota from token counts. Codex
  account windows are displayed when supplied. Claude quota may be unavailable.
  The internal daily API request guard is a separate limit.
- Processes use neutral working directories and disable tools, hooks, external
  MCP connections and unnecessary context. No API credentials are passed to a
  subscription runtime as a fallback.

Opening Settings or checking status does not perform an inference. **Test**
sends one small synthetic JSON request and consumes the selected account's
allowance. No fallback is used by this test. During normal tasks, only the
fallbacks explicitly saved for that task may be used, in their displayed order.

## Compatible endpoints, including Hermes

Create a named compatible connection with base URL, model and optional key.
Multiple connections can coexist. HTTP is accepted only on loopback; remote
endpoints require HTTPS. Keys are stored separately from connection metadata
and are never returned by the settings API. Omitting a key during an edit
preserves it; clearing it is explicit. Existing Custom settings remain usable.

For an already configured Hermes gateway, use its local OpenAI-compatible API
endpoint and configured model. Hermes still needs its own upstream provider;
running a gateway locally does not make inference free. Job Finder does not
install, start, stop, or update that gateway. DeepSeek remains available through
the existing API provider.

## MCP and desktop assistants

MCP is the reverse direction: an assistant uses Job Finder's functions and data.
See [jobfinder-mcp.md](jobfinder-mcp.md) for connection instructions and tools.
Codex desktop/CLI, Claude Desktop and Cowork use their appropriate local client
configuration. Configurations are generated for review; the app does not edit
global assistant settings. Cowork needs a local MCP plugin rather than a remote
connector pointed at localhost.

To make Job Finder available to **your Codex CLI in every working folder**, add
the generated local MCP entry to your user-level Codex configuration and start
a new CLI session. You do not need to launch the CLI from the Job Finder folder.
The registration applies only to that user/configuration; other users set up
their own endpoint and private token. Keep Job Finder open while using any MCP
client. A generated client configuration is not a declaration that all desktop
applications have been live-tested.

MCP searches collect and deduplicate without asking another model. An assistant
can obtain the current analysis prompt/schema, evaluate offers in its existing
conversation, and submit a complete batch. The app validates the current
profile and offer inputs, enforces hard requirements and records provenance.
Profile, preferences and application-state changes remain proposals until the
user confirms them in Job Finder. No MCP tool submits applications or emails.

## Local data and compatibility

The Windows package separates program components from private user state:

```text
JobFinder/
  JobFinder.exe              launcher: the file to open
  LEGGIMI.txt
  app/
    JobFinder.exe            application started by the launcher
    JobFinderMCP.exe         local MCP adapter, started by the assistant
    Updater.exe              update helper, started by the application
    _internal/               bundled Python code, web assets and libraries
    vendor/                  bundled OCR tools and language resources
    .jobfinder-layout.json   layout marker, version 1
  state/                     private user data
```

There are no junctions between these directories. The external package root
remains the workspace; packaged resources stay in `app/_internal`. The marker
inside `app` identifies this layout. Do not remove individual program files,
rename the directories or edit the marker. Only the root `JobFinder.exe` is a
normal user entry point; auxiliary executables require their own arguments.

- `state/searcher.db`: offers, profiles, chats, history and pending operations.
- `state/ai_connections.json`: named connections and explicit task routing.
- `state/ai_connection_secrets.json`: compatible connection keys; private.
- `state/agent/mcp-token.txt`: local MCP capability; private and generated by
  explicit setup, never embedded in copied command-line arguments.
- Other local credentials, settings and logs also belong to `state/`.
- Migration 031 adds `agent_operations` for durable operations, analysis tickets
  and pending proposals. Existing job and candidate tables are preserved.

Development workspaces and legacy installations may still use `data/` instead
of `state/`; that location remains supported for compatibility. Do not move a
legacy archive by hand. The generated MCP configuration uses the actual data
location, not a path guessed from this example.

For a simple consistent backup, exit Job Finder completely using its Quit
control and copy the entire `state/` directory to a separate location. An
active SQLite database requires a consistent SQLite backup;
copying only a live `searcher.db` is not a complete backup. Keep backups private:
they can contain CVs, mail-account credentials and connection keys. Preserve
`state/` across program updates. Root `LEGGIMI.txt` explains normal startup.

Switch a task back to **Current API settings** to restore its legacy routing.
Connections referenced by a route must be removed from that route before they
can be deleted. An unavailable connection leaves clear errors or existing
labelled local fallbacks; it does not silently enable another paid provider.

## Verification boundaries

Runtime protocols are tested with actual subprocesses and synthetic messages.
Automated app tests use isolated workspaces, fake boards and no outbound AI.
Browser checks cover configuration, ordered fallbacks, human approvals, mobile
layout, and all five UI languages. A live native completion proves access only
for that account, selected model and request, not unlimited quota or every model.

Official references: [Codex App Server](https://learn.chatgpt.com/docs/app-server),
[Claude programmatic mode](https://code.claude.com/docs/en/headless),
[Claude plan usage](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan),
[Hermes API server](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/api-server.md).
