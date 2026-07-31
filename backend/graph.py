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

import os

import supa
from pricing import price_usage
from providers import (_cfg, run_dimension_agent, run_label_identity,
                       run_pairing_check, run_upc_tool, run_verdict)
from references import DIMENSIONS, load_ref_b64, select_references
from rimage import fetch_authentic_references

# Per-dimension weights for the composite (sum = 1.0).
WEIGHTS = {"Logo": 0.22, "Stitching": 0.16, "Hardware": 0.22, "Label": 0.22, "Material": 0.18}

# A composite is only meaningful when enough of the item was actually assessed.
# Below this many scored dimensions the run returns REQUEST_RECAPTURE instead of
# a number — a score averaged over one or two dimensions reads as authoritative
# and is not.
MIN_ASSESSED_DIMS = max(1, int(os.environ.get("MIN_ASSESSED_DIMS", "3")))

# Label carries the deterministic identity evidence, so a run that could not read
# it is capped at 'caution' rather than being allowed to conclude counterfeit.
REQUIRE_LABEL_FOR_VERDICT = os.environ.get(
    "REQUIRE_LABEL_FOR_VERDICT", "1").strip().lower() in ("1", "true", "yes", "on")

# Tiny labeled set so the Run Report can show accuracy vs ground truth on
# known eval cases (live cases simply omit the "vs ground truth" tile).
GROUND_TRUTH = {"VF-2026-0412": "counterfeit", "VF-2026-0402": "authentic"}


class RunState(TypedDict, total=False):
    case_id: str
    brand: str
    provider: str
    ref_source: str
    product_id: str
    suspect_images: list
    upc_image: str
    references: dict
    fetched_refs: list
    fetched_meta: dict
    dimension_results: Annotated[list, operator.add]
    usage_log: Annotated[list, operator.add]
    upc_result: dict
    pairing: dict
    label_id: dict
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
    # Two distinct "no answer" outcomes, deliberately separate from Inconclusive.
    # Inconclusive means "assessed, genuinely ambiguous" — a human-review item.
    # These mean "could not assess" — an input problem, routed differently.
    "insufficient": "Insufficient Evidence — Recapture",
    "mismatch": "Reference Mismatch — Cannot Compare",
    # Deterministic label failure: a fibre list that cannot sum to 100, an RN
    # that resolves to another company. No vision confidence is involved, so
    # this is deliberately a distinct outcome from a vision-derived verdict.
    "hard_fail": "Counterfeit — Label Validation Failed",
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
    elif state.get("ref_source") == "product" and state.get("product_id"):
        refs = supa.product_images_b64(state["product_id"], cap=3)
        out["fetched_refs"] = refs
        out["fetched_meta"] = {"used": True, "source": "product",
                               "product_id": state["product_id"], "kept": len(refs)}
    return out


def _refs_for(state: RunState, dim: str) -> list:
    fetched = state.get("fetched_refs") or []
    if fetched:
        return fetched[:2]                           # Google-fetched authentic shots
    b = load_ref_b64(state["references"].get(dim))    # local data/ reference
    return [b] if b else []


def pairing_node(state: RunState) -> dict:
    """Input validation, before a single dimension agent runs. A confident
    mismatch short-circuits every scoring agent below."""
    ref_b64s = _refs_for(state, "_hero") or _refs_for(state, "Logo")
    result, usage = run_pairing_check(
        state["brand"], state.get("suspect_images", []), ref_b64s,
        state.get("provider", "openai"))
    out = {"pairing": result}
    if usage:
        out["usage_log"] = [usage]
    return out


def dimension_node(dim: str, state: RunState) -> dict:
    # Suspect and reference are not the same product — every dimension score
    # would be a comparison against the wrong thing. Abstain without spending a
    # model call.
    if (state.get("pairing") or {}).get("status") == "mismatch":
        return {"dimension_results": [{
            "dimension": dim, "score": None, "band": "neutral",
            "finding": "NOT SCORED — suspect and reference are different products.",
            "reasoning": (state["pairing"].get("note") or ""),
            "box": None, "confidence": 0.0, "status": "abstain",
            "insufficient_reason": "reference mismatch",
        }], "usage_log": []}
    result, usage = run_dimension_agent(
        dim, state["brand"], state["case_id"], state.get("suspect_images", []),
        _refs_for(state, dim), state.get("provider", "openai"))
    return {"dimension_results": [result], "usage_log": [usage]}


