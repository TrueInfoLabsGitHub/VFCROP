"""LangGraph orchestration for VERITAS counterfeit analysis.

Flow:  intake -> [5 dimension agents + UPC tool] (parallel) -> aggregate
              -> verdict (synth + verify) -> report -> END

The 5 dimension agents and the UPC tool run as parallel branches (one
LangGraph superstep). They append to reducer-backed state keys so their
concurrent writes merge instead of clobbering. `aggregate` runs only after all
branches complete (fan-in), then the verdict and report nodes run in sequence.
"""
import operator
import time
from functools import partial
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from pricing import price_usage
from providers import _cfg, run_dimension_agent, run_upc_tool, run_verdict
from references import DIMENSIONS, load_ref_b64, select_references
from rimage import fetch_authentic_references

# Per-dimension weights for the composite (sum = 1.0).
WEIGHTS = {"Logo": 0.22, "Stitching": 0.16, "Hardware": 0.22, "Label": 0.22, "Material": 0.18}

# Tiny labeled set so the Run Report can show accuracy vs ground truth on
# known eval cases (live cases simply omit the "vs ground truth" tile).
GROUND_TRUTH = {"VF-2026-0412": "counterfeit", "VF-2026-0402": "authentic"}


class RunState(TypedDict, total=False):
    case_id: str
    brand: str
    provider: str
    ref_source: str
    suspect_images: list
    upc_image: str
    references: dict
    fetched_refs: list
    fetched_meta: dict
    dimension_results: Annotated[list, operator.add]
    usage_log: Annotated[list, operator.add]
    upc_result: dict
    composite: dict
    verdict: dict
    report: dict
    started_at: float


def _band(score):
    if score is None:
        return "neutral"
    if score <= 30:
        return "authentic"
    if score <= 60:
        return "caution"
    return "counterfeit"


_VERDICT_LABEL = {
    "authentic": "Likely Authentic",
    "caution": "Inconclusive",
    "counterfeit": "Suspected Counterfeit",
}


# ---- nodes ----------------------------------------------------------------
def intake_node(state: RunState) -> dict:
    out = {"references": select_references(state["brand"]), "started_at": time.time(),
           "fetched_refs": [], "fetched_meta": {"used": False}}
    if state.get("ref_source") == "google":
        suspect = next((b for b in (state.get("suspect_images") or []) if b), None)
        cfg = _cfg(state.get("provider", "openai"))
        refs, meta, usage = fetch_authentic_references(suspect, state["brand"], cfg, keep=5)
        out["fetched_refs"] = refs
        out["fetched_meta"] = meta
        if usage:
            out["usage_log"] = usage
    return out


def dimension_node(dim: str, state: RunState) -> dict:
    fetched = state.get("fetched_refs") or []
    if fetched:
        ref_b64s = fetched[:2]                       # Google-fetched authentic shots
    else:
        b = load_ref_b64(state["references"].get(dim))   # local data/ reference
        ref_b64s = [b] if b else []
    result, usage = run_dimension_agent(
        dim, state["brand"], state["case_id"], state.get("suspect_images", []), ref_b64s,
        state.get("provider", "openai"))
    return {"dimension_results": [result], "usage_log": [usage]}


def upc_node(state: RunState) -> dict:
    result, usage = run_upc_tool(state["brand"], state["case_id"], state.get("upc_image", ""),
                                 state.get("provider", "openai"))
    return {"upc_result": result, "usage_log": [usage]}


def aggregate_node(state: RunState) -> dict:
    dims = state["dimension_results"]
    by = {d["dimension"]: d for d in dims}
    num = den = 0.0
    for dim, w in WEIGHTS.items():
        d = by.get(dim)
        if d and d["score"] is not None:
            num += d["score"] * w
            den += w
    score = round(num / den) if den else None
    # A failed/mismatched UPC master-record lookup nudges the composite up.
    if state.get("upc_result", {}).get("status") in ("nomatch", "mismatch") and score is not None:
        score = min(100, score + 6)
    band = _band(score)
    return {"composite": {"score": score, "band": band, "verdict_label": _VERDICT_LABEL[band]}}


