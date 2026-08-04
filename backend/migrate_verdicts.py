"""Dry-run migration report: which stored runs change verdict under the ladder.

Read-only. It re-scores every saved run through scoring.decide() using the
dimension states that run recorded, and prints the old -> new matrix. Nothing is
written back — a stored run is a record of what the system said at the time, and
rewriting history would destroy the audit trail this service exists to provide.

Run:  python backend/migrate_verdicts.py            (needs SUPABASE_URL/KEY)
      python backend/migrate_verdicts.py --detail   (one line per changed run)

Expect a large Insufficient Evidence column. That is the point rather than a
bug: runs saved before this change carry no per-dimension `state`, so every
dimension in them loads as ESTIMATED, and an item with nothing measured cannot
be cleared. Those runs were cleared on evidence that was never gathered; the new
answer for them is "we do not know", and the shot list says what would settle it.
"""
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import scoring                                                  # noqa: E402
import supa                                                     # noqa: E402
from references import DIMENSIONS                               # noqa: E402


def rescore(rec):
    """Re-run one stored record through the ladder. Returns the new composite."""
    dims = rec.get("dimensions") or {}
    objs = [scoring.Dim.from_record(name, dims.get(name)) for name in DIMENSIONS]
    return scoring.decide(
        objs,
        category=scoring.normalise_category(rec.get("product", "")),
        upc_status=(rec.get("upc") or {}).get("status") or "not_provided",
        label_hard_fail=bool((rec.get("label_validation") or {}).get("hard_fail")),
        pairing_ok=rec.get("pairing") != "mismatch",
        run_ok=rec.get("band") != "error",
        run_error=rec.get("error", ""),
    )


def main(detail=False):
    if not supa.available():
        print("Supabase is not configured — set SUPABASE_URL and SUPABASE_KEY.")
        return 1
    runs = supa.list_runs()
    print(f"{len(runs)} stored runs\n")

    matrix = collections.Counter()
    lanes = collections.Counter()
    changed = []
    for rec in runs:
        old = rec.get("verdict") or "(none)"
        new = rescore(rec)
        matrix[(old, new["verdict_label"])] += 1
        lanes[new["lane"]] += 1
        if old != new["verdict_label"]:
            changed.append((rec.get("case_id", ""), rec.get("engine", ""), old, new))

    width = max((len(o) for o, _ in matrix), default=10)
    print(f"{'OLD':<{width}}  ->  NEW                                   n")
    print("-" * (width + 48))
    for (old, new), n in sorted(matrix.items(), key=lambda kv: -kv[1]):
        print(f"{old:<{width}}  ->  {new:<36} {n:>3}")

    print(f"\nchanged: {len(changed)} of {len(runs)}")
    print("new routing:", ", ".join(f"{k} {v}" for k, v in sorted(lanes.items())))

    if detail:
        print("\ncase            engine            old -> new")
        for cid, engine, old, new in changed:
            print(f"{cid:<15} {engine:<17} {old} -> {new['verdict_label']}"
                  f"   ({new['reason']})")
    return 0


if __name__ == "__main__":
    sys.exit(main(detail="--detail" in sys.argv))