def label_id_node(state: RunState) -> dict:
    """OCR the tags, then run the deterministic rules. Independent of the
    reference images — these checks need no comparison at all."""
    prior = []
    try:
        prior = supa.prior_styles() if hasattr(supa, "prior_styles") else []
    except Exception:
        prior = []
    result, usage = run_label_identity(state["brand"], state.get("suspect_images", []),
                                       prior=prior, provider=state.get("provider", "openai"))
    out = {"label_id": result}
    if usage:
        out["usage_log"] = [usage]
    return out


def upc_node(state: RunState) -> dict:
    result, usage = run_upc_tool(state["brand"], state["case_id"], state.get("upc_image", ""),
                                 state.get("provider", "openai"))
    return {"upc_result": result, "usage_log": [usage]}


def aggregate_node(state: RunState) -> dict:
    """Composite over ASSESSED dimensions only, with a coverage floor.

    Abstentions are dropped, never coerced to a number. If too few dimensions
    were assessable the run returns no score at all — a composite averaged over
    one or two dimensions is indistinguishable, to whoever reads it, from one
    backed by five.
    """
    dims = state["dimension_results"]
    by = {d["dimension"]: d for d in dims}
    scored = [d for d in dims if d.get("score") is not None]
    abstained = [d["dimension"] for d in dims if d.get("score") is None]
    coverage = {"assessed": len(scored), "total": len(DIMENSIONS),
                "abstained": abstained}

    # 0. Deterministic label failure outranks everything below it. These checks
    #    carry no model uncertainty and need no reference image, so they stand
    #    even when the comparison itself is void or nothing was assessable.
    validation = ((state.get("label_id") or {}).get("validation") or {})
    if validation.get("hard_fail"):
        return {"composite": {
            "score": None, "band": "hard_fail", "verdict_label": _VERDICT_LABEL["hard_fail"],
            "coverage": coverage, "capped": False, "deterministic": True,
            "failed_checks": validation.get("failed", []),
            "reason": validation.get("summary", "A deterministic label check failed."),
        }}

    # 1. Incomparable inputs — the whole run is void, no score.
    if (state.get("pairing") or {}).get("status") == "mismatch":
        return {"composite": {
            "score": None, "band": "mismatch", "verdict_label": _VERDICT_LABEL["mismatch"],
            "coverage": coverage, "capped": False,
            "reason": (state["pairing"].get("note")
                       or "Suspect and reference are different products."),
        }}

    # 2. Not enough of the item was assessable to justify any number.
    if len(scored) < MIN_ASSESSED_DIMS:
        return {"composite": {
            "score": None, "band": "insufficient",
            "verdict_label": _VERDICT_LABEL["insufficient"], "coverage": coverage, "capped": False,
            "reason": (f"Only {len(scored)} of {len(DIMENSIONS)} dimensions could be assessed "
                       f"(need {MIN_ASSESSED_DIMS}). Not assessable: "
                       f"{', '.join(abstained) or 'none'}. Recapture with clearer photos of "
                       f"those regions."),
        }}

    # 3. Weighted mean over assessed dimensions, renormalised to their weights.
    num = den = 0.0
    for dim, w in WEIGHTS.items():
        d = by.get(dim)
        if d and d.get("score") is not None:
            num += d["score"] * w
            den += w
    score = round(num / den) if den else None

    # 4. UPC only moves the score on REAL evidence. 'not_provided' (no barcode
    #    photo) and 'unreadable' (capture failure) are absences of a check, not
    #    findings — treating them as findings added +6 to every run in the corpus.
    upc_status = (state.get("upc_result") or {}).get("status")
    if upc_status in ("nomatch", "mismatch") and score is not None:
        score = min(100, score + 6)

    band = _band(score)
    reason = ""
    capped = False
    # 5. Label carries the identity evidence. Without it, allow suspicion but not
    #    a counterfeit conclusion.
    if REQUIRE_LABEL_FOR_VERDICT and band == "counterfeit" and \
            (by.get("Label") or {}).get("score") is None:
        band, capped = "caution", True
        reason = ("Composite reached the counterfeit band but the Label dimension could not be "
                  "assessed; held at Inconclusive pending a legible tag photo.")
    return {"composite": {"score": score, "band": band, "verdict_label": _VERDICT_LABEL[band],
                          "coverage": coverage, "capped": capped, "reason": reason}}


