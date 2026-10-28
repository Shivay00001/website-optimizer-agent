# AI Website Optimizer Agent

AI-powered Technical SEO & CRO audit service. Submit a website URL, pick an LLM
(OpenAI, Anthropic, Gemini, Zhipu GLM, or local Ollama), and get a scored audit
report with critical issues and recommendations. Built as a sellable **audit + fix
service** (₹5–10k per audit+fix, upsell monthly care plan) for SMBs with broken/slow sites.

- **Backend:** FastAPI + LiteLLM (multi-LLM routing), SQLite/PostgreSQL via SQLAlchemy
- **Frontend:** Next.js + Tailwind dashboard
- **API:** OpenAPI docs at `/docs` (served by the backend)

## Quick start (local)

### 1. Backend

```bash
cd backend
cp .env.example .env          # fill in API_KEY + at least one LLM provider key
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn server:app --port 8010
```

Check it boots: `curl http://localhost:8010/health` → `{"status":"ok",...}` (200).

### 2. Frontend

```bash
cd frontend
cp .env.example .env           # set NEXT_PUBLIC_API_URL to your backend URL
npm install
npm run dev                    # http://localhost:3000
```

Enter the **Backend API Key** (must match the backend's `API_KEY`), optionally add
LLM provider keys (kept in browser localStorage, never stored server-side), enter a
target URL, and run the audit.

## Environment variables

All configuration is via env vars — no hardcoded secrets, keys, or URLs anywhere.
Copy `backend/.env.example` → `backend/.env` and `frontend/.env.example` → `frontend/.env`.

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_KEY` | **Yes** | — | Shared secret; clients send it as the `X-API-Key` header on every non-public endpoint. Generate: `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `OPENAI_API_KEY` | One provider key | — | Used for `gpt-*` models (also accepted per-request via `X-OpenAI-Key`) |
| `ANTHROPIC_API_KEY` | One provider key | — | Used for `claude-*` models (per-request: `X-Anthropic-Key`) |
| `GEMINI_API_KEY` | One provider key | — | Used for `gemini/*` models (per-request: `X-Gemini-Key`) |
| `ZHIPUAI_API_KEY` | One provider key | — | Used for `zhipu/*` models (per-request: `X-GLM-Key`) |
| `OLLAMA_BASE_URL` | For local models | `http://localhost:11434` | Ollama server URL for `ollama/*` models |
| `CORS_ORIGINS` | No | `http://localhost:3000` | Comma-separated browser origins allowed (no wildcards) |
| `DATABASE_URL` | No | `sqlite+aiosqlite:///./optimizer.db` | e.g. `postgresql+asyncpg://user:pass@host/db` in production |
| `PORT` | No | `8010` | Server port |
| `MAX_HTML_CHARS` | No | `15000` | HTML chars sent to the LLM per audit |
| `FETCH_TIMEOUT_SECONDS` | No | `15` | Website fetch timeout |
| `LITELLM_MAX_TOKENS` | No | `2000` | Max tokens for the audit response |
| `NEXT_PUBLIC_API_URL` | Frontend | `http://localhost:8010` | Backend base URL for the Next.js UI |

## API

Auth: `X-API-Key: <API_KEY>` on all endpoints except `GET /health`.

| Method | Endpoint | Description |
|---|---|---|
| GET | `/health` | Public. Returns 200 `{"status":"ok",...}` |
| POST | `/api/execute` | Submit audit → `202 {"status":"accepted","task_id":"..."}`. Body: `{"target_url":"https://…","model_id":"gpt-4o"}`. `target_url` must be a public http(s) URL (SSRF guard blocks private/loopback hosts); `model_id` must start with `gpt-`, `claude-`, `gemini/`, `zhipu/`, or `ollama/`. |
| GET | `/api/tasks/{task_id}` | Poll status: `pending` → `running` → `success`/`error`, plus `report` when done. |

All errors are structured JSON (`{"code": "...", "message": "..."}`); stack traces
never leave the server. Interactive docs: `/docs`.

## Deploy on Render (free tier)

**Backend (Web Service):**
1. Push this repo, create a Render **Web Service** from it.
2. Build command: `pip install -r backend/requirements.txt`
3. Start command: `uvicorn server:app --host 0.0.0.0 --port $PORT --app-dir backend`
   (or deploy the included `Dockerfile`)
4. Set env vars in the Render dashboard: `API_KEY` (generate a strong one),
   one provider key (e.g. `OPENAI_API_KEY`), `CORS_ORIGINS` = your frontend URL,
   `DATABASE_URL` = a Render PostgreSQL `postgresql+asyncpg://…` URL for persistence
   (SQLite works but is ephemeral on Render).
5. Health check path: `/health`.

**Frontend (Static Site):** create a Render **Static Site** from `frontend/`,
build `npm run build`, and set `NEXT_PUBLIC_API_URL` to the backend's Render URL.

## Smoke test

```bash
cd backend
API_KEY=test-key DATABASE_URL=sqlite+aiosqlite:////tmp/smoke.db python smoke_test.py
```

Runs 15 checks against the real app: health, auth (missing/wrong key → 401),
input validation (bad URL / model / loopback target → 422 structured JSON),
submit → 202 + task_id, unknown task → 404, no stack-trace leakage, no wildcard CORS.

## Security notes

- Never commit `.env` or real keys; only `.env.example` (dummy values) is tracked.
- LLM keys are read from server env or per-request headers — never stored in the DB.
- CORS is an explicit allowlist; `allow_credentials` is never combined with `*`.
- The audit target is SSRF-guarded (private/loopback hosts rejected).
- `DISABLE_SSRF_GUARD=1` exists for sandboxed test environments only — never set it in production.

## Built by VisionQuantech

Part of the [VisionQuantech](https://visionquantech.com/) AI automation studio. See [AI automation services](https://visionquantech.com/ai-automation-services) and the guide to [AI receptionist for dental clinics](https://visionquantech.com/blog/ai-receptionist-for-dental-clinics).
