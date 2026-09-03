"""Case store — the system of record for cases (SOW: "Casemates").

One JSONL row per case under data/, images on disk under data/cases/<id>/ with
SHA-256 computed at intake (chain-of-evidence). Zero-config storage; the
interface is table-shaped so a later move to a database is a storage swap.

Schema of a case row:
    case_id         VF-<year>-<seq> (generated) or caller-supplied
    brand           TNF | Vans | Timberland
    source_channel  one of SOURCE_CHANNELS (9 intake types)
    priority        Standard | High | Urgent
    location        free text (seizure location)
    origin_country  extracted / entered
    notes_text      intake notes (free text)
    submitter_id    who/what created it (a bot, per the SOW, or a user)
    status          New | In Review | Authenticated | Enforcement | Closed
    stage           1..6  (Intake, UPC, Style, Construction, Origin, Summary)
    assigned_to     analyst name
    score           latest counterfeit-probability score (0-100) or null
    verdict         latest composite verdict label
    images          [{file, name, sha256, bytes, width, height, low_res}]
    extraction      the AI intake extraction payload (brand/upc/style/... )
    overrides       {tab: {decision, notes, user, ts}}
    notes           [{ts, author, text}]  (investigation notes, E5-05)
    opened_at / analyzed_at / closed_at
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import threading
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INTAKE_PATH = os.environ.get("INTAKE_PATH", os.path.join(_ROOT, "data", "intake_cases.jsonl"))
CASE_FILES_DIR = os.environ.get("CASE_FILES_DIR", os.path.join(_ROOT, "data", "cases"))

_LOCK = threading.Lock()

STATUSES = ("New", "In Review", "Authenticated", "Enforcement", "Closed")
STAGES = ("Intake", "UPC", "Style", "Construction", "Origin", "Summary")
SOURCE_CHANNELS = (
    "Customs Seizure", "Marketplace Listing", "Test Purchase", "Retail Audit",
    "Consumer Report", "Distributor Report", "Online Monitoring",
    "Law Enforcement Referral", "Internal Investigation")
PRIORITIES = ("Standard", "High", "Urgent")
MAX_IMAGE_BYTES = 25 * 1024 * 1024          # SOW E3-01: max 25MB per file
LOW_RES_EDGE = 1000                          # SOW E3-03: warn below 1000px longest edge


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _read_all() -> list[dict]:
    if not os.path.exists(INTAKE_PATH):
        return []
    out = []
    with open(INTAKE_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass                        # one bad line never loses the file
    return out


def _write_all(rows: list[dict]) -> None:
    os.makedirs(os.path.dirname(INTAKE_PATH), exist_ok=True)
    tmp = INTAKE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, INTAKE_PATH)


def _next_case_id(rows) -> str:
    year = time.gmtime().tm_year
    prefix = f"VF-{year}-"
    seq = 0
    for r in rows:
        m = re.match(rf"^{re.escape(prefix)}(\d+)$", r.get("case_id") or "")
        if m:
            seq = max(seq, int(m.group(1)))
    return f"{prefix}{seq + 1:04d}"


def _blank(case_id: str) -> dict:
    return {"case_id": case_id, "brand": "", "source_channel": "", "priority": "Standard",
            "location": "", "origin_country": "", "notes_text": "", "submitter_id": "",
            "status": "New", "stage": 1, "assigned_to": "", "score": None, "verdict": "",
            "images": [], "extraction": {}, "overrides": {}, "notes": [],
            "ref_product_id": "", "ref_product_name": "",
            "opened_at": _now(), "analyzed_at": "", "closed_at": ""}


def _image_dims(raw: bytes):
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(raw))
        return im.size
    except Exception:
        return (0, 0)


def save_images(case_id: str, images: list[dict]) -> list[dict]:
    """images: [{name, b64}] -> stored files + metadata with SHA-256 hashes.
    Oversize files are rejected loudly, never silently trimmed."""
    folder = os.path.join(CASE_FILES_DIR, case_id)
    os.makedirs(folder, exist_ok=True)
    out = []
    for i, img in enumerate(images or []):
        raw = base64.b64decode((img.get("b64") or ""), validate=False)
        if not raw:
            continue
        if len(raw) > MAX_IMAGE_BYTES:
            raise ValueError(f"file {img.get('name') or i} exceeds the 25MB limit")
        name = (img.get("name") or f"image_{i}").strip()
        ext = ".pdf" if raw[:4] == b"%PDF" else ".png" if raw[:8].startswith(b"\x89PNG") else ".jpg"
        fn = f"img_{i}{ext}"
        with open(os.path.join(folder, fn), "wb") as f:
            f.write(raw)
        w, h = (0, 0) if ext == ".pdf" else _image_dims(raw)
        out.append({"file": fn, "name": name, "sha256": hashlib.sha256(raw).hexdigest(),
                    "bytes": len(raw), "width": w, "height": h,
                    "low_res": bool(w and h and max(w, h) < LOW_RES_EDGE)})
    return out


def image_path(case_id: str, fn: str) -> str | None:
    if "/" in fn or "\\" in fn or ".." in fn or "/" in case_id or "\\" in case_id or ".." in case_id:
        return None
    p = os.path.join(CASE_FILES_DIR, case_id, fn)
    return p if os.path.isfile(p) else None


def create_full(payload: dict) -> dict:
    """Full intake (E3-09). Generates the case id when none supplied; saves
    images with hashes; validates enum fields; idempotent by case_id."""
    with _LOCK:
        rows = _read_all()
        cid = (payload.get("case_id") or "").strip() or _next_case_id(rows)
        for r in rows:
            if r.get("case_id") == cid:
                return r
        row = _blank(cid)
        for k in ("brand", "location", "origin_country", "notes_text", "submitter_id",
                  "assigned_to"):
            row[k] = str(payload.get(k) or "").strip()[:400]
        sc = str(payload.get("source_channel") or "").strip()
        row["source_channel"] = sc if sc in SOURCE_CHANNELS else sc[:60]
        pr = str(payload.get("priority") or "Standard").strip()
        row["priority"] = pr if pr in PRIORITIES else "Standard"
        if isinstance(payload.get("extraction"), dict):
            row["extraction"] = payload["extraction"]
        row["images"] = save_images(cid, payload.get("images") or [])
        rows.append(row)
        _write_all(rows)
        return row


def get(case_id: str) -> dict | None:
    for r in _read_all():
        if r.get("case_id") == case_id:
            return r
    return None


_PATCHABLE = ("status", "stage", "assigned_to", "score", "verdict", "brand",
              "priority", "location", "origin_country", "extraction",
              "ref_product_id", "ref_product_name")


def patch(case_id: str, changes: dict) -> dict | None:
    """Update case fields (SOW: PATCH to Casemates). Status/stage validated;
    Closed stamps closed_at; a score arriving stamps analyzed_at."""
    with _LOCK:
        rows = _read_all()
        for r in rows:
            if r.get("case_id") != case_id:
                continue
            for k in _PATCHABLE:
                if k not in changes:
                    continue
                v = changes[k]
                if k == "status":
                    if v not in STATUSES:
                        raise ValueError(f"status must be one of {STATUSES}")
                    if v == "Closed":
                        r["closed_at"] = _now()
                elif k == "stage":
                    v = max(1, min(6, int(v)))
                elif k == "score":
                    v = None if v is None else max(0, min(100, int(v)))
                    if v is not None and not r.get("analyzed_at"):
                        r["analyzed_at"] = _now()
                r[k] = v
            ov = changes.get("override")
            if isinstance(ov, dict) and ov.get("tab"):
                r.setdefault("overrides", {})[str(ov["tab"])[:20]] = {
                    "decision": str(ov.get("decision") or "")[:80],
                    "notes": str(ov.get("notes") or "")[:400],
                    "user": str(ov.get("user") or "analyst")[:80], "ts": _now()}
            _write_all(rows)
            return r
    return None


def add_note(case_id: str, author: str, text: str) -> dict | None:
    """Investigation note with timestamp + author attribution (E5-05)."""
    text = (text or "").strip()
    if not text:
        raise ValueError("note text is required")
    with _LOCK:
        rows = _read_all()
        for r in rows:
            if r.get("case_id") == case_id:
                note = {"ts": _now(), "author": (author or "analyst")[:80], "text": text[:2000]}
                r.setdefault("notes", []).append(note)
                _write_all(rows)
                return note
    return None


def list_cases(status: str = "") -> list[dict]:
    """Newest first; optional status filter. Full rows minus image binaries
    (which live on disk anyway)."""
    rows = _read_all()
    if status:
        want = status.lower()
        rows = [r for r in rows
                if (r.get("status") or "").lower() == want
                or ("queued" == want and r.get("status") == "New")
                or ("analyzed" == want and r.get("analyzed_at"))]
    return sorted(rows, key=lambda r: r.get("opened_at", ""), reverse=True)


# ---- back-compat with the bot intake endpoint and the run-start hook ----
def create(case_id: str, brand: str = "", origin: str = "", note: str = "",
           submitter_id: str = "") -> dict:
    """Thin idempotent create used by POST /api/intake and the run-start hook."""
    return create_full({"case_id": case_id, "brand": brand, "location": origin,
                        "notes_text": note, "submitter_id": submitter_id})


def mark_analyzed(case_id: str) -> None:
    """A finished run reached the case store: stamp analyzed_at, advance the
    pipeline out of intake, move New -> In Review."""
    with _LOCK:
        rows = _read_all()
        changed = False
        for r in rows:
            if r.get("case_id") == case_id:
                if not r.get("analyzed_at"):
                    r["analyzed_at"] = _now()
                if r.get("status") in ("New", "queued"):
                    r["status"] = "In Review"
                if int(r.get("stage") or 1) < 5:
                    r["stage"] = 5
                changed = True
        if changed:
            _write_all(rows)
