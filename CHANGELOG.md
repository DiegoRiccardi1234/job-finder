# Changelog

## [Unreleased] — 2026-04

### Added
- **Unit test suite** (`tests/unit/`): coverage for chat context, handler parsing, fallback, CV ingest, scanner helpers, role shortlist, migrations, memory summarizer, provider retry, rate limiter, scraper canary.
- **Schema migrations** (`app/migrations/`): lightweight `schema_version`-tracked migrations, baseline detection for pre-existing DBs. 001 init schema, 002 `chat_messages.content_type`.
- **Role shortlist service** (`app/services/roles_shortlist.py`): dedicated module + `/api/roles/shortlist` GET/POST/DELETE. Dedup case-insensitive.
- **Career Coach UX**:
  - CV-derived + localized quick prompts (`/api/chat/prompts?lang=`).
  - Markdown rendering in coach bubbles (**bold** role names, *italic* hints, `code`, `-` bullet lists).
  - Role pills: clickable suggestions that add keywords to Step 2 without launching the search.
  - `suggested_roles` field in chat JSON envelope.
  - Conversation summarizer: condenses sessions >20 messages into a summary memory row.
- **No-API-key banner**: sticky warning when zero providers are configured.
- **Radar chart** (Chart.js) in job detail: skills / seniority / remote / salary / contract axes. Backend `match_axes` in `analyze_offer`.
- **Analytics**: `top_companies` widget with horizontal bar chart.
- **Export applications**: `GET /api/applications/export?format=csv|json` with tracking-relevant columns.
- **Onboarding wizard**: 3-step welcome overlay, localized in 5 languages, surfaces only when no CV loaded.
- **Fluid layout**: clamp-based typography + grid columns; design now scales with viewport without fixed breakpoints below 960px.
- **i18n**: `coach.expand/collapse/savedToShortlist`, `onboarding.*`, `banner.*`, `analytics.topCompanies`, `offcanvas.breakdown` + axis labels across en/it/es/fr/de.

### Changed
- **Provider calls** retry on 429/5xx/timeout with exponential backoff + jitter (`LLM_MAX_RETRIES`, `LLM_RETRY_BASE_SECONDS` env).
- **Rate limiter** (`app/rate_limit.py`): in-process sliding window on `/api/chat` (20/min), `/api/scan` (5/min), `/api/upload-cv` (10/min). Toggle with `ENABLE_RATE_LIMIT`.
- **Scraper pacing**: random 0.8–2.4s sleep between terms; `canary_warning` SSE event when a common keyword returns zero results.
- **Frontend**: `app.js` entry is now an ES module; shared helpers extracted to `web/modules/helpers.js`, `shortlist.js`, `theme.js`. Chat styles moved to `web/styles/chat.css`.
- **Chat JSON envelope**: clarified formatting rules (markdown markers) and documented `suggested_roles` shape.
- **Prompts**: `advising.txt` / `onboarding.txt` include a "Role exploration" section guiding CV-aware pivots.
- **E2E**: `chat-role-guidance` and `live-cv-chat-search` now skip by default; opt in with `RUN_LIVE_LLM=1`. New `chat-live-smoke.spec.js` + `live-smoke.yml` manual workflow.

### Removed
- Chat expand/collapse toggle (layout is now fully fluid).

### Tooling & Quality
- **Toolchain**: `pyproject.toml` consolidates ruff, mypy strict, pytest, and coverage config. `.pre-commit-config.yaml` adds whitespace, ruff (lint + format), and mypy hooks; `pytest.ini` removed.
- **Mypy strict**: full pass on `app/`. New `Callable[[], _RetryT] -> _RetryT` generic on `_with_retry`, `cast()` wrapping for SDK and `json.loads` Any leakage, typed lifespan/SSE generators in `main.py`.
- **Coverage**: CI runs `pytest --cov=app --cov-report=xml`, `scripts/coverage_badge.py` generates `coverage.json` for a self-hosted shields.io endpoint badge (no Codecov account required).
- **Docker**: multi-stage `Dockerfile` (deps → runtime), `docker-compose.yml` with healthcheck and persistent `./data` volume, `.dockerignore`, `.env.example` documenting every env var. `app/config.py` learned to read `.env` without adding a dependency.
- **Repo hygiene**: `.gitattributes` enforces LF line endings; extended `.gitignore` for `.env`, `.mypy_cache/`, `.ruff_cache/`, `coverage.xml`, `dist/`, `build/`.