def verdict_node(state: RunState) -> dict:
    verdict, usages = run_verdict(
        state.get("provider", "openai"), state["brand"], state["composite"],
        state["dimension_results"], state["upc_result"])
    return {"verdict": verdict, "usage_log": usages}


def report_node(state: RunState) -> dict:
    rows, total_in, total_out, total_cost, serial_ms = [], 0, 0, 0.0, 0
    # Stable display order: reverse-image pre-steps, dimensions, UPC, verdict, verify.
    order = {"Reverse image": -2, "Curate refs": -1,
             **{d: i for i, d in enumerate(DIMENSIONS)},
             "UPC / Tag": 10, "Verdict synth.": 11, "Verify": 12}
    for u in sorted(state["usage_log"], key=lambda x: order.get(x["agent"], 99)):
        # honor a preset cost (e.g. SerpAPI per-search); else price from tokens
        cost = u["cost"] if "cost" in u else price_usage(u["model"], u["tokens_in"], u["tokens_out"])
        total_in += u["tokens_in"]
        total_out += u["tokens_out"]
        total_cost += cost
        serial_ms += u["latency_ms"]
        rows.append({**u, "cost": round(cost, 6)})

    # Aggregator row (deterministic, no model spend).
    rows.append({"agent": "Aggregator", "model": "-", "tokens_in": 0, "tokens_out": 0,
                 "latency_ms": 100, "cost": 0.0})

    # Wall-clock = reverse-image pre-steps (sequential) + parallel band + tail.
    pre = sum(u["latency_ms"] for u in state["usage_log"]
              if u["agent"] in ("Reverse image", "Curate refs"))
    parallel = [u["latency_ms"] for u in state["usage_log"]
                if u["agent"] in DIMENSIONS or u["agent"] == "UPC / Tag"]
    tail = [u["latency_ms"] for u in state["usage_log"]
            if u["agent"] in ("Verdict synth.", "Verify")]
    wall_ms = pre + (max(parallel) if parallel else 0) + 100 + sum(tail)

    evals = _evals(state)
    return {"report": {
        "rows": rows,
        "totals": {"tokens_in": total_in, "tokens_out": total_out,
                   "tokens": total_in + total_out, "cost": round(total_cost, 4),
                   "wall_ms": wall_ms, "serial_ms": serial_ms + 100},
        "evals": evals,
    }}


def _evals(state: RunState) -> dict:
    dims = state["dimension_results"]
    confs = [d["confidence"] for d in dims if d.get("confidence") is not None]
    abst = sum(1 for d in dims if d["status"] == "abstain")
    v = state["verdict"]
    out = {
        "confidence": round(sum(confs) / len(confs), 2) if confs else None,
        "verifier": "confirmed" if v["verifier_confirmed"] else "refuted",
        "verifier_votes": v["verifier_votes"],
        "abstentions": f"{abst} / {len(DIMENSIONS)}",
    }
    truth = GROUND_TRUTH.get(state["case_id"])
    if truth:
        predicted = "counterfeit" if state["composite"]["band"] != "authentic" else "authentic"
        out["ground_truth"] = truth
        out["correct"] = predicted == truth
    return out


# ---- graph wiring ---------------------------------------------------------
def build_graph():
    g = StateGraph(RunState)
    g.add_node("intake", intake_node)
    for dim in DIMENSIONS:
        g.add_node(f"dim_{dim}", partial(dimension_node, dim))
    g.add_node("upc", upc_node)
    g.add_node("aggregate", aggregate_node)
    g.add_node("synthesize", verdict_node)   # node name must differ from state key
    g.add_node("build_report", report_node)

    g.add_edge(START, "intake")
    for dim in DIMENSIONS:                 # fan-out
        g.add_edge("intake", f"dim_{dim}")
        g.add_edge(f"dim_{dim}", "aggregate")  # fan-in
    g.add_edge("intake", "upc")
    g.add_edge("upc", "aggregate")
    g.add_edge("aggregate", "synthesize")
    g.add_edge("synthesize", "build_report")
    g.add_edge("build_report", END)
    return g.compile()
