# Deploying VF VERITAS to Railway

The app runs as a **single service**: the FastAPI backend serves both the API
(`/api/*`) and the frontend — the analysis app at the **root URL**, plus
`/data` reference images. No separate static host needed.

## 1. Connect the repo
1. Railway → **New Project → Deploy from GitHub repo** → pick
   `trueinf/veritas_updated`.
2. Railway auto-detects Python (Nixpacks) and uses `railway.json`'s start
   command: `uvicorn app:app --app-dir backend --host 0.0.0.0 --port $PORT`.

## 2. Set environment variables (Service → Variables)
Copy these from your local `backend/.env` — **never commit them**:

| Variable | Value |
|---|---|
| `OPENAI_API_KEY` | your OpenAI key |
| `OPENAI_MODEL` | `gpt-5.5` |
| `SERPAPI_API_KEY` | your SerpAPI key |
| `SERPAPI_COST_PER_SEARCH` | `0` |
| `SUPABASE_URL` | `https://xxxx.supabase.co` |
| `SUPABASE_SERVICE_KEY` | your Supabase service_role key |
| `SUPABASE_BUCKET` | `veritas-products` |

The product catalog (add / select products) needs the three `SUPABASE_*`
vars. It creates one dedicated bucket and never touches existing data.

With no keys set, the app still boots and runs in mock mode.

## 3. Deploy & open
Railway builds and gives a public URL — **the root URL is the app** (engine +
reference toggles, multi-image upload, UPC extraction, run report).
`/api/health` reports which engines are live.

## Notes
- The frontend calls the API on the **same origin** in production, and falls
  back to `http://127.0.0.1:8000` only when opened from the local `:8753` dev
  server — so local two-server dev and the single-service deploy both work.
- `data/` (authentic reference images) ships with the repo and is required.
- Outbound internet is needed (OpenAI / OpenRouter / SerpAPI / catbox / unpkg).

## Case queue (RabbitMQ)

The "Case Queue" from the architecture diagram, wired into both entry points:

- **The UI (existing workflow):** `/api/analyze` publishes the case to the
  durable `case-queue`; `backend/worker.py` — a separate process — runs it and
  sends the outcome back over `case-queue-results`, which `/api/job/{id}`
  serves to the UI's polling. Frontend unchanged; the UI still persists via
  `/api/export/save`. If the broker is unreachable, `/api/analyze` silently
  falls back to the original inline background thread, so a laptop without
  Docker running behaves exactly as before. `USE_QUEUE=0` disables the queue
  path. Caution: a reachable broker with **no worker running** means queued
  jobs sit forever — run the worker whenever the broker is up.
- **External producers (UiPath later):** POST the same payload to
  `/api/enqueue`; the worker persists the outcome itself (visible at
  `/api/cases`).

A case that fails twice is parked on `case-queue-failed` instead of retrying
forever. `/api/queue/status` reports queue depth.

**Broker.** Local dev:

```
docker run -d --name rabbitmq -p 5672:5672 -p 15672:15672 rabbitmq:3-management
```

Management UI at http://localhost:15672 (guest/guest). Production: a managed
broker such as CloudAMQP (free tier is plenty) — copy its `amqps://…` URL.

**Env vars** (both the API service and the worker service):

| Variable | Value |
|---|---|
| `RABBITMQ_URL` | `amqp://guest:guest@localhost:5672/%2F` locally; the CloudAMQP URL in prod |
| `CASE_QUEUE` | optional, defaults to `case-queue` |

**Worker.** Run alongside the API — locally `python worker.py` from
`backend/`; on Railway add a **second service** on the same repo and point its
**Settings → Config-as-code** at `railway.worker.json` (a dashboard start
command is NOT enough: `railway.json`'s `startCommand` is config-as-code and
overrides it, so the worker would boot as a second uvicorn). Give it the same
variables as the API. Scale by running more worker instances; RabbitMQ
load-balances one case per worker.

**Smoke test:**

```
curl -X POST localhost:8000/api/enqueue -H "Content-Type: application/json" \
     -d "{\"case_id\":\"CM-TEST-1\",\"brand\":\"TNF\"}"
```

then watch the worker log pick it up, and `GET /api/queue/status` drain to 0.

## Local dev (unchanged)
```
cd backend && python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app:app --port 8000        # backend + can also serve the frontend at /
# optional separate static server:
python -m http.server 8753         # from repo root
```
