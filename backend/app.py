"""FastAPI entrypoint for the VERITAS analysis backend.

POST /api/analyze runs the LangGraph orchestration and returns the structured
dimension results, composite verdict, UPC result, and the per-run Run Report
(token usage, cost per agent, eval signals). Mock mode runs with no API keys;
set GEMINI_API_KEY / OPENAI_API_KEY to go live.

Run:  uvicorn app:app --port 8000   (from the backend/ directory)
"""
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from graph import DIMENSIONS, build_graph
from providers import mode

app = FastAPI(title="VF VERITAS Analysis API")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

GRAPH = build_graph()


class AnalyzeReq(BaseModel):
    case_id: str
    brand: str = "TNF"
    provider: str = "openai"                  # "openai" (GPT-5.5) | "gemini" (via OpenRouter)
    reference_source: str = "local"           # "local" (data/) | "google" (reverse-image)
    suspect_image: str | None = None         # single product photo (back-compat)
    suspect_images: list[str] | None = None  # multiple product photos (preferred)
    upc_image: str | None = None             # barcode/UPC photo, drives the UPC OCR node


@app.get("/api/health")
def health():
    return {"ok": True, "mode": mode()}


@app.post("/api/analyze")
def analyze(req: AnalyzeReq):
    imgs = req.suspect_images if req.suspect_images else ([req.suspect_image] if req.suspect_image else [])
    provider = "gemini" if req.provider == "gemini" else "openai"
    ref_source = "google" if req.reference_source == "google" else "local"
    state = {"case_id": req.case_id, "brand": req.brand, "provider": provider,
             "ref_source": ref_source,
             "suspect_images": [b for b in imgs if b],
             "upc_image": req.upc_image or ""}
    out = GRAPH.invoke(state)
    dims = sorted(out["dimension_results"],
                  key=lambda d: DIMENSIONS.index(d["dimension"]))
    return {
        "case_id": req.case_id,
        "brand": req.brand,
        "mode": mode(),
        "composite": out["composite"],
        "dimensions": dims,
        "upc": out["upc_result"],
        "verdict": out["verdict"],
        "report": out["report"],
        "references": out["references"],
        "fetched_meta": out.get("fetched_meta", {"used": False}),
    }


# ---- serve the analyze.html app (single-service deploy) ----
# The root URL IS the app. Only analyze.html + /data are served; there is no
# prototype. The /api/* routes above are registered first, so they win.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@app.get("/")
def root():
    return FileResponse(os.path.join(_ROOT, "analyze.html"))


# Serve /analyze.html and /data/* (e.g. reference images). check_dir=False so a
# missing directory never crashes startup.
app.mount("/data", StaticFiles(directory=os.path.join(_ROOT, "data")), name="data")


@app.get("/analyze.html")
def analyze_page():
    return FileResponse(os.path.join(_ROOT, "analyze.html"))
