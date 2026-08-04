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

import scoring
import supa
from pricing import price_usage
from providers import (ALWAYS_SCORE, _cfg, run_dimension_agent,
                       run_label_identity, run_pairing_check, run_upc_tool,
                       run_verdict)
from references import DIMENSIONS, load_ref_b64, select_references
from rimage import fetch_authentic_references

# Every weight, threshold and factor now lives in ONE place: scoring.py's
# SCORING_CONSTANTS. Re-exported here because callers and tests import them from
# graph, and because a second definition is how the two drifted apart before.
WEIGHTS = scoring.DIM_WEIGHTS
SCORING_CONSTANTS = scoring.SCORING_CONSTANTS

# Kept as an env-only escape hatch for the label annotation below. It no longer
# demotes a counterfeit-band score to Inconclusive: suppressing suspicion
# because the tag was unreadable contradicts the rule that the coverage guard
# never suppresses suspicion. The verdict stands and says so instead.
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
    product_name: str
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
    """DEVIATION band for a score: 0 = matches the authentic reference on every
    dimension, 100 = matches none of it. Low is good — the same direction as
    every dimension, every primitive and every constant in the system.

    NOTE: this is a DISPLAY helper only. Nothing derives a verdict from it — the
    band comes from the decision ladder in scoring.decide(), which also weighs
    coverage and evidence and can hold a low-deviation item at Insufficient
    Evidence rather than clearing it on a number alone.
    """
    if score is None:
        return "neutral"
    if score <= scoring.BAND_AUTHENTIC:
        return "authentic"
    if score <= scoring.BAND_LIKELY_AUTH:
        return "likely_authentic"
    if score < scoring.BAND_COUNTERFEIT:
        return "caution"
    if score < scoring.DISPOSITIVE_THRESHOLD:
        return "likely_counterfeit"
    return "counterfeit"


def _verdict_label(band, score=None):
    """Verdict text for a band. `score` is kept for call-site compatibility."""
    return _VERDICT_LABEL[band]


