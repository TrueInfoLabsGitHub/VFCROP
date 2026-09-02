"""Replay a labelled corpus of stored runs through the CURRENT decision ladder.

    python _replay_labelled.py _purged_runs_20260806-084941.json --label counterfeit

Why this exists: every band this system has shipped was chosen by argument
rather than by measurement, and twice the argument was wrong in a way a single
replay would have exposed. The bands are not opinions once this runs.

The corpus is a stored run export — either {"records": [...]} or a bare list.
`--label` states the ground truth for every record in the file; pass a second
file with `--label authentic` to measure the other side.

Two numbers matter and they trade off against each other:

  FALSE CLEARANCE   a counterfeit released as genuine.  Must be zero.
  FALSE REJECTION   a genuine item rejected.            The price you pay.

You cannot read one without the other. The reband to a conviction floor of 11
improved false clearance by 1.6 points and was never measured against false
rejection at all — where it rejected genuine stock for creasing.

NOTE ON LEGACY RECORDS. Runs written before the state model store `status` but
no `state`, per-dimension `confidence` or `internal_coverage`. Those are
reconstructed here (scored -> MEASURED at confidence 0.75 and full internal
coverage, everything else non-contributing), which is deliberately GENEROUS to
the original verdict: it gives old measurements every chance to clear. Absolute
rates on such a corpus are indicative; comparisons between settings are sound.
"""
import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import scoring                                          # noqa: E402
from scoring import Dim, DimState                       # noqa: E402

_STATE_FOR_STATUS = {
    "scored": DimState.MEASURED,
    "estimated": DimState.ESTIMATED,
    "abstain": DimState.NOT_ASSESSABLE,
    "error": DimState.FAILED,
    "not_applicable": DimState.NOT_APPLICABLE,
}


def _dims_from(record):
    out = []
    for name in scoring.DIMENSION_NAMES:
        v = (record.get("dimensions") or {}).get(name)
        if not isinstance(v, dict):
            continue
        state = v.get("state")
        if state not in vars(DimState).values():
            state = _STATE_FOR_STATUS.get(v.get("status"), DimState.ESTIMATED)
        measured = state == DimState.MEASURED
        conf = v.get("confidence")
        cov = v.get("internal_coverage")
        out.append(Dim(name, v.get("score"), state,
                       conf if conf is not None else (0.75 if measured else 0.30),
                       cov if cov is not None else (1.0 if measured else 0.0),
                       v.get("finding", ""),
                       v.get("deterministic", False)))
    return out