def verdict_node(state: RunState) -> dict:
    verdict, usages = run_verdict(
        state.get("provider", "openai"), state["brand"], state["composite"],
        state["dimension_results"], state["upc_result"])
    return {"verdict": verdict, "usage_log": usages}


def report_node(state: RunState) -> dict:
    rows, total_in, total_out, total_cost, serial_ms = [], 0, 0, 0.0, 0
    # Stable display order: reverse-image pre-steps, dimensions, UPC, verdict, verify.
    order = {"Reverse image": -3, "Curate refs": -2, "Pairing": -1,
             "Label ID": 9,
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

    # Wall-clock = sequential pre-steps (reverse image, ref curation, pairing)
    # + the parallel dimension band + the verdict tail.
    pre = sum(u["latency_ms"] for u in state["usage_log"]
              if u["agent"] in ("Reverse image", "Curate refs", "Pairing"))
    parallel = [u["latency_ms"] for u in state["usage_log"]
                if u["agent"] in DIMENSIONS or u["agent"] in ("UPC / Tag", "Label ID")]
    # Synthesis and the N verify calls all run concurrently, so the tail costs
    # the slowest of them, not their sum.
    tail = [u["latency_ms"] for u in state["usage_log"]
            if u["agent"] in ("Verdict synth.", "Verify")]
    wall_ms = pre + (max(parallel) if parallel else 0) + 100 + (max(tail) if tail else 0)

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
    # Average confidence over ASSESSED dimensions only — an abstention's 0.0
    # would otherwise drag the number down and read as low-quality analysis
    # rather than absent analysis.
    confs = [d["confidence"] for d in dims
             if d.get("score") is not None and d.get("confidence") is not None]
    abst = sum(1 for d in dims if d["status"] == "abstain")
    comp = state["composite"]
    v = state["verdict"]
    out = {
        "confidence": round(sum(confs) / len(confs), 2) if confs else None,
        "verifier": "confirmed" if v["verifier_confirmed"] else "refuted",
        "verifier_votes": v["verifier_votes"],
        "abstentions": f"{abst} / {len(DIMENSIONS)}",
        "assessed": f"{comp.get('coverage', {}).get('assessed', 0)} / {len(DIMENSIONS)}",
        "pairing": (state.get("pairing") or {}).get("status", "skipped"),
        "label_checks": ((state.get("label_id") or {}).get("validation") or {}).get("counts"),
    }
    truth = GROUND_TRUTH.get(state["case_id"])
    # A run with no score makes no prediction — scoring it against ground truth
    # would silently count "I don't know" as a wrong (or right) answer.
    if truth and comp.get("score") is not None:
        predicted = "counterfeit" if comp["band"] != "authentic" else "authentic"
        out["ground_truth"] = truth
        out["correct"] = predicted == truth
    elif truth:
        out["ground_truth"] = truth
        out["correct"] = None
    return out


# ---- graph wiring ---------------------------------------------------------
def build_graph():
    g = StateGraph(RunState)
    g.add_node("intake", intake_node)
    g.add_node("check_pairing", pairing_node)      # node name must differ from state key
    for dim in DIMENSIONS:
        g.add_node(f"dim_{dim}", partial(dimension_node, dim))
    g.add_node("label_identity", label_id_node)    # node name must differ from state key
    g.add_node("upc", upc_node)
    g.add_node("aggregate", aggregate_node)
    g.add_node("synthesize", verdict_node)   # node name must differ from state key
    g.add_node("build_report", report_node)

    # Pairing sits between intake and the fan-out so every dimension agent can
    # see its verdict and skip the call when the inputs are incomparable.
    g.add_edge(START, "intake")
    g.add_edge("intake", "check_pairing")
    for dim in DIMENSIONS:                 # fan-out
        g.add_edge("check_pairing", f"dim_{dim}")
        g.add_edge(f"dim_{dim}", "aggregate")  # fan-in
    g.add_edge("check_pairing", "upc")
    g.add_edge("upc", "aggregate")
    g.add_edge("check_pairing", "label_identity")   # runs alongside the dimension band
    g.add_edge("label_identity", "aggregate")
    g.add_edge("aggregate", "synthesize")
    g.add_edge("synthesize", "build_report")
    g.add_edge("build_report", END)
    return g.compile()
