"""Delete selected saved runs so a case can be re-tested.

Selection mirrors what you see in the exported workbook, so the numbers match
the '#' column on the "VERITAS analyses" sheet.

Deleting is irreversible, so this is dry-run by default: it prints the exact
rows it would remove, and only touches anything when you add --apply. Every
deleted record is written to a JSON backup first, so a mistake is recoverable
by re-saving from that file.

Usage
  python backend/delete_runs.py --list                  # show every case with its #
  python backend/delete_runs.py --case test1 test2      # dry run, by case id
  python backend/delete_runs.py --from-number 31        # dry run, # 31 and above
  python backend/delete_runs.py --from-number 31 --apply
"""
import argparse
import json
import os
import sys
import time

# Every other script that talks to Supabase from this machine does this first —
# the local TLS stack is intercepted (Avast), so the system trust store has to be
# injected or every request dies on CERTIFICATE_VERIFY_FAILED. This file was the
# one that did not, which made it look like the database was unreachable.
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:                       # not needed where TLS is not intercepted
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import supa                                                      # noqa: E402


def group_cases(runs):
    """Group runs into the case rows the export shows, preserving order.

    Mirrors exporter._build_analyses_sheet so the '#' printed here is the '#'
    in the workbook."""
    cases = {}
    for idx, rec in enumerate(runs):
        cid = rec.get("case_id") or ""
        key = cid or f"\x00nocase-{idx}"
        g = cases.setdefault(key, {"order": idx, "cid": cid, "records": []})
        g["records"].append(rec)
    ordered = sorted(cases.values(), key=lambda c: c["order"])
    for n, g in enumerate(ordered, start=1):
        g["number"] = n
    return ordered


def select(groups, case_ids=None, from_number=None, numbers=None):
    """Case groups matching any given selector. Case ids match case-insensitively."""
    wanted = {c.strip().lower() for c in (case_ids or []) if c.strip()}
    nums = set(numbers or [])
    out = []
    for g in groups:
        if wanted and (g["cid"] or "").strip().lower() in wanted:
            out.append(g)
        elif from_number is not None and g["number"] >= from_number:
            out.append(g)
        elif g["number"] in nums:
            out.append(g)
    return out


def describe(g):
    r = g["records"][0]
    engines = ", ".join(sorted({x.get("engine", "") for x in g["records"] if x.get("engine")}))
    return (f"#{g['number']:>3}  {g['cid'] or '(no case id)':<18} "
            f"{(r.get('product') or '')[:38]:<38} "
            f"{len(g['records'])} run(s) [{engines}]")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="list every case and exit")
    ap.add_argument("--case", nargs="*", default=[], help="case id(s) to delete")
    ap.add_argument("--number", nargs="*", type=int, default=[], help="case #(s) to delete")
    ap.add_argument("--from-number", type=int, default=None,
                    help="delete this case # and every one after it")
    ap.add_argument("--apply", action="store_true", help="actually delete (default: dry run)")
    ap.add_argument("--backup", default="deleted_runs_backup.json")
    args = ap.parse_args(argv)

    if not supa.available():
        print("Supabase is not configured. Set SUPABASE_URL and SUPABASE_SERVICE_KEY.")
        return 2

    runs = supa.list_runs()
    groups = group_cases(runs)
    print(f"{len(runs)} saved run(s) across {len(groups)} case(s).\n")

    if args.list or not (args.case or args.number or args.from_number is not None):
        for g in groups:
            print(describe(g))
        if not args.list:
            print("\nNothing selected. Choose with --case, --number or --from-number.")
        return 0

    picked = select(groups, args.case, args.from_number, args.number)
    if not picked:
        print("No case matched that selection. Run with --list to see what exists.")
        return 1

    records = [r for g in picked for r in g["records"]]
    print("WILL DELETE:" if args.apply else "WOULD DELETE (dry run):")
    for g in picked:
        print("  " + describe(g))
    print(f"\n{len(picked)} case(s), {len(records)} run record(s).")

    if not args.apply:
        print("\nNothing was deleted. Add --apply to go ahead.")
        return 0

    with open(args.backup, "w", encoding="utf-8") as f:
        json.dump({"deleted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "records": records}, f, indent=1)
    print(f"\nBackup of {len(records)} record(s) -> {args.backup}")

    supa.delete_runs([r.get("id") for r in records if r.get("id")])
    print(f"Deleted. {supa.runs_count()} run(s) remain.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
