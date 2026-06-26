# Deploying VF VERITAS to Railway

The app runs as a **single service**: the FastAPI backend serves both the API
(`/api/*`) and the frontend (`analyze.html`, the `.dc.html` prototype, `/data`
images). No separate static host needed.

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
| `OPENROUTER_API_KEY` | your OpenRouter key |
| `GEMINI_OR_MODEL` | `google/gemini-3.1-pro-preview` |
| `SERPAPI_API_KEY` | your SerpAPI key |
| `SERPAPI_COST_PER_SEARCH` | `0` |

With no keys set, the app still boots and runs in mock mode.

## 3. Deploy & open
Railway builds and gives a public URL. The app is at:
- `/` → prototype (redirects to the `.dc.html` app)
- `/analyze.html` → the live analysis page (engine + reference toggles)

`/api/health` should report which engines are live.

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
