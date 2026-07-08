"""FastAPI entrypoint for the VERITAS analysis backend.

POST /api/analyze runs the LangGraph orchestration and returns the structured
dimension results, composite verdict, UPC result, and the per-run Run Report
(token usage, cost per agent, eval signals). Mock mode runs with no API keys;
set GEMINI_API_KEY / OPENAI_API_KEY to go live.

Run:  uvicorn app:app --port 8000   (from the backend/ directory)
"""
import os
import time
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import exporter
import supa
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
    reference_source: str = "local"           # "local" (data/) | "google" | "product" (catalog)
    product_id: str | None = None             # selected catalog product (when reference_source=product)
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
    ref_source = req.reference_source if req.reference_source in ("local", "google", "product") else "local"
    state = {"case_id": req.case_id, "brand": req.brand, "provider": provider,
             "ref_source": ref_source, "product_id": req.product_id or "",
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


# ---- product catalog (Supabase Storage) ----
class ProductReq(BaseModel):
    name: str
    brand: str = ""
    images: list[str] = []   # base64 (no data: header)


@app.get("/api/products")
def products_list():
    if not supa.available():
        return {"available": False, "products": []}
    return {"available": True, "products": supa.list_products()}


@app.post("/api/products")
def products_create(req: ProductReq):
    if not supa.available():
        raise HTTPException(503, "Supabase not configured")
    if not req.name.strip() or not req.images:
        raise HTTPException(400, "name and at least one image are required")
    return supa.create_product(req.name.strip(), req.brand.strip(), req.images)


@app.delete("/api/products/{pid}")
def products_delete(pid: str):
    if not supa.available():
        raise HTTPException(503, "Supabase not configured")
    supa.delete_product(pid)
    return {"ok": True}


@app.get("/api/products/{pid}/img/{filename}")
def products_image(pid: str, filename: str):
    b = supa.image_bytes(pid, filename)
    if b is None:
        raise HTTPException(404, "image not found")
    return Response(content=b, media_type="image/jpeg")


# ---- Excel export log (Supabase-backed history) ----
class ExportSaveReq(BaseModel):
    engine: str = ""
    product: str = ""
    suspect_image: str | None = None    # base64 (no data: header)
    upc_image: str | None = None
    data: dict = {}                     # the /api/analyze response


def _build_record(req: ExportSaveReq) -> dict:
    d = req.data or {}
    comp = d.get("composite") or {}
    dims = d.get("dimensions") or []
    dim_map = {x.get("dimension"): {"score": x.get("score"), "finding": x.get("finding") or ""}
               for x in dims if x.get("dimension")}
    upc = d.get("upc") or {}
    verd = d.get("verdict") or {}
    tot = (d.get("report") or {}).get("totals") or {}
    evals = (d.get("report") or {}).get("evals") or {}
    return {
        "id": f"{int(time.time())}-{uuid.uuid4().hex[:6]}",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "case_id": d.get("case_id", ""),
        "brand": d.get("brand", ""),
        "engine": req.engine or "",
        "product": req.product or "",
        "verdict": comp.get("verdict_label", ""),
        "score": comp.get("score"),
        "band": comp.get("band", ""),
        "dimensions": dim_map,
        "upc": {"status": upc.get("status", ""), "extracted": upc.get("extracted", ""),
                "expected": upc.get("expected", "")},
        "verifier": "confirmed" if verd.get("verifier_confirmed") else "refuted",
        "confidence": evals.get("confidence"),
        "tokens": tot.get("tokens"),
        "cost": tot.get("cost"),
        "latency_ms": tot.get("wall_ms"),
        "suspect_thumb": exporter.thumb(req.suspect_image),
        "upc_thumb": exporter.thumb(req.upc_image),
    }


@app.get("/api/export/count")
def export_count():
    if not supa.available():
        return {"available": False, "count": 0}
    return {"available": True, "count": supa.runs_count()}


@app.post("/api/export/save")
def export_save(req: ExportSaveReq):
    if not supa.available():
        raise HTTPException(503, "Supabase not configured")
    if not req.data:
        raise HTTPException(400, "no analysis data to save")
    rec = _build_record(req)
    supa.save_run(rec)
    return {"ok": True, "id": rec["id"], "count": supa.runs_count()}


@app.get("/api/export.xlsx")
def export_xlsx():
    if not supa.available():
        raise HTTPException(503, "Supabase not configured")
    data = exporter.build_workbook(supa.list_runs())
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="VERITAS_analyses.xlsx"'},
    )


@app.delete("/api/export")
def export_clear():
    if not supa.available():
        raise HTTPException(503, "Supabase not configured")
    supa.clear_runs()
    return {"ok": True, "count": 0}


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
