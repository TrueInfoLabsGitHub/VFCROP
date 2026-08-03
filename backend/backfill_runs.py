"""One-off backfill: fill null dimension scores in saved runs.

Runs saved before ALWAYS_SCORE stored `{"score": null, "status": "abstain"}` for
any dimension the model could not assess, which shows as n/a in the export. This
re-runs only the affected cases through the live pipeline (ALWAYS_SCORE on) so
every cell gets a number derived from that case's own photos, rather than a
constant nothing ever looked at.

IMPORTANT LIMITATION
Only 150px thumbnails are stored per run — the original full-resolution suspect
photos are not kept. The re-run therefore sees a much smaller image than the
first run did, and will mostly land on estimates rather than measurements. That
is the honest ceiling here: the numbers will be the model's impression of that
specific item, marked status="estimated", not recovered measurements.

Safety: dry-run by default, writes a JSON backup of every record it touches, and
only ever fills dimensions that are currently null — an existing number is never
overwritten.

Usage
  python backend/backfill_runs.py                     # dry run, report only
  python backend/backfill_runs.py --apply             # write changes
  python backend/backfill_runs.py --apply --limit 5   # do a few first
"""
import argparse
import copy
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import graph                                                    # noqa: E402
import supa                                                     # noqa: E402
from references import DIMENSIONS                                # noqa: E402

DIMS = list(DIMENSIONS)


# ---- pure helpers (unit-tested; no network) --------------------------------
def null_dims(rec):
    """Dimension names whose stored score is null."""
    dims = rec.get("dimensions") or {}
    out = []
    for d in DIMS:
        x = dims.get(d)
        if isinstance(x, dict) and x.get("score") is None:
            out.append(d)
    return out


def needs_backfill(rec):
    return bool(null_dims(rec))


def provider_for(engine_label):
    """Stored engine label -> provider key understood by the graph."""
    e = (engine_label or "").strip().lower()
    if "gemini" in e:
        return "gemini"
    if "kimi" in e or "moonshot" in e:
        return "kimi"
    return "openai"


def build_state(rec):
    """Reconstruct the graph inputs from a stored record."""
    imgs = [b for b in (rec.get("suspect_thumbs") or []) if b]
    return {
        "case_id": rec.get("case_id") or rec.get("id") or "",
        "brand": rec.get("brand") or "TNF",
        "provider": provider_for(rec.get("engine")),
        "ref_source": "local",
        "product_id": "",
        "suspect_images": imgs,
        "upc_image": "",
    }


def merge(rec, fresh_dims, composite):
    """Fill ONLY the null dimensions, then bring the summary fields back into
    agreement with them. Original timestamps, cost and latency are preserved —
    they describe the original run, not this backfill."""
    out = copy.deepcopy(rec)
    dims = out.setdefault("dimensions", {})
    filled = []
    for name in null_dims(rec):
        src = fresh_dims.get(name)
        if not src or src.get("score") is None:
            continue
        dims[name] = {
            "score": src.get("score"),
            "finding": src.get("finding") or "",
            "status": src.get("status") or "estimated",
        }
        filled.append(name)
    if not filled:
        return out, []
    if composite:
        out["score"] = composite.get("score")
        out["band"] = composite.get("band", out.get("band", ""))
        out["verdict"] = composite.get("verdict_label", out.get("verdict", ""))
        out["assessed"] = (composite.get("coverage") or {}).get("assessed")
    out["backfilled_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    out["backfill_note"] = (
        "Null dimension scores filled by re-running the stored 150px thumbnails "
        "with ALWAYS_SCORE. Filled values are estimates, not measurements; "
        "'assessed' still counts only evidence-backed dimensions."
    )
    return out, filled


# ---- runner ----------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="write the changes (default is a dry run)")
    ap.add_argument("--limit", type=int, default=0, help="process at most N runs")
    ap.add_argument("--backup", default="backfill_backup.json",
                    help="where to write the pre-change copies")
    args = ap.parse_args(argv)

    if not supa.available():
        print("Supabase is not configured. Set SUPABASE_URL and "
              "SUPABASE_SERVICE_KEY (backend/.env or the environment).")
        return 2

    runs = supa.list_runs()
    todo = [r for r in runs if needs_backfill(r)]
    if args.limit:
        todo = todo[:args.limit]

    print(f"{len(runs)} saved runs, {len([r for r in runs if needs_backfill(r)])} "
          f"with null dimensions.")
    if not todo:
        print("Nothing to do.")
        return 0
    print(f"Processing {len(todo)}"
          f"{' (DRY RUN — no writes)' if not args.apply else ''}\n")

    if args.apply:
        with open(args.backup, "w", encoding="utf-8") as f:
            json.dump(todo, f, indent=1)
        print(f"Backup of {len(todo)} original records -> {args.backup}\n")

    g = graph.build_graph()
    changed = failed = 0
    for i, rec in enumerate(todo, 1):
        rid = rec.get("id")
        gaps = null_dims(rec)
        label = f"[{i}/{len(todo)}] {rec.get('case_id') or rid} ({rec.get('engine')})"
        if not (rec.get("suspect_thumbs") or []):
            print(f"{label}: SKIP — no stored suspect image to re-run")
            continue
        try:
            out = g.invoke(build_state(rec))
        except Exception as e:
            failed += 1
            print(f"{label}: FAILED — {e}")
            continue
        fresh = {d["dimension"]: d for d in out.get("dimension_results", [])}
        merged, filled = merge(rec, fresh, out.get("composite") or {})
        if not filled:
            print(f"{label}: no fill produced for {gaps}")
            continue
        vals = ", ".join(f"{d}={merged['dimensions'][d]['score']}" for d in filled)
        print(f"{label}: {vals}  score {rec.get('score')} -> {merged.get('score')}")
        if args.apply:
            supa.save_run(merged)
        changed += 1

    print(f"\n{changed} run(s) {'updated' if args.apply else 'would be updated'}"
          f"{f', {failed} failed' if failed else ''}.")
    if not args.apply:
        print("Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
