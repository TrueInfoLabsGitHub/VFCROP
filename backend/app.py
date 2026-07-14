"""FastAPI entrypoint for the VERITAS analysis backend.

POST /api/analyze runs the LangGraph orchestration and returns the structured
dimension results, composite verdict, UPC result, and the per-run Run Report
(token usage, cost per agent, eval signals). Mock mode runs with no API keys;
set GEMINI_API_KEY / OPENAI_API_KEY to go live.

Run:  uvicorn app:app --port 8000   (from the backend/ directory)
"""
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout, as_completed

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


# In-memory async jobs. A full run (esp. Compare with the slow Kimi reasoning
# model) can take minutes; holding one HTTP request open that long trips the
# platform's gateway timeout (→ 502). Instead we start the work in a background
# thread, return a job id immediately, and let the UI poll for the result.
_JOBS: dict = {}
_JOBS_LOCK = threading.Lock()

# Hard ceiling on one run's wall-clock, so a stuck/very-slow engine can't run
# forever — the job returns whatever finished within the budget. Reasoning
# models (Kimi K2.6) run a full parallel-dimension + verdict chain that can take
# several minutes; the run is an async job the frontend polls, so this can be
# generous without risking a gateway timeout. Raise on Railway if Kimi needs more.
RUN_DEADLINE = float(os.environ.get("RUN_DEADLINE", "420"))


def _start_job(fn):
    """fn(set_partial) runs in a background thread. It may call set_partial(obj)
    to publish intermediate progress that GET /api/job returns while running."""
    jid = uuid.uuid4().hex
    with _JOBS_LOCK:
        _JOBS[jid] = {"status": "running", "ts": time.time()}
        if len(_JOBS) > 60:                       # bound memory: drop old finished jobs
            for k in sorted(_JOBS, key=lambda k: _JOBS[k]["ts"])[:20]:
                if _JOBS[k]["status"] != "running":
                    _JOBS.pop(k, None)

    def set_partial(obj):
        j = _JOBS.get(jid)
        if j and j.get("status") == "running":
            j["partial"] = obj

    def worker():
        try:
            _JOBS[jid] = {"status": "done", "result": fn(set_partial), "ts": time.time()}
        except Exception as e:
            _JOBS[jid] = {"status": "error", "error": str(e), "ts": time.time()}

    threading.Thread(target=worker, daemon=True).start()
    return {"job_id": jid}


_PROVIDERS = ("openai", "gemini", "kimi")


class AnalyzeReq(BaseModel):
    case_id: str
    brand: str = "TNF"
    provider: str = "openai"                  # "openai" (GPT-5.5) | "gemini" | "kimi" (via router)
    reference_source: str = "local"           # "local" (data/) | "google" | "product" (catalog)
    product_id: str | None = None             # selected catalog product (when reference_source=product)
    suspect_image: str | None = None         # single product photo (back-compat)
    suspect_images: list[str] | None = None  # multiple product photos (preferred)
    upc_image: str | None = None             # barcode/UPC photo, drives the UPC OCR node


class CompareReq(AnalyzeReq):
    # Which engines to run side-by-side on the SAME inputs. They execute
    # concurrently; each returns its own full result (or an error entry).
    providers: list[str] = ["openai", "gemini", "kimi"]


@app.get("/api/health")
def health():
    return {"ok": True, "mode": mode()}


def _run_one(req: AnalyzeReq, provider: str) -> dict:
    """Run the graph end-to-end for one provider and shape the /api/analyze result."""
    imgs = req.suspect_images if req.suspect_images else ([req.suspect_image] if req.suspect_image else [])
    ref_source = req.reference_source if req.reference_source in ("local", "google", "product") else "local"
    state = {"case_id": req.case_id, "brand": req.brand, "provider": provider,
             "ref_source": ref_source, "product_id": req.product_id or "",
             "suspect_images": [b for b in imgs if b],
             "upc_image": req.upc_image or ""}
    out = GRAPH.invoke(state)
    dims = sorted(out["dimension_results"], key=lambda d: DIMENSIONS.index(d["dimension"]))
    return {
        "case_id": req.case_id, "brand": req.brand, "provider": provider,
        "composite": out["composite"], "dimensions": dims,
        "upc": out["upc_result"], "verdict": out["verdict"], "report": out["report"],
        "references": out["references"], "fetched_meta": out.get("fetched_meta", {"used": False}),
    }


@app.post("/api/analyze")
def analyze(req: AnalyzeReq):
    provider = req.provider if req.provider in _PROVIDERS else "openai"

    def work(_set_partial):
        with ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(_run_one, req, provider)
            try:
                res = fut.result(timeout=RUN_DEADLINE)
            except FuturesTimeout:
                raise RuntimeError(f"{provider} exceeded the {int(RUN_DEADLINE)}s time budget")
        return {"mode": mode(), **res}

    return _start_job(work)


