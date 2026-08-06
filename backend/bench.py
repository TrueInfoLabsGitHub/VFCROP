"""Offline benchmark harness — run the SAME labeled cases through every engine
and emit one comparison sheet (accuracy vs ground truth + cost + latency).

This is the automated counterpart to the in-app "Compare all" view: instead of
clicking through 50 cases, point it at a labeled dataset and it produces
per-run and per-engine summary CSVs.

No mock: with ALLOW_MOCK=0 (default) a missing key or a failed call is recorded
as a real error for that (case, engine), never a fabricated score.

--------------------------------------------------------------------------
Dataset format — a JSONL file, one case per line:

  {"case_id": "VF-2026-0412", "brand": "TNF", "label": "counterfeit",
   "suspect_images": ["cases/0412/front.jpg", "cases/0412/back.jpg"],
   "reference_images": ["refs/nuptse/1.jpg"],          # optional
   "upc_image": "cases/0412/upc.jpg"}                   # optional

  label must be "authentic" or "counterfeit". Image paths are relative to
  --images-dir (defaults to the folder containing the cases file).

Run:
  cd backend
  python bench.py --cases ../bench/cases.jsonl --providers openai
--------------------------------------------------------------------------
"""
import argparse
import base64
import csv
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

from graph import DIMENSIONS, WEIGHTS, _VERDICT_LABEL, _band
from pricing import price_usage
from providers import run_dimension_agent, run_upc_tool, run_verdict

PROVIDERS = ("openai",)          # Gemini and Kimi removed 2026-08-06


def _b64(path, base):
    full = path if os.path.isabs(path) else os.path.join(base, path)
    with open(full, "rb") as f:
        return base64.b64encode(f.read()).decode()


def _cost_of(usages):
    return round(sum(u.get("cost", price_usage(u["model"], u["tokens_in"], u["tokens_out"]))
                     for u in usages), 6)


def run_case_provider(case, provider, base):
    """Run one case end-to-end on one engine. Mirrors the graph's aggregate +
    verdict logic so we can inject per-case reference images directly."""
    t0 = time.time()
    suspect = [_b64(p, base) for p in case.get("suspect_images", [])]
    refs = [_b64(p, base) for p in case.get("reference_images", [])][:2]
    upc_img = case.get("upc_image")
    upc_b64 = _b64(upc_img, base) if upc_img else ""
    brand, cid = case.get("brand", "TNF"), case["case_id"]

    # 5 dimension agents (concurrent, like the app), + UPC tool.
    usages, dims = [], []

    def one_dim(dim):
        return run_dimension_agent(dim, brand, cid, suspect, refs, provider)

    with ThreadPoolExecutor(max_workers=len(DIMENSIONS)) as ex:
        for res, usage in ex.map(one_dim, DIMENSIONS):
            dims.append(res)
            usages.append(usage)
    upc_res, upc_usage = run_upc_tool(brand, cid, upc_b64, provider)
    usages.append(upc_usage)

    # composite (weighted) — same rule as graph.aggregate_node
    by = {d["dimension"]: d for d in dims}
    num = den = 0.0
    for dim, w in WEIGHTS.items():
        d = by.get(dim)
        if d and d["score"] is not None:
            num += d["score"] * w
            den += w
    score = round(num / den) if den else None
    if upc_res.get("status") in ("nomatch", "mismatch") and score is not None:
        score = min(100, score + 6)
    band = _band(score)
    composite = {"score": score, "band": band, "verdict_label": _VERDICT_LABEL[band]}

    verdict, vusages = run_verdict(provider, brand, composite, dims, upc_res)
    usages += vusages

    predicted = "counterfeit" if band != "authentic" else "authentic"
    return {
        "score": score, "band": band, "predicted": predicted,
        "dims": {d["dimension"]: d["score"] for d in dims},
        "upc_status": upc_res.get("status", ""),
        "verifier": "confirmed" if verdict.get("verifier_confirmed") else "refuted",
        "cost_usd": _cost_of(usages),
        "tokens": sum(u["tokens_in"] + u["tokens_out"] for u in usages),
        "latency_s": round(time.time() - t0, 1),
    }