### Bug fixes
- **CV upload preference key mismatch** (`/api/upload-cv`): the handler checked `summary.get("ruoli_preferiti")` but both the LLM prompt and the heuristic returned `preferred_roles`, so the per-user roles preference was never persisted on upload. Now reads and writes `preferred_roles`.
- **CV content validation**: `validate_cv_content()` rejects uploads under 200 chars or missing common CV keywords (HTTP 422), preventing junk PDFs from polluting the profile store.
- **CV upload deduplication**: migration `003_candidate_profile_hash.py` adds `content_hash` + index; re-uploading the same file now returns the existing `profile_id` instead of creating a duplicate row.

### Refactor
- `web/app.js`: extracted i18n into `web/modules/i18n.js` with a `onLanguageChange` callback registry, dropping ~80 LOC from the main entry. Chat / scan / kanban / recommendations splits remain on the follow-up list.

### Docs
- README slimmed to 4 demo screenshots, accurate test count (99) and i18n key count (204), refreshed Project structure tree, new Rate limiting and Database migrations sections, expanded Mermaid architecture diagram (rate_limit, migrations, roles_shortlist, chat memory).
- `CONTRIBUTING.md`, `SECURITY.md`, `Makefile` added; `DOCS/schema.md` and `DOCS/security.md` tracked and linked from README.
- New `tests/e2e/readme-demo-gif.spec.js` records an animated hero GIF via Playwright + ffmpeg (run via `npm run record-demo`).
- Empty `tests/e2e/screenshots.spec.js` deleted; `readme-cv-showcase.spec.js` renamed to `manual-cv-flow.spec.js` and restricted to the Italian CV.

### Standalone Windows bundle
- `scripts/launch_exe.py`: PyInstaller entry point. Resolves a writable workspace next to the executable, sets `JOBFINDER_WORKSPACE` before importing `app.main`, opens the default browser when uvicorn is ready, runs without `reload`.
- `JobFinder.spec`: PyInstaller config with two analyses (`launch_exe` → `JobFinder.exe`, `updater` → `Updater.exe`) merged via `MERGE` so dependencies are stored once. Hidden imports cover `pkgutil`-discovered submodules (`app.migrations`, `app.providers`) and the LLM SDKs imported lazily inside try/except.
- `scripts/build_exe.py` + `make build-exe`: idempotent local build that wipes `build/`, runs PyInstaller, and zips `dist/JobFinder/` into `dist/JobFinder-windows.zip`.
- **Banner signup links**: the no-API-key sticky banner now renders three CTAs (Cerebras free key, Groq key, Open Settings) so a non-developer can register in 30 s without reading docs. 4 new i18n keys × 5 locales (`banner.signupHint`, `signupCerebras`, `signupGroq`, `openSettings`).
- **README "For non-developers (Windows)"**: 5-step download → extract → run → register → paste-key flow, plus SmartScreen workaround. New shields.io release badge linking to the latest GitHub release.
- **CI release workflow** (`.github/workflows/release.yml`): on tag `v*` push, runs `python scripts/build_exe.py` on `windows-latest` and uploads `JobFinder-windows.zip` as a release asset (auto-generated notes). `workflow_dispatch` trigger uploads it as an artifact instead, for dry runs.

### Self-update (standalone bundle)
- `app/update_sync.py`: `sync_install_dir(source, target)` copies a freshly-extracted bundle over the install dir, skipping any path whose first component is `data`, `.env`, or `.env.local`. User DB, secrets, settings, and logs are guaranteed to survive every update.
- `scripts/updater.py` (bundled as `Updater.exe`): waits for the parent JobFinder PID to exit (Windows `OpenProcess` / POSIX `os.kill(pid, 0)`), downloads the latest `*windows.zip` asset from GitHub Releases, extracts to a temp dir, runs the sync, restarts JobFinder.exe. Every step logged to `data/logs/updater.log`. Failures leave the install dir untouched.
- `POST /api/update/start` (`app/main.py`): refuses with 409 in dev mode or when already on latest, refuses with 500 if `Updater.exe` is missing. On success, spawns the updater detached and schedules `os._exit(0)` 0.8 s later so the response flushes and files unlock.
- `app/version.py:get_version_info` reports `frozen: bool` so the frontend picks the right update flow.
- Frontend update banner branches on `info.frozen`: bundle users see a progress modal that polls `/api/health` every 2 s, detects the file-replacement outage window, and auto-reloads the page when the new process answers. Dev users keep the existing `git pull && pip install` flow.
- 6 new unit tests (`tests/unit/test_update_sync.py`) cover: data dir survives, app/ files are replaced, brand new files land, `.env` stays put, source `data/` subtree is ignored, missing source raises.
