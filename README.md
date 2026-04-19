# Job Finder — AI-Powered Job Search Assistant

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![AI Powered](https://img.shields.io/badge/AI-Multi--LLM-blueviolet)](#supported-llm-providers)

> A localhost-first AI-powered job search assistant that runs on your machine and helps you scrape LinkedIn & Indeed, analyze offers against your CV, and plan applications from one dashboard.

---

## Features

- **Smart CV Analysis** — Upload your CV (PDF, DOCX, TXT) and the AI extracts skills, experience level, and ideal roles
- **AI Career Coach** — Conversational chatbot that learns your preferences, suggests search terms, and recommends which jobs to apply for, with autonomous form-filling
- **Model Selection & Tag-Based Search** — Pick your preferred LLM provider directly from the UI and search precisely using dynamic tags
- **Multi-Source Scan** — Scrapes LinkedIn and Indeed simultaneously with real-time SSE progress
- **Personalized Rating** — Each job gets a 1-10 AI score based on your profile, with pros/cons and actionable advice
- **Kanban Board** — Track jobs across columns: Open → Applied → Interviewing → Rejected
- **Cover Letter Generator** — One-click AI-generated cover letters tailored to each job + your CV
- **Analytics Dashboard** — Track application stats, score distributions, and scan history
- **Dark Mode** — Full dark/light theme with glassmorphism UI
- **Multi-LLM Support** — Works with OpenRouter, Groq, Anthropic (Claude 3.7 Sonnet), OpenAI (GPT-4.5 / 4o), Google (Gemini 1.5 Pro) and Cerebras.

## Media

> **Demo Video**
> 
> *A brief demo video/GIF showcasing Tag Search, Kanban workflow, and AI Chatbot autocomplete will be available here soon.*

**Dashboard — Job Table & Recommendations**

![Dashboard](screenshots/readme/dashboard-recommendations-en.png)

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.11+, FastAPI, SQLite |
| Frontend | Vanilla JS, CSS3 (Glassmorphism) |
| AI/LLM | Multi-provider (OpenRouter, Groq, OpenAI, Anthropic, Google, Cerebras) |
| Scraping | [python-jobspy](https://github.com/Bunsly/JobSpy) (LinkedIn, Indeed) |
| Streaming | Server-Sent Events (SSE) |
| Testing | Playwright (E2E), pytest |

## Quick Start

### Prerequisites

- Python 3.11+
- At least one LLM API key (Cerebras, Groq, OpenAI, Anthropic, or Google)
- Node.js (optional, for E2E tests only)

### Installation

```bash
# Clone the repository
git clone https://github.com/YOUR_USERNAME/job-finder.git
cd job-finder

# Create and activate virtual environment
python -m venv .venv

# Windows
.\.venv\Scripts\Activate.ps1

# macOS/Linux
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Run

```bash
python run_webapp.py
```

Open **http://127.0.0.1:8000** in your browser.

## Localhost Mode (Not Offline)

`Job Finder` runs locally on localhost, but it is not an offline app.

| Works locally without internet | Requires internet |
|-------|-----------|
| UI navigation and filters | Job scraping from LinkedIn/Indeed |
| Local SQLite data (`data/searcher.db`) | LLM chat/coaching |
| Existing jobs/recommendations already in DB | AI scoring for newly scanned jobs |
| Manual job actions and status changes | Cover letter generation |
| CSV export | Provider health checks against external APIs |

If internet is unavailable, you can still open and use the local dashboard, but online features will fail gracefully until connectivity is restored.

### First-Time Setup

1. Go to **Settings** and enter your LLM API key
2. Upload your CV (PDF, DOCX, or TXT)
3. Chat with the AI Coach — it will ask about your preferences
4. Run a job scan from Settings or let the chatbot suggest search terms
5. Review results on the Dashboard and move jobs through Kanban actions

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│  Browser UI  │────▶│  FastAPI API  │────▶│  SQLite DB  │
│  (Vanilla JS)│◀────│  + SSE Stream │◀────│  (Local)    │
└─────────────┘     └──────┬───────┘     └─────────────┘
                           │
                    ┌──────▼───────┐
                    │  LLM Provider │
                    │  (Multi-API)  │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │  Job Scraper  │
                    │  (JobSpy)     │
                    └──────────────┘
```

## Supported LLM Providers

| Provider | Selectable Models / Notes |
|----------|---------------------------------------------------------|
| **OpenRouter** | Access almost any standard model (Claude 3.7, GPT-4.5, Llama 3) |
| **Cerebras** | Llama 3.3 70B (Fast inference, free tier available) |
| **Groq** | Llama / Mixtral (Very fast, generous free tier) |
| **OpenAI** | GPT-4o, GPT-4.5, o1, o3-mini |
| **Anthropic** | Claude 3.7 Sonnet, Claude 3.5 Haiku |
| **Google** | Gemini 1.5 Pro, Gemini 2.0 Flash |

Configure your preferred provider in Settings. The app automatically falls back to the next available provider if one fails or hits rate-limits.

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/health` | Health check + provider status |
| POST | `/api/upload-cv` | Upload CV for AI analysis |
| GET | `/api/profile` | Get active candidate profile |
| GET | `/api/scan/stream` | SSE streaming job scan |
| GET | `/api/jobs` | List jobs with filters |
| GET | `/api/jobs/{id}` | Job detail with AI analysis |
| POST | `/api/jobs/{id}/cover-letter` | Generate cover letter |
| POST | `/api/jobs/{id}/action` | Set job status (apply/skip) |
| POST | `/api/chat` | Chat with AI Career Coach |
| GET | `/api/analytics` | Dashboard statistics |
| GET | `/api/recommendations` | AI-recommended top jobs |

## E2E Tests

```bash
npm install
npx playwright install chromium
npm run test:e2e
```

## Local Data

All data is stored locally in `data/searcher.db` (SQLite). Back up the `data/` folder before major updates.

---

# Italiano

## Avvio Rapido

```bash
# Clona il repository
git clone https://github.com/YOUR_USERNAME/job-finder.git
cd job-finder

# Crea e attiva l'ambiente virtuale
python -m venv .venv
.\.venv\Scripts\Activate.ps1  # Windows

# Installa le dipendenze
pip install -r requirements.txt

# Avvia l'app
python run_webapp.py
```

Apri **http://127.0.0.1:8000** nel browser.

### Primo Utilizzo

1. Vai in **Settings** e inserisci la tua API key LLM
2. Carica il tuo CV (PDF, DOCX o TXT)
3. Parla con l'AI Coach — ti fara domande sulle tue preferenze
4. Lancia una scansione da Settings o lascia che il chatbot suggerisca i termini di ricerca
5. Consulta i risultati nella Dashboard, trascina i lavori nella board Kanban

## Licenza / License

MIT — See [LICENSE](LICENSE) for details.
