# KAIROS — AI incident investigation 

Designed to reduce downtime in complex production systems.
It continuously ingests and understands company-wide context from logs, Jira, Slack, Teams, and codebases.
Using this unified knowledge, it autonomously investigates failures, traces root causes, and provides evidence-backed insights.
Engineers can interact via a chat interface to quickly diagnose issues and take action, including automated PR generation for fixes.


Manual mode: a React chat UI sends natural language issues to a FastAPI backend. The backend retrieves **Qdrant** vectors, then runs a **LangGraph** flow and a **grounded** chat model. If retrieval confidence is below the threshold (or nothing is retrieved), the model is bypassed and the API returns **“Insufficient data”** / **“No sufficient evidence found”** style content.

Auto  Mode  : ingests logs from azure and automatically performs diagnostics to give suggested fixes

## Prerequisites

- Python 3.10+
- Node 18+ (for the frontend)
- Docker (recommended) for Qdrant
- Groq API key (OpenAI-compatible) for chat completions

## 1) Start databases

From the repo root:

```powershell
docker compose up -d
```

This starts:

- Qdrant: `http://localhost:6333`

## 2) Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# Edit .env — set GROQ_API_KEY
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000/docs` for interactive API docs.

### Ingest sample evidence (recommended before investigating)

```powershell
curl -X POST http://localhost:8000/api/ingest -H "Content-Type: application/json" -d "{\"items\":[{\"text\":\"[payments-api] timeout calling auth-service in prod\",\"source\":\"slack\",\"service\":\"payments-api\",\"severity\":\"high\",\"line_start\":1}]}"
```

### Index a codebase folder (optional)

```powershell
curl -X POST "http://localhost:8000/api/ingest/codebase?root=..&service=kairos&max_files=40"
```

## 3) Frontend

```powershell
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. The dev server proxies `/api/*` to `http://127.0.0.1:8000`.

## Core API

| Endpoint | Purpose |
| --- | --- |
| `POST /api/investigate` | Run manual investigation (RAG + grounded LLM) |
| `POST /api/ingest` | Upsert documents into Qdrant |
| `POST /api/ingest/codebase` | Chunk code files into Qdrant |
| `POST /api/webhook/slack` | Slack events / generic message ingestion |
| `POST /api/webhook/teams` | Microsoft Teams payload ingestion |
| `POST /api/webhook/jira` | Jira webhook ingestion |
| `POST /api/create-pr` | Create branch + commit Markdown + open GitHub PR |

Reports are also written under `backend/reports/` as `.md` files on each `/investigate` call.

## Guardrails

- The chat model receives **only** retrieved vector chunks.
- If **no** vector hits or **confidence &lt; `CONFIDENCE_THRESHOLD`**, the backend returns a fixed **Insufficient data** Markdown report (LLM generation is skipped).
- System prompt enforces citations like `[SOURCE line N]` mapped to chunk line metadata.

## GitHub PR automation (`/api/create-pr`)

Set in `backend/.env`:

- `GITHUB_TOKEN` (classic PAT with `repo`)
- `GITHUB_OWNER`, `GITHUB_REPO`
- `GITHUB_DEFAULT_BRANCH` (default `main`)

The React UI exposes **Review & Raise PR** (opens the PR in a new tab when successful).

---

## API key setup (external systems)

### Slack

1. Go to [Slack API apps](https://api.slack.com/apps).
2. Create an app → **Incoming Webhooks** (and/or **Event Subscriptions**).
3. Set the **Request URL** to your public URL: `https://<host>/api/webhook/slack`.
4. For signed events, set `SLACK_SIGNING_SECRET` in `backend/.env`.

### Microsoft Teams

- Use an **Incoming Webhook** connector or **Azure Bot**; point the webhook URL to `https://<host>/api/webhook/teams`.
- The MVP handler accepts JSON with a top-level `text` field (adapt mapping to your connector payload as needed).

### Jira

1. Jira **Settings → System → Webhooks** → add URL `https://<host>/api/webhook/jira`.
2. Optionally restrict by project/issue events.
3. For REST/API scripts, create an Atlassian API token from your Atlassian account (not required for the webhook ingest path).

---
