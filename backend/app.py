"""FastAPI entrypoint for the VERITAS analysis backend.

POST /api/analyze runs the LangGraph orchestration and returns the structured
dimension results, composite verdict, UPC result, and the per-run Run Report
(token usage, cost per agent, eval signals). Mock mode runs with no API key;
set OPENAI_API_KEY to go live.

Run:  uvicorn app:app --port 8000   (from the backend/ directory)
"""
import json
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout, as_completed

# On Windows behind TLS-inspecting proxies (or with a stale CA bundle), Python's
# bundled certificates cannot validate hosts the operating system trusts, and
# every Supabase call dies with CERTIFICATE_VERIFY_FAILED. truststore delegates
# verification to the OS certificate store. Optional: without the package,
# behaviour is exactly as before. Must run before any TLS context is created.
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import audit as audit_log
import case_queue
import enterprise
import exporter
import intake
import preflight
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


def _register_job(jid):
    with _JOBS_LOCK:
        _JOBS[jid] = {"status": "running", "ts": time.time()}
        if len(_JOBS) > 60:                       # bound memory: drop old finished jobs
            for k in sorted(_JOBS, key=lambda k: _JOBS[k]["ts"])[:20]:
                if _JOBS[k]["status"] != "running":
                    _JOBS.pop(k, None)


def _start_job(fn):
    """fn(set_partial) runs in a background thread. It may call set_partial(obj)
    to publish intermediate progress that GET /api/job returns while running."""
    jid = uuid.uuid4().hex
    _register_job(jid)

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


# ---- queue-backed analyze (RabbitMQ) ----
# /api/analyze publishes the case to the RabbitMQ case queue and worker.py runs
# it; the result comes back on the results queue and lands in _JOBS, so the
# UI's existing /api/job polling works unchanged. If the broker is unreachable
# the endpoint falls back to the original inline background thread — a laptop
# without Docker running, or a deploy without a broker, keeps working as before.
# USE_QUEUE=0 disables the queue path outright.
USE_QUEUE = os.environ.get("USE_QUEUE", "1").strip().lower() in ("1", "true", "yes", "on")
_RESULTS_THREAD_LOCK = threading.Lock()
_results_thread = None


def _results_loop():
    """Consume the results queue forever; each message resolves one job."""
    import pika

    def on_result(ch, method, _props, body):
        try:
            msg = json.loads(body)
            jid = msg.get("job_id")
            if jid and jid in _JOBS:               # unknown jid (API restarted) → drop
                if msg.get("ok"):
                    _JOBS[jid] = {"status": "done", "result": msg.get("result") or {},
                                  "ts": time.time()}
                else:
                    _JOBS[jid] = {"status": "error",
                                  "error": msg.get("error") or "run failed",
                                  "ts": time.time()}
        except Exception:
            pass                                    # malformed result: ack and move on
        ch.basic_ack(method.delivery_tag)

    while True:
        try:
            conn, ch = case_queue.connect()
            ch.basic_consume(queue=case_queue.RESULTS_QUEUE,
                             on_message_callback=on_result)
            ch.start_consuming()
        except Exception:
            time.sleep(5)                           # broker down — retry quietly


def _ensure_results_consumer():
    global _results_thread
    with _RESULTS_THREAD_LOCK:
        if _results_thread is None or not _results_thread.is_alive():
            _results_thread = threading.Thread(target=_results_loop, daemon=True)
            _results_thread.start()


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


class PreflightReq(BaseModel):
    case_id: str = ""
    suspect_images: list[str] | None = None


@app.post("/api/preflight")
def preflight_check(req: PreflightReq):
    """Stage 0. The UI calls this before /api/analyze so an unusable batch is
    bounced in milliseconds — no engine run, nothing stored beyond one log row."""
    res = preflight.check(req.suspect_images or [])
    if not res["ok"]:
        preflight.log_rejection(req.case_id, res)
    return res


class IntakeReq(BaseModel):
    case_id: str
    brand: str = "TNF"
    origin: str = ""
    note: str = ""
    submitter_id: str = ""


