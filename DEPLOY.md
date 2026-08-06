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

## Local dev (unchanged)
```
cd backend && python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app:app --port 8000        # backend + can also serve the frontend at /
# optional separate static server:
python -m http.server 8753         # from repo root
```
