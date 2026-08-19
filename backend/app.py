"""FastAPI entrypoint for the VERITAS analysis backend.

POST /api/analyze runs the LangGraph orchestration and returns the structured
dimension results, composite verdict, UPC result, and the per-run Run Report
(token usage, cost per agent, eval signals). Mock mode runs with no API key;
set OPENAI_API_KEY to go live.

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
import scoring
import supa
from graph import DIMENSIONS, build_graph
from providers import mode

app = FastAPI(title="VF VERITAS Analysis API")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

GRAPH = build_graph()


# In-memory async jobs. A full run can take minutes — eleven vision calls, five
# of them concurrent — and holding one HTTP request open that long trips the
# platform's gateway timeout (→ 502). Instead we start the work in a background
# thread, return a job id immediately, and let the UI poll for the result.
_JOBS: dict = {}
_JOBS_LOCK = threading.Lock()

# Hard ceiling on one run's wall-clock, so a stuck engine can't run forever —
# the job returns whatever finished within the budget. The run is an async job
# the frontend polls, so this can be generous without risking a gateway timeout.
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


# One engine. Gemini and Kimi were removed on 2026-08-06; `provider` survives on
# the request so existing clients keep working, and any value resolves to GPT-5.5.
_PROVIDERS = ("openai",)


class AnalyzeReq(BaseModel):
    case_id: str
    brand: str = "TNF"
    provider: str = "openai"                  # GPT-5.5 — the only engine
    reference_source: str = "local"           # "local" (data/) | "google" | "product" (catalog)
    product_id: str | None = None             # selected catalog product (when reference_source=product)
    product: str = ""                        # product name — drives the per-product
                                             # UPC master lookup and the category
                                             # applicability table. Optional.
    suspect_image: str | None = None         # single product photo (back-compat)
    suspect_images: list[str] | None = None  # multiple product photos (preferred)
    upc_image: str | None = None             # barcode/UPC photo, drives the UPC OCR node


class CompareReq(AnalyzeReq):
    # Kept so an older client calling /api/compare still gets a valid answer.
    # With one engine there is nothing to compare: the endpoint runs GPT-5.5 once
    # and returns it in the same envelope.
    providers: list[str] = ["openai"]


@app.get("/api/health")
def health():
    return {"ok": True, "mode": mode()}


def _product_name(req: AnalyzeReq) -> str:
    """What this item is called. Used for the per-product UPC master lookup and
    for category applicability (a T-shirt has no hardware). The client may send
    it directly; otherwise resolve it from the selected catalog product."""
    if (req.product or "").strip():
        return req.product.strip()
    if req.product_id and supa.available():
        try:
            for p in supa.list_products():
                if p.get("id") == req.product_id:
                    return (p.get("name") or "").strip()
        except Exception:
            pass
    return ""


def _run_one(req: AnalyzeReq, provider: str) -> dict:
    """Run the graph end-to-end for one provider and shape the /api/analyze result."""
    imgs = req.suspect_images if req.suspect_images else ([req.suspect_image] if req.suspect_image else [])
    ref_source = req.reference_source if req.reference_source in ("local", "google", "product") else "local"
    state = {"case_id": req.case_id, "brand": req.brand, "provider": provider,
             "ref_source": ref_source, "product_id": req.product_id or "",
             "product_name": _product_name(req),
             "suspect_images": [b for b in imgs if b],
             "upc_image": req.upc_image or ""}
    out = GRAPH.invoke(state)
    dims = sorted(out["dimension_results"], key=lambda d: DIMENSIONS.index(d["dimension"]))
    return {
        "case_id": req.case_id, "brand": req.brand, "provider": provider,
        "product": state["product_name"],
        "composite": out["composite"], "dimensions": dims,
        "upc": out["upc_result"], "verdict": out["verdict"], "report": out["report"],
        "references": out["references"], "fetched_meta": out.get("fetched_meta", {"used": False}),
        "pairing": out.get("pairing", {"status": "skipped"}),
        "label_id": out.get("label_id", {}),
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
    """Retained for backward compatibility only.

    This ran several engines side by side on identical inputs. With Gemini and
    Kimi removed there is one engine, so it runs GPT-5.5 once and returns it in
    the multi-engine envelope an older client expects. New clients should call
    /api/analyze."""
    provs = ["openai"]

    def run(p):
        try:
            return {"ok": True, **_run_one(req, p)}
        except Exception as e:
            return {"ok": False, "provider": p, "error": str(e),
                    "case_id": req.case_id, "brand": req.brand}

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
                                   "case_id": req.case_id, "brand": req.brand,
                                   "error": "engine exceeded the time budget (still reasoning)"})
        return {"mode": mode(), "providers": provs, "results": results,
                "case_verdict": _case_verdict(results)}

    return _start_job(work)


def _case_verdict(results: dict) -> dict:
    """Stage 7 — one verdict for the case from several engines' composites.

    Never an average. Averaging engines is the dilution bug one level up: the
    single engine that actually resolved the foundry code would be voted down by
    the two that could not."""
    comps = {_ENGINE_CANON.get(p, p): (r or {}).get("composite") or {}
             for p, r in (results or {}).items() if (r or {}).get("ok")}
    return scoring.combine_engines(comps)


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
    case_id: str = ""                       # used when the run failed and data is empty
    brand: str = ""
    error: str = ""                         # non-empty records a failed run


# One canonical label per provider. The export groups engines by this value, so
# any drift — a differently-spelled label, or an empty one — silently splits a
# single engine into two column blocks and leaves half the rows looking unsaved.
# The provider on the response is authoritative; the client-supplied label is
# only a fallback.
_ENGINE_CANON = {"openai": "GPT-5.5"}


def _canonical_engine(req_engine: str, data: dict) -> str:
    prov = str((data or {}).get("provider") or "").strip().lower()
    if prov in _ENGINE_CANON:
        return _ENGINE_CANON[prov]
    label = (req_engine or "").strip()
    for canon in _ENGINE_CANON.values():          # tolerate case/spacing drift
        if label.lower() == canon.lower():
            return canon
    return label or "(engine not recorded)"


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
    # A failed run is recorded, not dropped. Losing it makes "this engine failed"
    # indistinguishable from "this engine was never run" — the row simply
    # disappears from the export with no trace.
    failed = (d.get("ok") is False) or bool(d.get("error")) or bool(req.error)
    comp = d.get("composite") or {}
    dims = d.get("dimensions") or []
    # Carry `status` through to the export: a null score alone cannot tell the
    # reader whether the dimension abstained or the engine never ran. `state`
    # and `internal_coverage` travel with the score for the same reason — a
    # stored run must be re-scorable, and a score without its state is exactly
    # the reading that cleared four guesses as authentic.
    dim_map = {x.get("dimension"): {"score": x.get("score"), "finding": x.get("finding") or "",
                                    "status": x.get("status") or "",
                                    "state": x.get("state") or "",
                                    "confidence": x.get("confidence"),
                                    "internal_coverage": x.get("internal_coverage", 0.0)}
               for x in dims if x.get("dimension")}
    upc = d.get("upc") or {}
    # ALWAYS a dict. Read in two places below, and a malformed or legacy
    # payload carrying a non-dict here would break persistence outright.
    _f = (d.get("label_id") or {}).get("fields")
    _fields = _f if isinstance(_f, dict) else {}
    verd = d.get("verdict") or {}
    tot = (d.get("report") or {}).get("totals") or {}
    evals = (d.get("report") or {}).get("evals") or {}
    return {
        "id": f"{int(time.time())}-{uuid.uuid4().hex[:6]}",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        # fall back to the request when the run failed before producing a result
        "case_id": d.get("case_id") or req.case_id or "",
        "brand": d.get("brand") or req.brand or "",
        "engine": _canonical_engine(req.engine, d),
        "product": req.product or "",
        "verdict": ("Run Failed" if failed else comp.get("verdict_label", "")),
        "score": comp.get("score"),
        "band": ("error" if failed else comp.get("band", "")),
        "error": (str(d.get("error") or req.error or "run failed") if failed else ""),
        "assessed": (comp.get("coverage") or {}).get("assessed"),
        "applicable": (comp.get("coverage") or {}).get("applicable"),
        # Stage 5. Coverage must never travel apart from the score: a 72 over
        # 30% of the item and a 72 over 90% of it are not the same statement.
        "coverage": comp.get("coverage_pct"),
        "deviation": comp.get("deviation"),
        # Every row must carry a lane, failures included. A blank lane falls out
        # of every filter, so the case is never picked up by anyone — which is
        # its own silent escape path. Run Failed routes to REVIEW.
        "lane": (comp.get("lane") or scoring.LANE_FOR_BAND.get(
            "error" if failed else comp.get("band", ""), "REVIEW")),
        # WHICH LADDER RUNG FIRED. Persisted so the export can print it beside
        # the verdict and anyone can check the call against one sentence in
        # scoring.RULES, instead of inferring it from prose or a database query.
        "rule": comp.get("rule") or "",
        "driver": comp.get("driver") or "",
        "recapture": comp.get("recapture") or [],
        # why a verdict was held back — the coverage column no longer shows it
        "capped": bool(comp.get("capped")),
        "reason": comp.get("reason", "") or "",
        "label_validation": {
            "hard_fail": bool((((d.get("label_id") or {}).get("validation")) or {}).get("hard_fail")),
            "failed": (((d.get("label_id") or {}).get("validation")) or {}).get("failed", []),
            "counts": (((d.get("label_id") or {}).get("validation")) or {}).get("counts", {}),
            "summary": (((d.get("label_id") or {}).get("validation")) or {}).get("summary", ""),
        },
        "spec_validation": {
            "spec_hard_fail": bool((comp.get("spec_validation") or {}).get("spec_hard_fail")),
            "provenance_hard_fail": bool(
                (comp.get("spec_validation") or {}).get("provenance_hard_fail")),
            "failed": (comp.get("spec_validation") or {}).get("failed", []),
            "counts": (comp.get("spec_validation") or {}).get("counts", {}),
            "internal_coverage": (comp.get("spec_validation") or {}).get(
                "internal_coverage", 0.0),
            "summary": (comp.get("spec_validation") or {}).get("summary", ""),
        },
        "logo_validation": {
            "spec_hard_fail": bool((comp.get("logo_validation") or {}).get("spec_hard_fail")),
            "provenance_hard_fail": bool(
                (comp.get("logo_validation") or {}).get("provenance_hard_fail")),
            "failed": (comp.get("logo_validation") or {}).get("failed", []),
            "counts": (comp.get("logo_validation") or {}).get("counts", {}),
            "internal_coverage": (comp.get("logo_validation") or {}).get(
                "internal_coverage", 0.0),
            "summary": (comp.get("logo_validation") or {}).get("summary", ""),
        },
        "material_validation": {
            "spec_hard_fail": bool((comp.get("material_validation") or {}).get("spec_hard_fail")),
            "provenance_hard_fail": bool(
                (comp.get("material_validation") or {}).get("provenance_hard_fail")),
            "failed": (comp.get("material_validation") or {}).get("failed", []),
            "counts": (comp.get("material_validation") or {}).get("counts", {}),
            "internal_coverage": (comp.get("material_validation") or {}).get(
                "internal_coverage", 0.0),
            "summary": (comp.get("material_validation") or {}).get("summary", ""),
        },
        # The OCR'd tag text the deterministic layers ran on. Kept because a
        # verdict that cannot be re-derived is not auditable, and because every
        # future text rule has to be backtestable against real tags — the first
        # material rules shipped blind precisely because this was not stored.
        "label_fields": {
            k: _fields.get(k, "")
            for k in ("care_text", "fiber_content", "product_family", "date_code",
                      "mark_text", "style_number")
        },
        "style_number": _fields.get("style_number", ""),
        "pairing": (d.get("pairing") or {}).get("status", ""),
        "dimensions": dim_map,
        "upc": {"status": upc.get("status", ""), "extracted": upc.get("extracted", ""),
                "expected": upc.get("expected", "")},
        # Stage 9. 'confirmed/refuted' was the vocabulary of the old prompt that
        # told three reviewers to REFUTE the verdict — and they refuted 5 of 5,
        # so the column carried no information. Reviewers now classify
        # independently and the agreement is tallied in code, so the column
        # records the tally AND what each reviewer actually said.
        "verifier": verd.get("verifier_votes") or (
            "confirmed" if verd.get("verifier_confirmed") else "refuted"),
        "verifier_confirmed": bool(verd.get("verifier_confirmed")),
        "reviewer_labels": verd.get("reviewer_labels") or [],
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
    if not req.data and not req.error:
        raise HTTPException(400, "no analysis data to save")
    rec = _build_record(req)
    supa.save_run(rec)
    return {"ok": True, "id": rec["id"], "count": supa.runs_count()}


def _export_name(first, last, ids):
    """Name the file after what is in it, so partial downloads do not all land
    in the downloads folder as VERITAS_analyses(3).xlsx."""
    if ids:
        return f"VERITAS_analyses_{len(ids)}_cases.xlsx"
    if first is not None and last is not None:
        return f"VERITAS_analyses_{first}-{last}.xlsx"
    if first is not None:
        return f"VERITAS_analyses_from_{first}.xlsx"
    if last is not None:
        return f"VERITAS_analyses_to_{last}.xlsx"
    return "VERITAS_analyses.xlsx"


@app.get("/api/export.xlsx")
def export_xlsx(first: int | None = None, last: int | None = None,
               cases: str | None = None):
    """Download the workbook, optionally limited to a slice of the history.

    first/last are inclusive case numbers matching the '#' column on the sheet;
    `cases` is a comma-separated list of case ids and takes precedence. With no
    parameters the whole history is exported, as before."""
    if not supa.available():
        raise HTTPException(503, "Supabase not configured")
    ids = [c for c in (cases or "").split(",") if c.strip()]
    runs = exporter.select_runs(supa.list_runs(), first=first, last=last, cases=ids)
    if not runs:
        raise HTTPException(404, "no saved runs match that selection")
    data = exporter.build_workbook(runs)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{_export_name(first, last, ids)}"'},
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