# band key -> verdict text. The keys are unchanged so the colour maps in the
# exporter and the UI keep working; the texts follow the decision ladder.
_VERDICT_LABEL = {
    "authentic": "Authentic",
    "likely_authentic": "Likely Authentic",
    "caution": "Inconclusive",
    # Deviation is significant but too little of the item was measured to
    # escalate. A review item that says which way it leans.
    "likely_counterfeit": "Inconclusive — Suspicious",
    # 'Suspected', not 'Counterfeit': this is a vision-derived conclusion, and
    # the only outcome entitled to the flat word is the deterministic one below.
    "counterfeit": "Suspected Counterfeit",
    # Two distinct "no answer" outcomes, deliberately separate from Inconclusive.
    # Inconclusive means "assessed, genuinely ambiguous" — a human-review item.
    # These mean "could not assess" — an input problem, routed differently.
    "insufficient": "Insufficient Evidence",
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
    # Suspect and reference are not the same product, so every dimension score
    # would be a deviation measured against the wrong garment. Not unreliable —
    # meaningless. Abstain without spending a model call.
    #
    # ALWAYS_SCORE used to override this, on the reasoning that every cell should
    # carry a number. It produced rows reading "Reference Mismatch — Cannot
    # Compare" beside five populated dimension scores: three mutually
    # contradictory statements in one row, and someone will read the numbers.
    # A filled cell is worth having when it is the model's honest impression of
    # the right product; it is worth nothing when it is a measurement of a
    # different one.
    if (state.get("pairing") or {}).get("status") == "mismatch":
        return {"dimension_results": [{
            "dimension": dim, "score": None, "band": "neutral",
            "finding": "NOT SCORED — suspect and reference are different products.",
            "reasoning": (state["pairing"].get("note") or ""),
            "box": None, "confidence": 0.0, "status": "abstain",
            "state": scoring.DimState.NOT_ASSESSABLE, "internal_coverage": 0.0,
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
            "status": "error", "state": scoring.DimState.FAILED,
            "internal_coverage": 0.0, "insufficient_reason": str(e),
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
                                     state.get("provider", "openai"),
                                     product=state.get("product_name", ""))
    except Exception as e:
        # A barcode OCR failure is not a reason to lose the analysis.
        return {"upc_result": {"status": "unreadable", "note": f"UPC OCR failed: {e}",
                               "expected": "", "extracted": "", "belongs": None},
                "usage_log": []}
    return {"upc_result": result, "usage_log": [usage]}


def _category_for(state: RunState) -> str:
    """Best guess at the product category, for the applicability table.

    Three sources, none authoritative on its own: what the operator called the
    product, what the pairing agent said the suspect photos show, and what the
    care tag says. An unrecognised category excludes nothing — which keeps
    Hardware in the denominator, the safe direction."""
    return scoring.normalise_category(
        state.get("product_name", ""),
        (state.get("pairing") or {}).get("suspect_item", ""),
        ((state.get("label_id") or {}).get("fields") or {}).get("product_family", ""))


def aggregate_node(state: RunState) -> dict:
    """Composite and verdict, via the decision ladder in scoring.py.

    The three rules that matter, in the order they run:

      * a confirmed dispositive defect escalates BEFORE any coverage gate — it
        needs no corroboration;
      * ESTIMATED dimensions are shown but never counted, so four guesses can
        never outvote one measurement;
      * an item is cleared only on the PRESENCE of evidence: the care tag must
        actually have been read, plus one other forensic dimension.
    """
    dims = state["dimension_results"]
    by = {d["dimension"]: d for d in dims}
    dim_objs = [scoring.Dim.from_record(d["dimension"], d) for d in dims]

    # Reported alongside the ladder's own numbers, for the UI and the export.
    #   assessed  — a real measurement stood behind the number
    #   estimated — a filled cell; displayed, never counted
    measured = [d for d in dim_objs if d.state == scoring.DimState.MEASURED]
    partial = [d.name for d in dim_objs if d.state == scoring.DimState.PARTIAL]
    estimated = [d["dimension"] for d in dims if d.get("status") == "estimated"]
    abstained = [d["dimension"] for d in dims if d.get("score") is None]
    errored = [d["dimension"] for d in dims if d.get("status") == "error"]

    validation = ((state.get("label_id") or {}).get("validation") or {})
    pairing = state.get("pairing") or {}
    # ALWAYS_SCORE keeps the row populated on a mismatch: the numbers are filled
    # but the mismatch verdict still wins, so the warning is never lost.
    pairing_ok = pairing.get("status") != "mismatch"

    result = scoring.decide(
        dim_objs,
        category=_category_for(state),
        upc_status=(state.get("upc_result") or {}).get("status") or "not_provided",
        label_hard_fail=bool(validation.get("hard_fail")),
        hard_fail_reason=validation.get("summary", ""),
        pairing_ok=pairing_ok,
        pairing_note=pairing.get("note", ""),
        label_readable=(by.get("Label") or {}).get("status") == "scored",
        runtime_not_applicable=[d["dimension"] for d in dims
                                if d.get("state") == scoring.DimState.NOT_APPLICABLE],
    )

    applicable = [d for d in result["dimension_states"]
                  if d["state"] != scoring.DimState.NOT_APPLICABLE]
    coverage = {
        "assessed": len(measured), "total": len(DIMENSIONS),
        "applicable": len(applicable), "partial": partial,
        "abstained": abstained, "estimated": estimated, "errored": errored,
        "scored_from": len(result["contributing"]),
        # Stage 5's number. It travels with the score everywhere the score goes.
        "effective": result["coverage_pct"],
    }

    return {"composite": {
        # AUTHENTICITY for the reader (100 = matches the reference on every
        # dimension); `deviation` is the raw internal value the ladder used.
        "score": result["score"],
        "deviation": result["deviation"],
        "band": result["band"],
        "verdict_label": result["verdict_label"],
        "lane": result["lane"],
        "driver": result["driver"],
        "recapture": result["recapture"],
        "coverage": coverage,
        "coverage_pct": result["coverage_pct"],
        "dimension_states": result["dimension_states"],
        "contributing": result["contributing"],
        "deterministic": bool(validation.get("hard_fail")),
        "failed_checks": validation.get("failed", []),
        # `capped` used to mean "a band was overridden". The ladder has no
        # override step — it decides once — so it now means "this verdict is a
        # non-answer", which is what every consumer actually used it for.
        "capped": result["band"] in ("insufficient", "mismatch"),
        "reason": result["reason"],
    }}


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
            "escalated": comp.get("lane") == "REJECTED",
            "verifier_confirmed": False,
            "verifier_votes": "0/0",
            "reviewer_labels": [],
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
    cov = comp.get("coverage", {}) or {}
    out = {
        "confidence": round(sum(confs) / len(confs), 2) if confs else None,
        "verifier": "confirmed" if v["verifier_confirmed"] else "refuted",
        "verifier_votes": v["verifier_votes"],
        "reviewer_labels": v.get("reviewer_labels", []),
        "abstentions": f"{abst} / {len(DIMENSIONS)}",
        # Measured out of APPLICABLE, not out of five: a T-shirt has four
        # dimensions, and scoring it out of five guarantees it looks unassessed.
        "assessed": f"{cov.get('assessed', 0)} / {cov.get('applicable', len(DIMENSIONS))}",
        "coverage": comp.get("coverage_pct"),
        "lane": comp.get("lane", ""),
        "estimated": len(cov.get("estimated", [])),
        "partial": cov.get("partial", []),
        "pairing": (state.get("pairing") or {}).get("status", "skipped"),
        "label_checks": ((state.get("label_id") or {}).get("validation") or {}).get("counts"),
    }
    truth = GROUND_TRUTH.get(state["case_id"])
    # Only a run that actually committed to a lane makes a prediction. A REVIEW
    # lane is "I don't know", and scoring it against ground truth would count
    # that as a right or wrong answer when it is neither.
    lane = comp.get("lane")
    if truth and lane in ("REJECTED", "CLEARED"):
        predicted = "counterfeit" if lane == "REJECTED" else "authentic"
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