def main():
    ap = argparse.ArgumentParser(description="VERITAS engine benchmark")
    ap.add_argument("--cases", required=True, help="path to cases .jsonl")
    ap.add_argument("--images-dir", default=None, help="base dir for image paths (default: cases file dir)")
    ap.add_argument("--providers", default="openai")
    ap.add_argument("--baseline", default="openai", help="engine to measure agreement against")
    ap.add_argument("--out", default=None, help="output dir (default: alongside cases file)")
    args = ap.parse_args()

    base = args.images_dir or os.path.dirname(os.path.abspath(args.cases))
    out_dir = args.out or os.path.dirname(os.path.abspath(args.cases))
    os.makedirs(out_dir, exist_ok=True)
    provs = [p for p in args.providers.split(",") if p.strip() in PROVIDERS]

    cases = []
    with open(args.cases, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    print(f"{len(cases)} cases × {len(provs)} engines = {len(cases) * len(provs)} runs\n")

    runs = []                          # per (case, provider)
    for case in cases:
        cid, label = case["case_id"], case.get("label", "")
        # engines run concurrently on identical inputs
        def run(p):
            try:
                r = run_case_provider(case, p, base)
                return p, {"ok": True, **r}
            except Exception as e:
                return p, {"ok": False, "error": str(e)}
        results = {}
        with ThreadPoolExecutor(max_workers=len(provs)) as ex:
            for p, r in ex.map(run, provs):
                results[p] = r
        for p in provs:
            r = results[p]
            row = {"case": cid, "label": label, "provider": p, "ok": r["ok"],
                   "error": r.get("error", "")}
            if r["ok"]:
                row.update({
                    "score": r["score"], "band": r["band"], "predicted": r["predicted"],
                    "correct": (r["predicted"] == label) if label else "",
                    **{d: r["dims"].get(d, "") for d in DIMENSIONS},
                    "upc_status": r["upc_status"], "verifier": r["verifier"],
                    "cost_usd": r["cost_usd"], "latency_s": r["latency_s"], "tokens": r["tokens"],
                })
            runs.append(row)
            tag = "ok" if r["ok"] else "ERR"
            print(f"  {cid:16} {p:8} {tag}  " +
                  (f"score={r['score']} pred={r['predicted']} label={label} cost=${r['cost_usd']:.4f}"
                   if r["ok"] else r["error"][:80]))

    # ---- write per-run CSV ----
    run_cols = ["case", "label", "provider", "ok", "score", "band", "predicted", "correct",
                *DIMENSIONS, "upc_status", "verifier", "cost_usd", "latency_s", "tokens", "error"]
    runs_path = os.path.join(out_dir, "bench_runs.csv")
    with open(runs_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=run_cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(runs)

    # ---- per-engine summary ----
    base_pred = {r["case"]: r.get("predicted") for r in runs
                 if r["provider"] == args.baseline and r["ok"]}
    summary = []
    for p in provs:
        pr = [r for r in runs if r["provider"] == p]
        ok = [r for r in pr if r["ok"]]
        labeled = [r for r in ok if r["label"]]
        correct = [r for r in labeled if r["correct"] is True]
        fp = [r for r in labeled if r["label"] == "authentic" and r["predicted"] == "counterfeit"]
        fn = [r for r in labeled if r["label"] == "counterfeit" and r["predicted"] == "authentic"]
        agree = [r for r in ok if base_pred.get(r["case"]) is not None
                 and r["predicted"] == base_pred[r["case"]]]
        agree_den = [r for r in ok if base_pred.get(r["case"]) is not None]
        costs = [r["cost_usd"] for r in ok]
        lats = [r["latency_s"] for r in ok]
        summary.append({
            "provider": p, "runs": len(pr), "errors": len(pr) - len(ok),
            "accuracy": f"{len(correct)}/{len(labeled)}" + (f" ({100*len(correct)/len(labeled):.0f}%)" if labeled else ""),
            "false_pos": len(fp), "false_neg": len(fn),
            f"agree_vs_{args.baseline}": (f"{len(agree)}/{len(agree_den)}" if agree_den else "-"),
            "mean_cost": round(sum(costs) / len(costs), 5) if costs else 0,
            "total_cost": round(sum(costs), 4),
            "mean_latency_s": round(sum(lats) / len(lats), 1) if lats else 0,
        })
    sum_path = os.path.join(out_dir, "bench_summary.csv")
    with open(sum_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(summary[0].keys()))
        w.writeheader()
        w.writerows(summary)

    print("\n=== summary ===")
    for s in summary:
        print("  " + "  ".join(f"{k}={v}" for k, v in s.items()))
    print(f"\nwrote {runs_path}\n      {sum_path}")


if __name__ == "__main__":
    main()
