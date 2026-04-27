# Database Schema

SQLite single-file DB at `data/searcher.db`. WAL journal mode. Schema evolves through migrations in `app/migrations/`.

## Tables

### `jobs`
One row per scraped offer. De-duplicated by `job_hash`.

| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | autoincrement |
| job_hash | TEXT UNIQUE | hash of url/title+company |
| titolo, azienda, descrizione, sede | TEXT | listing fields |
| fonte, link, ricerca_usata, modalita | TEXT | metadata |
| analysis_json | TEXT | LLM analysis (JSON string) |
| punteggio_ai | INTEGER | 0–10 |
| consiglio | TEXT | apply/evaluate/skip |
| status | TEXT | open / applied / interviewing / rejected / archived |
| is_favorite, is_new | INTEGER | bool flags |
| first_seen_at, last_seen_at, analyzed_at, updated_at | TEXT | ISO timestamps |

### `scan_runs`
Scan lifecycle tracking.

### `candidate_profiles`
Uploaded CVs. `summary_json` holds `skills`, `preferred_roles`, `experience_level`, etc.

### `chat_messages`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| session_id | TEXT | |
| role | TEXT | user / assistant / system |
| content | TEXT | |
| content_type | TEXT | `message` or `summary` (see Fase 6) |
| created_at | TEXT | ISO timestamp |

### `preferences`
Key-value store (TEXT/TEXT). Keys used:
- `active_profile_id`, `ui_language`, `linkedin_url`, `remote_mode`, `min_ral`, `prefer_role_qa|cyber|data`, `last_scan_location`
- `role_shortlist` (JSON string of strings)
- API key slots: `cerebras_api_key`, `groq_api_key`, …

### `job_actions`
Audit trail of state transitions per job.

### `schema_version`
Tracker written by `app/migrations/__init__.py`.

## Migrations

- `001_init.py` — all initial tables.
- `002_chat_message_type.py` — adds `chat_messages.content_type`.

Add new migrations as `NNN_name.py` exposing `VERSION`, `DESCRIPTION`, `upgrade(conn)`. They run in order; baseline detection skips them on pre-existing production DBs.
