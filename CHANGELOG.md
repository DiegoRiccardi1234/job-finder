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
