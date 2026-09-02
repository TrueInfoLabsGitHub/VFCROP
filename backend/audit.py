"""Server-side action log — the audit trail behind 'all actions are logged'.

Append-only JSONL under data/, so it works with zero configuration and cannot
be edited in place by the API (there is no update or delete). Moving it into a
database later is a storage swap; the shape of a row is the contract:

    {ts, user, action, detail}

Actions are dot-namespaced verbs the frontend already emits through its single
audit() hook: RUN.STARTED, RUN.COMPLETED, RUN.FAILED, RUN.REJECTED,
VERDICT.CONFIRMED, CASE.ESCALATED, CASES.EXPORTED, REPORT.EXPORTED.
Unknown actions are accepted (the log records what happened, it does not
gatekeep vocabulary) but are length-bounded and shape-checked.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDIT_PATH = os.environ.get("AUDIT_PATH", os.path.join(_ROOT, "data", "audit_log.jsonl"))

_LOCK = threading.Lock()
_ACTION_RX = re.compile(r"^[A-Z0-9_.\-]{2,40}$")


def record(action: str, detail: str = "", user: str = "") -> dict:
    action = (action or "").strip().upper()
    if not _ACTION_RX.match(action):
        raise ValueError("action must be a short dot-namespaced verb, e.g. RUN.STARTED")
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "user": (user or "").strip()[:80] or "analyst",
           "action": action,
           "detail": (detail or "").strip()[:400]}
    with _LOCK:
        os.makedirs(os.path.dirname(AUDIT_PATH), exist_ok=True)
        with open(AUDIT_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def recent(limit: int = 50) -> list[dict]:
    """Newest first. Reads the tail without loading an unbounded file."""
    limit = max(1, min(int(limit or 50), 500))
    if not os.path.exists(AUDIT_PATH):
        return []
    with open(AUDIT_PATH, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - 256 * 1024))
        chunk = f.read().decode("utf-8", "replace")
    rows = []
    for line in chunk.splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return list(reversed(rows[-limit:]))
