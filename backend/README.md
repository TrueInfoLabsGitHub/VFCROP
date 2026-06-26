# VF VERITAS — Analysis Backend (Phase 1)

LangGraph agent orchestration for real counterfeit analysis. The 5 forensic
dimension agents (Logo, Stitching, Hardware, Label, Material) + a UPC/tag tool
run **in parallel** on Gemini, fan in to a composite score, then an OpenAI
verdict tier synthesizes and adversarially verifies the result. Every run emits
a **Run Report**: per-agent token usage, cost, latency, and eval signals.

Runs in **mock mode with no API keys** so you can see the whole flow today;
set keys to go live.

```
intake → [ Logo · Stitching · Hardware · Label · Material · UPC ] → aggregate
       → verdict (synthesize + verify) → report → END
            Gemini 3 Pro (perception)        OpenAI GPT-5.5 (verdict)
```

## Run

```sh
cd backend
python -m venv .venv
.venv\Scripts\activate            # Windows  (source .venv/bin/activate on macOS/Linux)
pip install -r requirements.txt
uvicorn app:app --port 8000
```

Then serve the project root (for the frontend) from the repo root:

```sh
python -m http.server 8753
```

Open **http://127.0.0.1:8753/analyze.html** → pick a case → *Run AI analysis*.

## Go live

Mock mode needs nothing. To use real models, set keys (see `.env.example`):

```sh
set GEMINI_API_KEY=...      # Windows  (export on macOS/Linux)
set OPENAI_API_KEY=...
```

The backend reads them at startup; `/api/health` shows `live`/`mock` per
provider. If a live call fails it falls back to mock for that node, so the run
never hard-fails. Model IDs are configurable (`GEMINI_MODEL`, `OPENAI_MODEL`) —
update them to whatever your account has access to.

## API

- `GET  /api/health` → `{ ok, mode }`
- `POST /api/analyze` → `{ case_id, brand, suspect_image? (base64) }`
  returns `composite`, `dimensions[]`, `upc`, `verdict`, `report`, `references`.

## Files

| File | Role |
|---|---|
| `graph.py` | LangGraph StateGraph — fan-out/fan-in, aggregate, verdict, report nodes |
| `providers.py` | Gemini + OpenAI calls with usage capture; deterministic mock fallback |
| `pricing.py` | Per-model price table → cost calculator |
| `references.py` | Brand → authentic `data/` images, mapped per dimension |
| `app.py` | FastAPI entrypoint + CORS |

## Not yet (next phases)

Streaming per-dimension to the gauge, human-in-the-loop override via LangGraph
`interrupt()`, Casemates/audit persistence, LangSmith tracing + an Evals
dashboard, and wiring into the main `.dc.html` tabs (this page is the
standalone Phase-1 surface).