@app.post("/api/intake")
def intake_create(req: IntakeReq):
    """Open a case in the register. Idempotent by case_id — a run starting
    twice, or an external feed retrying, never duplicates the case."""
    try:
        return intake.create(req.case_id, req.brand, req.origin, req.note, req.submitter_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/intake")
def intake_list(status: str = ""):
    return {"cases": intake.list_cases(status)}


class CaseCreateReq(BaseModel):
    case_id: str = ""                     # blank -> generated VF-<year>-<seq>
    brand: str = ""
    source_channel: str = ""
    priority: str = "Standard"
    location: str = ""
    origin_country: str = ""
    notes_text: str = ""
    submitter_id: str = ""
    extraction: dict = {}
    images: list[dict] = []               # [{name, b64}]


@app.post("/api/case")
def case_create(req: CaseCreateReq):
    """Full intake (E3-09): metadata + images stored with SHA-256 hashes."""
    try:
        return intake.create_full(req.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/case/{cid}")
def case_get(cid: str):
    rec = intake.get(cid)
    if not rec:
        raise HTTPException(404, "case not found")
    return rec


class CasePatchReq(BaseModel):
    status: str | None = None
    stage: int | None = None
    assigned_to: str | None = None
    score: int | None = None
    verdict: str | None = None
    brand: str | None = None
    priority: str | None = None
    location: str | None = None
    origin_country: str | None = None
    extraction: dict | None = None
    override: dict | None = None          # {tab, decision, notes, user}


@app.patch("/api/case/{cid}")
def case_patch(cid: str, req: CasePatchReq):
    try:
        rec = intake.patch(cid, {k: v for k, v in req.model_dump().items() if v is not None})
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not rec:
        raise HTTPException(404, "case not found")
    return rec


class NoteReq(BaseModel):
    author: str = ""
    text: str


@app.post("/api/case/{cid}/notes")
def case_note(cid: str, req: NoteReq):
    try:
        note = intake.add_note(cid, req.author, req.text)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not note:
        raise HTTPException(404, "case not found")
    return note


@app.get("/api/queue")
def case_queue_list():
    """The Case Queue (Epic 2): every case record, newest first."""
    return {"cases": intake.list_cases()}


@app.get("/api/case-image/{cid}/{fn}")
def case_image(cid: str, fn: str):
    p = intake.image_path(cid, fn)
    if not p:
        raise HTTPException(404, "image not found")
    media = "application/pdf" if p.endswith(".pdf") else \
            "image/png" if p.endswith(".png") else "image/jpeg"
    return FileResponse(p, media_type=media)


class ExtractReq(BaseModel):
    images: list[str] = []                # base64, no data: header


@app.post("/api/extract")
def extract_metadata(req: ExtractReq):
    """Intake AI extraction (E3-06/07): brand, UPC, style, origin, text, boxes."""
    try:
        return enterprise.extract(req.images)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"extraction failed: {str(e)[:160]}")


@app.get("/api/pim")
def pim(upc: str = "", style: str = ""):
    """PIM lookup by UPC or style number (E8-01/02)."""
    if not upc and not style:
        raise HTTPException(400, "pass upc= or style=")
    return enterprise.pim_lookup(upc, style)


@app.get("/api/dam")
def dam(style: str = "", name: str = ""):
    """Authentic reference imagery by style (E8-03)."""
    return enterprise.dam_images(style, name)


@app.get("/api/suppliers")
def suppliers(factory: str = "", country: str = "", name: str = ""):
    """Authorized Supplier Registry cross-reference (E4-23, E8-08)."""
    return enterprise.suppliers_query(factory, country, name)


@app.get("/api/tms")
def tms(origin: str = "", dest: str = ""):
    """TMS shipping-lane cross-reference (E5-06, E8-09)."""
    return enterprise.tms_lanes(origin, dest)


class AuditReq(BaseModel):
    action: str
    detail: str = ""
    user: str = ""


@app.post("/api/audit")
def audit_record(req: AuditReq):
    """Append-only. There is no update or delete route on purpose."""
    try:
        return audit_log.record(req.action, req.detail, req.user)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/audit")
def audit_recent(limit: int = 50):
    return {"entries": audit_log.recent(limit)}


def _hydrate_from_case(req: AnalyzeReq) -> None:
    """A run started from the case screen sends no photos — the images already
    live in the case store from intake. Load them off disk, downscale to ~1024px
    for the engine, and pick the barcode shot (extraction.upc.image_index) as
    the UPC image."""
    if req.suspect_images or req.suspect_image:
        return
    rec = intake.get(req.case_id)
    if not rec:
        return
    import base64
    import io
    from PIL import Image as _PILImage
    upc_idx = ((rec.get("extraction") or {}).get("upc") or {}).get("image_index")
    b64s: list[str] = []
    upc_b64 = None
    for i, im in enumerate(rec.get("images") or []):
        fn = im.get("file") or ""
        if fn.lower().endswith(".pdf"):
            continue
        path = intake.image_path(req.case_id, fn)
        if not path or not os.path.exists(path):
            continue
        try:
            with _PILImage.open(path) as pic:
                pic = pic.convert("RGB")
                pic.thumbnail((1024, 1024))
                buf = io.BytesIO()
                pic.save(buf, "JPEG", quality=88)
            b64 = base64.b64encode(buf.getvalue()).decode()
        except Exception:
            continue
        if upc_idx is not None and i == upc_idx:
            upc_b64 = b64
        b64s.append(b64)
    if b64s:
        req.suspect_images = b64s
        if not req.upc_image:
            req.upc_image = upc_b64 or b64s[0]


def _preflight_or_422(req: AnalyzeReq):
    """Defence in depth: the same gate inside /api/analyze, for clients that
    skip the preflight endpoint. Raises 422 with a readable reason."""
    if not preflight.ENABLED:
        return
    imgs = req.suspect_images if req.suspect_images else ([req.suspect_image] if req.suspect_image else [])
    res = preflight.check(imgs)
    if not res["ok"]:
        preflight.log_rejection(req.case_id, res)
        why = "; ".join(f"photo {p['image']}: {p['issue']}" if p.get("image") else p["issue"]
                        for p in res["problems"]) or "no usable photos"
        raise HTTPException(422, f"pre-flight rejected — {why}. Retake and resubmit; "
                                 f"the run was not started and nothing was stored.")


@app.post("/api/analyze")
def analyze(req: AnalyzeReq):
    provider = req.provider if req.provider in _PROVIDERS else "openai"
    _hydrate_from_case(req)
    _preflight_or_422(req)

    if USE_QUEUE:
        try:
            jid = uuid.uuid4().hex
            _ensure_results_consumer()
            case_queue.publish({**req.model_dump(), "job_id": jid})
            _register_job(jid)
            return {"job_id": jid, "queued": True}
        except Exception:
            pass                       # broker unreachable → inline, as before

    def work(_set_partial):
        with ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(_run_one, req, provider)
            try:
                res = fut.result(timeout=RUN_DEADLINE)
            except FuturesTimeout:
                raise RuntimeError(f"{provider} exceeded the {int(RUN_DEADLINE)}s time budget")
        return {"mode": mode(), **res}

    return _start_job(work)


@app.post("/api/enqueue")
def enqueue(req: AnalyzeReq):
    """Drop a case onto the RabbitMQ case queue instead of running it inline.

    This is the producer entry point for UiPath (standard HTTP Request
    activity): the robot pulls a case from Casemates.io and POSTs the same
    payload /api/analyze takes. worker.py picks it up, runs the analysis, and
    saves the outcome to the runs store, where /api/cases shows it."""
    if not req.case_id.strip():
        raise HTTPException(400, "case_id is required")
    try:
        case_queue.publish(req.model_dump())
    except Exception as e:
        raise HTTPException(503, f"case queue unavailable: {e}")
    return {"queued": True, "case_id": req.case_id, "queue": case_queue.QUEUE}


@app.get("/api/queue/status")
def queue_status():
    """Queue depth + consumer count, for dashboards and smoke tests."""
    try:
        return {"available": True, **case_queue.depth()}
    except Exception as e:
        return {"available": False, "error": str(e)}


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
    queued = intake.list_cases("queued")
    if not supa.available():
        return {"available": False, "cases": [], "kpis": {}, "queued": queued}
    try:
        runs = supa.list_runs()                   # oldest first
    except Exception as e:
        # A configured-but-unreachable store (network, SSL, outage) must not
        # 500 the whole cases page — the register still has the queue.
        return {"available": False, "cases": [], "kpis": {}, "queued": queued,
                "store_error": str(e)[:200]}
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
    return {"available": True, "count": len(runs), "kpis": kpis, "cases": out,
            "queued": queued}


@app.get("/api/cases/{rid}")
def case_detail(rid: str):
    """Full saved run for the case dashboard — clicking a row opens this."""
    if not supa.available():
        raise HTTPException(503, "Supabase not configured")
    try:
        rec = supa.get_run(rid)
    except Exception as e:
        raise HTTPException(503, f"case store unreachable: {str(e)[:160]}")
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
    try:
        return {"available": True, "products": supa.list_products()}
    except Exception as e:
        # configured-but-unreachable store: degrade like unconfigured, say why
        return {"available": False, "products": [], "store_error": str(e)[:200]}


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
                                    "internal_coverage": x.get("internal_coverage", 0.0),
                                    # WHY a dimension was or was not measurable:
                                    # what the locator found, whether it could be
                                    # cropped, whether it was legible. Without
                                    # this the export's region columns are blank
                                    # and every 'not visible' needs a database
                                    # query to explain.
                                    "region": x.get("region") or {},
                                    # A deterministic injection must be re-read
                                    # AS one. evidence_gate() refuses to let a
                                    # text check stand in for a forensic
                                    # examination, and it can only do that if the
                                    # flag survives persistence — otherwise a
                                    # re-scored run clears on 'the label, plus
                                    # the label'.
                                    "deterministic": bool(x.get("deterministic"))}
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
    try:
        return {"available": True, "count": supa.runs_count()}
    except Exception as e:
        return {"available": False, "count": 0, "store_error": str(e)[:200]}


@app.post("/api/export/save")
def export_save(req: ExportSaveReq):
    if not supa.available():
        raise HTTPException(503, "Supabase not configured")
    if not req.data and not req.error:
        raise HTTPException(400, "no analysis data to save")
    rec = _build_record(req)
    supa.save_run(rec)
    intake.mark_analyzed(rec.get("case_id") or "")   # the register follows the store
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
               cases: str | None = None, full: int = 0):
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
    data = exporter.build_workbook(runs, full=bool(full))
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
