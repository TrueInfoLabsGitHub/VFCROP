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
from providers import (ALWAYS_SCORE, _cfg, run_dimension_agent,
                       run_label_identity, run_pairing_check, run_upc_tool,
                       run_verdict)
from references import DIMENSIONS, load_ref_b64, select_references
from rimage import fetch_authentic_references

# Per-dimension weights for the composite (sum = 1.0).
# Even across all five: no dimension is treated as a better predictor than
# another. The previous uneven split (.22/.22/.22/.18/.16) was an inherited
# assumption that had never been fitted to outcome data, so weighting them
# equally is the honest default until there is labelled data to fit against.
WEIGHTS = {d: 0.20 for d in ("Logo", "Stitching", "Hardware", "Label", "Material")}

# A composite is only meaningful when enough of the item was actually assessed.
# Below this many scored dimensions the run returns REQUEST_RECAPTURE instead of
# a number — a score averaged over one or two dimensions reads as authoritative
# and is not.
MIN_ASSESSED_DIMS = max(1, int(os.environ.get("MIN_ASSESSED_DIMS", "3")))

# Label carries the deterministic identity evidence, so a run that could not read
# it is capped at 'caution' rather than being allowed to conclude counterfeit.
REQUIRE_LABEL_FOR_VERDICT = os.environ.get(
    "REQUIRE_LABEL_FOR_VERDICT", "1").strip().lower() in ("1", "true", "yes", "on")

# The mirror of the rule above, in the other direction. Clearing an item as
# authentic on no measured evidence is the more dangerous error: a counterfeit
# photographed badly produces low estimates — the model saw no defects, which is
# not the same as there being none — and would otherwise be waved through. Below
# this many MEASURED dimensions a run can be Inconclusive, never Authentic.
MIN_MEASURED_FOR_AUTHENTIC = max(0, int(os.environ.get("MIN_MEASURED_FOR_AUTHENTIC", "3")))

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
    """0 = no deviation detected, 100 = clearly different.
    0 -> Authentic | 1-30 -> Likely Authentic | 31-60 -> Inconclusive |
    61-100 -> Suspected Counterfeit. The first two share the 'authentic' band;
    only the verdict wording differs (see _verdict_label)."""
    if score is None:
        return "neutral"
    if score <= 30:
        return "authentic"
    if score <= 60:
        return "caution"
    return "counterfeit"


