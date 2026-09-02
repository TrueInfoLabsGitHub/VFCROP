"""Case intake register — the queue of cases OPENED but not yet analyzed.

A case exists from the moment somebody decides to look at an item (a flagged
listing, a customs referral), not from the moment a run finishes. This module
is that register. Storage is a local JSONL under data/ so it works end to end
with zero configuration; the interface is deliberately table-shaped so moving
it into Supabase later is a storage swap, not a redesign.

Status model, and who moves it:
    queued    -> created here (POST /api/intake, or implicitly when a run starts)
    analyzed  -> stamped by the export-save hook the moment a finished run for
                 this case number lands in the case store
There is no 'rejected' status here: pre-flight rejections live in their own
log (preflight.py) because they are submissions, not cases.
"""
from __future__ import annotations

import json
import os
import threading
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INTAKE_PATH = os.environ.get("INTAKE_PATH", os.path.join(_ROOT, "data", "intake_cases.jsonl"))

_LOCK = threading.Lock()


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


def create(case_id: str, brand: str = "", origin: str = "", note: str = "",
           submitter_id: str = "") -> dict:
    """Idempotent: creating an id that already exists returns the existing row
    unchanged — a run starting twice must not duplicate the case."""
    case_id = (case_id or "").strip()
    if not case_id:
        raise ValueError("case_id is required")
    with _LOCK:
        rows = _read_all()
        for r in rows:
            if r.get("case_id") == case_id:
                return r
        row = {"case_id": case_id, "brand": (brand or "").strip(),
               "origin": (origin or "").strip(), "note": (note or "").strip()[:400],
               "submitter_id": (submitter_id or "").strip(),
               "status": "queued", "opened_at": _now(), "analyzed_at": ""}
        rows.append(row)
        _write_all(rows)
        return row


def mark_analyzed(case_id: str) -> None:
    """Called by the export-save hook: the case's run reached the case store."""
    case_id = (case_id or "").strip()
    if not case_id:
        return
    with _LOCK:
        rows = _read_all()
        changed = False
        for r in rows:
            if r.get("case_id") == case_id and r.get("status") != "analyzed":
                r["status"] = "analyzed"
                r["analyzed_at"] = _now()
                changed = True
        if changed:
            _write_all(rows)


def list_cases(status: str = "") -> list[dict]:
    """Newest first; optionally filtered by status ('queued' / 'analyzed')."""
    rows = _read_all()
    if status:
        rows = [r for r in rows if r.get("status") == status]
    return sorted(rows, key=lambda r: r.get("opened_at", ""), reverse=True)