def load(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data["records"] if isinstance(data, dict) and "records" in data else data


def replay(records, truth):
    lanes = collections.Counter()
    verdicts = collections.Counter()
    wrong = []
    coverage_hist = collections.Counter()
    for r in records:
        dims = _dims_from(r)
        res = scoring.decide(dims, category=scoring.normalise_category(r.get("product")))
        lanes[res["lane"]] += 1
        verdicts[res["verdict_label"]] += 1
        coverage_hist[sum(1 for d in dims if d.state == DimState.MEASURED)] += 1
        bad = (truth == "counterfeit" and res["lane"] == "CLEARED") or \
              (truth == "authentic" and res["lane"] == "REJECTED")
        if bad:
            wrong.append((r, res, dims))
    return lanes, verdicts, wrong, coverage_hist


def audit(wrong, truth):
    """The escape audit: WHICH rung produced each wrong call, and through which
    gate it walked. Tightening a threshold without this is guessing — the
    2.8%-at-band-11 result exists because the escapes scored in single digits,
    where no band can reach them. This names the gate that actually let each
    one through, so the next tightening moves the lever the escapes used."""
    bad_name = "FALSE CLEARANCE" if truth == "counterfeit" else "FALSE REJECTION"
    by_rule = collections.Counter()
    print(f"\n  ESCAPE AUDIT — every {bad_name.lower()}, by the rung that produced it")
    print(f"  {'case':<22}{'rule':<6}{'comp':>5}{'cov':>6}{'ccov':>6}  dimension states")
    for r, res, dims in wrong:
        rule = res.get("rule") or "?"
        by_rule[rule] += 1
        ccov = scoring.clearance_coverage(dims)
        states = " ".join(
            f"{d.name[:3]}:{'-' if d.score is None else int(d.score)}"
            f"/{(d.state or '?')[:4]}@{d.confidence:.0%}"
            for d in dims)
        print(f"  {(r.get('case_id') or '?'):<22}{rule:<6}"
              f"{'-' if res.get('score') is None else int(res['score']):>5}"
              f"{res.get('coverage_pct', 0):>6.0%}{ccov:>6.0%}  {states}")
    print(f"\n  by clearing rung ({len(wrong)} total):")
    for rule, n in by_rule.most_common():
        sentence = scoring.RULES.get(rule, "")
        print(f"    {rule:<6}{n:>4}   {sentence}")
    print(
        "\n  reading it: a low-composite escape (comp < 11) was never MEASURED —\n"
        "  no band catches it; the lever is the gate on the rung above. Cleared at\n"
        "  ~75% ccov -> raise COVERAGE_FOR_LIKELY_AUTH. Cleared with exactly 2\n"
        "  forensic dims -> raise MIN_FORENSIC_DIMS_FOR_CLEARANCE. Cleared at ~60%\n"
        "  confidence -> raise MIN_CONFIDENCE_FOR_CLEARANCE. Cleared without a UPC\n"
        "  -> set LIKELY_AUTH_REQUIRES_UPC_MATCH. Re-run BOTH corpora after every\n"
        "  change — a tightening that looks free on an all-counterfeit file is\n"
        "  only unmeasured, not free.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus")
    ap.add_argument("--label", choices=("counterfeit", "authentic"), required=True)
    ap.add_argument("--show", type=int, default=10, help="how many wrong calls to print")
    ap.add_argument("--audit", action="store_true",
                    help="per-escape breakdown: rule, composite, coverages, dim states")
    args = ap.parse_args()

    records = load(args.corpus)
    lanes, verdicts, wrong, cov = replay(records, args.label)
    total = sum(lanes.values()) or 1

    bad_lane = "CLEARED" if args.label == "counterfeit" else "REJECTED"
    bad_name = "FALSE CLEARANCE" if args.label == "counterfeit" else "FALSE REJECTION"

    print(f"\n{len(records)} runs, all labelled {args.label.upper()}")
    print(f"bands: authentic<={scoring.BAND_AUTHENTIC} likely<={scoring.BAND_LIKELY_AUTH} "
          f"convict>={scoring.DIM_COUNTERFEIT}  |  coverage_for_likely_auth="
          f"{scoring.COVERAGE_FOR_LIKELY_AUTH}%  forensic_dims_required="
          f"{scoring.MIN_FORENSIC_DIMS_FOR_CLEARANCE}\n")

    for lane in ("REJECTED", "REVIEW", "CLEARED"):
        print(f"  {lane:<10} {lanes[lane]:>5}  {lanes[lane] / total:>6.1%}")
    print(f"\n  {bad_name}: {len(wrong)} / {total}  ({len(wrong) / total:.1%})")

    print("\n  verdicts:")
    for k, v in verdicts.most_common():
        print(f"    {k:<48} {v:>5}")

    print("\n  dimensions actually MEASURED per run:")
    for n in sorted(cov):
        print(f"    {n} of 5   {cov[n]:>5}  {cov[n] / total:>6.1%}")

    if wrong:
        print(f"\n  the {bad_name.lower()}s (showing {min(args.show, len(wrong))}):")
        for r, res, dims in wrong[:args.show]:
            m = [(d.name, int(d.score)) for d in dims
                 if d.state == DimState.MEASURED and d.score is not None]
            print(f"    {(r.get('case_id') or '?'):<24} {res['verdict_label']:<20} measured={m}")
        if args.audit:
            audit(wrong, args.label)

    return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main())