def _verdict_label(band, score):
    """Verdict text for a band. A composite of exactly 0 means no deviation was
    detected on any dimension, which is a stronger statement than "likely" —
    so it gets its own wording. The band stays 'authentic' so colours and the
    scorecard's % Authentic keep counting it with the 1-30 range."""
    if band == "authentic" and score == 0:
        return "Authentic"
    return _VERDICT_LABEL[band]


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
    b = load_ref_b64((state.get("references") or {}).get(dim))   # local data/ ref
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
    # model call. ALWAYS_SCORE overrides this: if every cell must carry a number,
    # the agents have to run so the number is the model's own estimate.
    if not ALWAYS_SCORE and (state.get("pairing") or {}).get("status") == "mismatch":
        return {"dimension_results": [{
            "dimension": dim, "score": None, "band": "neutral",
            "finding": "NOT SCORED — suspect and reference are different products.",
            "reasoning": (state["pairing"].get("note") or ""),
            "box": None, "confidence": 0.0, "status": "abstain",
            "insufficient_reason": "reference mismatch",
        }], "usage_log": []}
    try:
        result, usage = run_dimension_agent(
            dim, state["brand"], state["case_id"], state.get("suspect_images", []),
            _refs_for(state, dim), state.get("provider", "openai"))
    except Exception as e:
        # One agent failing must not destroy the run. Previously a single 429 on
        # any dimension propagated out and discarded the other four dimensions'
        # results along with everything already spent on them.
        return {"dimension_results": [{
            "dimension": dim, "score": None, "band": "neutral",
            "finding": f"AGENT FAILED — {e}",
            "reasoning": str(e), "box": None, "confidence": 0.0,
            "status": "error", "insufficient_reason": str(e),
        }], "usage_log": []}
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
    try:
        result, usage = run_upc_tool(state["brand"], state["case_id"],
                                     state.get("upc_image", ""),
                                     state.get("provider", "openai"))
    except Exception as e:
        # A barcode OCR failure is not a reason to lose the analysis.
        return {"upc_result": {"status": "unreadable", "note": f"UPC OCR failed: {e}",
                               "expected": "", "extracted": "", "belongs": None},
                "usage_log": []}
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
    # Two different counts, deliberately kept apart:
    #   evidence  — a real measurement stood behind the number
    #   usable    — has a number at all (includes ALWAYS_SCORE estimates)
    # The composite is computed over `usable`, but `coverage.assessed` reports
    # `evidence`, so a filled grid never masquerades as a fully-measured one.
    evidence = [d for d in dims if d.get("status") == "scored"]
    usable = [d for d in dims if d.get("score") is not None]
    estimated = [d["dimension"] for d in dims if d.get("status") == "estimated"]
    abstained = [d["dimension"] for d in dims if d.get("score") is None]
    scored = usable
    errored = [d["dimension"] for d in dims if d.get("status") == "error"]
    coverage = {"assessed": len(evidence), "total": len(DIMENSIONS),
                "abstained": abstained, "estimated": estimated,
                "errored": errored, "scored_from": len(usable)}

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

    # 1. Incomparable inputs. Without ALWAYS_SCORE the run is void; with it we
    #    still fill the numbers but keep the mismatch verdict, so the row is
    #    populated and the warning is not lost.
    pairing_mismatch = (state.get("pairing") or {}).get("status") == "mismatch"
    if pairing_mismatch and not ALWAYS_SCORE:
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
            (by.get("Label") or {}).get("status") != "scored":
        band, capped = "caution", True
        reason = ("Composite reached the counterfeit band but the Label dimension could not be "
                  "assessed; held at Inconclusive pending a legible tag photo.")
    # 6. The same restraint in the other direction: no clearing an item that was
    #    never really examined.
    elif band == "authentic" and len(evidence) < MIN_MEASURED_FOR_AUTHENTIC:
        band, capped = "caution", True
        reason = (f"only {len(evidence)} of {len(DIMENSIONS)} dimensions measured — "
                  f"not enough evidence to clear as authentic")
    if pairing_mismatch:
        reason = ((state.get("pairing") or {}).get("note")
                  or "Suspect and reference are different products.")
        return {"composite": {"score": score, "band": "mismatch",
                              "verdict_label": _VERDICT_LABEL["mismatch"],
                              "coverage": coverage, "capped": capped, "reason": reason}}
    return {"composite": {"score": score, "band": band,
                          "verdict_label": _verdict_label(band, score),
                          "coverage": coverage, "capped": capped, "reason": reason}}


def verdict_node(state: RunState) -> dict:
    comp = state["composite"]
    try:
        verdict, usages = run_verdict(
            state.get("provider", "openai"), state["brand"], comp,
            state["dimension_results"], state["upc_result"])
    except Exception as e:
        # The composite is ALREADY computed by the time this runs. Letting a
        # failed summary call kill the run threw away every dimension score for
        # the sake of a paragraph of prose — which is how a rate-limited verdict
        # tier wiped out otherwise complete runs.
        dims = sorted((d for d in state["dimension_results"] if d.get("score") is not None),
                      key=lambda d: -d["score"])
        return {"verdict": {
            "label": comp.get("verdict_label", ""),
            "summary": (f"Verdict synthesis unavailable ({e}). The composite score and "
                        f"the dimension findings are unaffected."),
            "escalated": comp.get("band") not in ("authentic", "insufficient", "mismatch"),
            "verifier_confirmed": False,
            "verifier_votes": "0/0",
            "key_evidence": [f"{d['dimension']}: {d.get('finding') or ''}" for d in dims[:3]],
            "degraded": True,
        }, "usage_log": []}
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
        "estimated": len((comp.get("coverage", {}) or {}).get("estimated", [])),
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