@app.post("/api/compare")
def compare(req: CompareReq):
    """Start a side-by-side run of several engines on identical inputs. They run
    concurrently in the background and each result is published as it completes,
    so fast engines show immediately. Any engine still running when the deadline
    hits is marked as timed out — the run always returns."""
    provs = [p for p in req.providers if p in _PROVIDERS] or ["openai"]

    def run(p):
        try:
            return {"ok": True, **_run_one(req, p)}
        except Exception as e:
            return {"ok": False, "provider": p, "error": str(e)}

    def work(set_partial):
        results = {}
        set_partial({"mode": mode(), "providers": provs, "results": {}})
        with ThreadPoolExecutor(max_workers=len(provs)) as ex:
            futs = {ex.submit(run, p): p for p in provs}
            try:
                for fut in as_completed(list(futs), timeout=RUN_DEADLINE):
                    results[futs[fut]] = fut.result()
                    set_partial({"mode": mode(), "providers": provs, "results": dict(results)})
            except FuturesTimeout:
                pass
        for p in provs:
            results.setdefault(p, {"ok": False, "provider": p,
                                   "error": "engine exceeded the time budget (still reasoning)"})
        return {"mode": mode(), "providers": provs, "results": results}

    return _start_job(work)


@app.get("/api/job/{jid}")
def job_status(jid: str):
    j = _JOBS.get(jid)
    if not j:
        raise HTTPException(404, "unknown job")
    if j["status"] == "running":
        out = {"status": "running"}
        if j.get("partial"):
            out["partial"] = j["partial"]
        return out
    if j["status"] == "error":
        return {"status": "error", "error": j.get("error", "run failed")}
    return {"status": "done", **j["result"]}


@app.get("/api/cases")
def cases():
    """Case dashboard: KPIs + recent cases, derived from the saved-runs store."""
    if not supa.available():
        return {"available": False, "cases": [], "kpis": {}}
    runs = supa.list_runs()                       # oldest first
    num = lambda v: v if isinstance(v, (int, float)) else None
    scores = [r["score"] for r in runs if num(r.get("score")) is not None]
    lats = [r["latency_ms"] for r in runs if num(r.get("latency_ms")) is not None]
    med = None
    if lats:
        s = sorted(lats)
        n = len(s)
        med = round((s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2) / 1000)
    kpis = {
        "open": len(runs),
        "counterfeit": sum(1 for r in runs if r.get("band") == "counterfeit"),
        "review": sum(1 for r in runs if r.get("band") == "caution"),
        "avg_deviation": round(sum(scores) / len(scores)) if scores else None,
        "median_turnaround_s": med,
        "spark": scores[-12:],
    }
    out = []
    for r in reversed(runs[-60:]):                # most recent first
        dims = r.get("dimensions") or {}
        top = ("", -1)
        for v in dims.values():
            if isinstance(v, dict) and isinstance(v.get("score"), (int, float)) and v["score"] > top[1]:
                top = (v.get("finding") or "", v["score"])
        out.append({
            "id": r.get("case_id") or r.get("id"),
            "rid": r.get("id"),                       # unique run id, for opening the detail
            "brand": r.get("brand", ""), "engine": r.get("engine", ""),
            "verdict": r.get("verdict", ""), "band": r.get("band", ""),
            "score": r.get("score"), "upc": (r.get("upc") or {}).get("status", ""),
            "summary": top[0], "created_at": r.get("created_at", ""),
        })
    return {"available": True, "count": len(runs), "kpis": kpis, "cases": out}


@app.get("/api/cases/{rid}")
def case_detail(rid: str):
    """Full saved run for the case dashboard — clicking a row opens this."""
    if not supa.available():
        raise HTTPException(503, "Supabase not configured")
    rec = supa.get_run(rid)
    if not rec:
        raise HTTPException(404, "run not found")
    return rec


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
    product_id: str = ""                    # catalog id → authentic refs resolved server-side
    suspect_image: str | None = None        # base64 (no data: header, back-compat)
    suspect_images: list[str] = []          # all suspect photos (preferred)
    reference_images: list[str] = []        # authentic reference photos (optional override)
    upc_image: str | None = None
    data: dict = {}                         # the /api/analyze response


def _build_record(req: ExportSaveReq) -> dict:
    d = req.data or {}
    _sus_imgs = req.suspect_images or ([req.suspect_image] if req.suspect_image else [])
    _sus_imgs = [b for b in _sus_imgs if b]
    # authentic references: explicit override, else resolve from the catalog
    _ref_imgs = [b for b in (req.reference_images or []) if b]
    if not _ref_imgs and req.product_id and supa.available():
        try:
            _ref_imgs = supa.product_images_b64(req.product_id, cap=12)
        except Exception:
            _ref_imgs = []
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
        "suspect_thumbs": [t for t in (exporter.thumb(b) for b in _sus_imgs) if t],
        "reference_thumbs": [t for t in (exporter.thumb(b) for b in _ref_imgs) if t],
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
