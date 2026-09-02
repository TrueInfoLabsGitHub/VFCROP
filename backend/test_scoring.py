"""Regression tests for the scoring path.

The bug this file originally locked down: a dimension agent that could not see
its region was forced to return an integer, and the aggregator averaged that
fabricated number into a composite. The canonical failure is the `test1` case —
a folded macro shot of a down-jacket care label paired against a cotton t-shirt
reference — which produced three different confident scores (53 / 33 / 42).

It now also covers the decision ladder that replaced the plain weighted mean.
The acceptance tests for that ladder live in test_acceptance.py; this file
covers the plumbing around it.

Run:  pytest backend/test_scoring.py -q      (from the repo root)
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import graph                                                  # noqa: E402
import providers                                              # noqa: E402
import scoring                                                # noqa: E402
from references import DIMENSIONS                              # noqa: E402


# ---- helpers ---------------------------------------------------------------
_STATE_FOR = {"scored": scoring.DimState.MEASURED,
              "estimated": scoring.DimState.ESTIMATED,
              "abstain": scoring.DimState.NOT_ASSESSABLE,
              "error": scoring.DimState.FAILED,
              "not_applicable": scoring.DimState.NOT_APPLICABLE}


def dim(name, score, status="scored", confidence=0.8, state=None, coverage=None):
    """One dimension result as the agents emit it.

    score / state / internal_coverage travel together on purpose — reading a
    score without its state is the failure this whole module exists to stop."""
    st = state or _STATE_FOR[status]
    if coverage is None:
        coverage = 1.0 if st in (scoring.DimState.MEASURED, scoring.DimState.PARTIAL) else 0.0
    return {"dimension": name, "score": score, "band": providers._band(score),
            "finding": "f", "reasoning": "r", "box": None,
            "confidence": confidence, "status": status,
            "state": st, "internal_coverage": coverage}


def state(dims, upc_status="not_provided", pairing_status="ok", hard_fail=False,
          product="", label_fields=None):
    return {"dimension_results": dims,
            "upc_result": {"status": upc_status},
            "product_name": product,
            "pairing": {"status": pairing_status, "note": "n", "suspect_item": ""},
            # `fields` feeds the deterministic MATERIAL layer, which reads the
            # same OCR output the label validator does. Empty by default, so
            # every check there reports UNKNOWN and the new rungs stay dormant.
            "label_id": {"fields": label_fields or {},
                         "validation": {"hard_fail": hard_fail, "failed": ["R3"],
                                        "summary": "RN resolves to another company."}}}


def agg(dims, **kw):
    return graph.aggregate_node(state(dims, **kw))["composite"]


def all_measured(deviation, **kw):
    return agg([dim(d, deviation) for d in DIMENSIONS], **kw)


# ---- the abstain gate ------------------------------------------------------
@pytest.fixture
def honest(monkeypatch):
    """ALWAYS_SCORE off — the honest-abstention mode.

    graph.py does `from providers import ALWAYS_SCORE`, which binds its own copy
    at import time, so both names have to be patched."""
    monkeypatch.setattr(providers, "ALWAYS_SCORE", False)
    monkeypatch.setattr(graph, "ALWAYS_SCORE", False)


def test_unassessable_dimension_yields_no_score(honest):
    """A model saying it cannot evaluate must not produce a number."""
    parsed = {"assessable": False, "insufficient_reason": "logo not visible",
              "score": 0, "finding": "x", "reasoning": "y", "confidence": 0.9}
    result, _usage = providers._dim_result("Logo", parsed, "test-model", 0, 0, 0.0)
    assert result["score"] is None
    assert result["status"] == "abstain"
    assert result["state"] == scoring.DimState.NOT_ASSESSABLE
    assert "INSUFFICIENT" in result["finding"]


def test_low_confidence_is_treated_as_abstention(honest):
    """A confident-looking score with 0.1 confidence is a guess, not a measurement."""
    parsed = {"assessable": True, "insufficient_reason": "", "score": 50,
              "finding": "x", "reasoning": "y", "confidence": 0.1}
    result, _ = providers._dim_result("Hardware", parsed, "test-model", 0, 0, 0.0)
    assert result["score"] is None
    assert result["status"] == "abstain"


def test_assessable_dimension_still_scores_normally():
    parsed = {"assessable": True, "insufficient_reason": "", "score": 82,
              "finding": "x", "reasoning": "y", "confidence": 0.9}
    result, _ = providers._dim_result("Material", parsed, "test-model", 0, 0, 0.0)
    assert result["score"] == 82
    assert result["status"] == "scored"
    assert result["state"] == scoring.DimState.MEASURED
    assert result["band"] == "counterfeit"


# ---- the coverage floor ----------------------------------------------------
def test_one_measured_dimension_cannot_produce_a_verdict():
    """The test1 shape: most dimensions unassessable -> no clearance, ever."""
    dims = [dim("Logo", None, "abstain", 0.0), dim("Stitching", None, "abstain", 0.0),
            dim("Hardware", None, "abstain", 0.0), dim("Label", 0),
            dim("Material", None, "abstain", 0.0)]
    c = agg(dims)
    assert c["band"] == "insufficient"
    assert c["verdict_label"] == "Insufficient Evidence"
    assert c["coverage"]["assessed"] == 1
    assert c["lane"] == "REVIEW"


def test_abstentions_are_dropped_not_counted_as_zeros():
    dims = [dim("Logo", 80), dim("Stitching", 80), dim("Hardware", None, "abstain", 0.0),
            dim("Label", 80), dim("Material", None, "abstain", 0.0)]
    c = agg(dims)
    # 80 across every measured dimension. If the two abstentions were coerced to
    # 0 the deviation would come out near 48 and the item would not escalate.
    assert c["deviation"] == 80
    assert c["coverage"]["assessed"] == 3
    assert set(c["coverage"]["abstained"]) == {"Hardware", "Material"}


def test_recapture_names_the_shots_that_would_resolve_it():
    """An Insufficient Evidence verdict is only useful if it says what to do."""
    dims = [dim("Logo", 10), dim("Stitching", None, "abstain", 0.0),
            dim("Hardware", None, "abstain", 0.0), dim("Label", None, "abstain", 0.0),
            dim("Material", None, "abstain", 0.0)]
    c = agg(dims)
    assert c["band"] == "insufficient"
    shots = " ".join(c["recapture"])
    assert "Label: Interior care tag" in shots
    assert "Stitching:" in shots and "Hardware:" in shots
    assert "Logo:" not in shots               # Logo was measured — nothing to recapture


# ---- the pairing gate must not fire on ordinary evidence shots -------------
def test_pairing_prompt_allows_detail_shots_against_a_full_garment():
    """The real-world submission shape is close-ups of the SUSPECT's tags against
    a full-garment REFERENCE. Treating that as a mismatch voided whole runs and
    produced 0/5 rows, so the prompt must say so explicitly."""
    p = providers._PAIRING_PROMPT
    assert "NORMAL shape of this work, not a mismatch" in p
    assert "cannot contradict the reference" in p
    assert "ONLY when the two sets positively show DIFFERENT product categories" in p


def test_a_mismatch_produces_no_composite_but_keeps_the_dimension_cells():
    """Comparing against the wrong product makes every deviation meaningless, so
    the composite is withheld. The per-dimension cells still carry their numbers,
    so the row is populated in the export and the warning is not lost."""
    dims = [dim(d, 40, "estimated") for d in DIMENSIONS]
    c = agg(dims, pairing_status="mismatch")
    assert c["score"] is None
    assert c["verdict_label"] == "Reference Mismatch — Cannot Compare"
    assert c["band"] == "mismatch"
    assert c["lane"] == "REVIEW"


def test_missing_reference_file_does_not_crash():
    """load_ref_b64(None) used to raise; a brand map missing a dimension would
    take the whole run down."""
    from references import load_ref_b64
    assert load_ref_b64(None) is None
    assert load_ref_b64("") is None


# ---- weights ---------------------------------------------------------------
def test_weights_and_diagnostics_cover_every_dimension():
    assert set(graph.WEIGHTS) == set(DIMENSIONS)
    assert sum(graph.WEIGHTS.values()) == 100      # whole-number percentages
    assert set(scoring.DIM_DIAGNOSTIC) == set(DIMENSIONS)
    # Label carries the identity evidence, so it is both the heaviest weight and
    # the most diagnostic dimension.
    assert max(graph.WEIGHTS, key=graph.WEIGHTS.get) == "Label"
    assert max(scoring.DIM_DIAGNOSTIC, key=scoring.DIM_DIAGNOSTIC.get) == "Label"


def test_every_constant_lives_in_one_config_block():
    """They were scattered across three modules and drifted apart. One block, so
    they can be fitted against labelled data later without a treasure hunt."""
    for key in ("DIM_WEIGHTS", "DIM_DIAGNOSTIC", "GROUP_SHARES", "GROUP_FLOOR_FACTOR",
                "PARTIAL_WEIGHT_FACTOR", "MIN_DIM_CONFIDENCE", "DISPOSITIVE_THRESHOLD",
                "BAND_COUNTERFEIT", "COVERAGE_FOR_CONCLUSION", "UPC_MISMATCH_FLOOR"):
        assert key in scoring.SCORING_CONSTANTS
    assert graph.SCORING_CONSTANTS is scoring.SCORING_CONSTANTS


# ---- the ladder ------------------------------------------------------------
# Bands: 0-3 authentic, 4-30 likely authentic, 31+ suspected counterfeit.
#
# Authentic additionally requires every applicable dimension to have been
# MEASURED — these cases satisfy that, so only the bands decide here. The
# all-measured requirement is exercised on its own further down.
#
# There is no gap before the counterfeit band, so "Inconclusive" is not reachable
# through the bands at all; it survives only for runs that reach rung 11 another
# way.
@pytest.mark.parametrize("deviation,label", [
    (0,  "Authentic"),                  # every dimension identical to the reference
    (3,  "Authentic"),                  # the top of the authentic band
    (4,  "Likely Authentic"),
    (10, "Likely Authentic"),
    (25, "Likely Authentic"),           # the top of the clearing band
    (26, "Inconclusive"),               # the photographic-noise range: a person looks
    (30, "Inconclusive"),
    (31, "Suspected Counterfeit"),      # DIM_COUNTERFEIT — one dimension is enough
    (60, "Suspected Counterfeit"),
    (84, "Suspected Counterfeit"),
])
def test_ladder_boundaries_at_full_coverage(deviation, label):
    """ONE scale end to end: the composite is reported on the same deviation
    scale as the dimensions that produced it. Every dimension is MEASURED here,
    so the coverage gates are all satisfied and only the bands decide."""
    c = all_measured(deviation, upc_status="match")   # certification needs the lookup
    assert c["deviation"] == deviation
    assert c["score"] == deviation
    assert c["verdict_label"] == label


def test_a_dispositive_defect_escalates_before_any_coverage_gate():
    """Rule 4. A confirmed dispositive defect needs no corroboration — it must
    fire even though the rest of the item was never measured."""
    dims = [dim("Logo", 10, "estimated", 0.3), dim("Stitching", 10, "estimated", 0.3),
            dim("Hardware", 10, "estimated", 0.3), dim("Label", 90, confidence=0.8),
            dim("Material", 10, "estimated", 0.3)]
    c = agg(dims)
    assert c["verdict_label"] == "Suspected Counterfeit"
    assert c["driver"] == "Label"
    assert c["lane"] == "REJECTED"


def test_a_partial_dimension_may_not_trigger_the_dispositive_rule():
    """A PARTIAL dimension ran on geometry, which photography moves as much as
    authenticity does. It may raise the composite; it may not escalate alone."""
    dims = [dim("Logo", 90, state=scoring.DimState.PARTIAL, coverage=0.5, confidence=0.8)]
    dims += [dim(d, 5, "estimated", 0.3) for d in DIMENSIONS if d != "Logo"]
    c = agg(dims)
    # It convicts — but through R4c, the partial-specific rung with its own
    # 2x-noise-ceiling threshold, never through R4's dispositive rule, whose
    # confidence semantics belong to MEASURED dimensions only.
    assert c["rule"] == "R4c"
    assert c["rule"] not in ("R4", "R4b")



def test_one_measured_dimension_in_the_band_convicts_on_thin_coverage():
    """Rung 4b. A single MEASURED dimension at 61 or worse convicts outright —
    no corroboration, no coverage requirement, and no averaging against the four
    dimensions that happened to look fine.

    This case used to return 'Inconclusive — Suspicious' because coverage was
    under 35%. It no longer does: coverage gates what the COMPOSITE may
    conclude, and rung 4b does not consult the composite."""
    dims = [dim("Material", 70, confidence=0.8)]
    dims += [dim(d, 70, "estimated", 0.3) for d in DIMENSIONS if d != "Material"]
    c = agg(dims)
    assert c["verdict_label"] == "Suspected Counterfeit"
    assert c["lane"] == "REJECTED"
    assert c["driver"] == "Material"
    assert c["coverage_pct"] < scoring.COVERAGE_FOR_COUNTERFEIT / 100



def test_thin_coverage_still_downgrades_a_COMPOSITE_only_suspicion():
    """Rung 6 survives for the case it was written for: the composite reaches
    the band while NO single dimension does, so there is nothing dispositive to
    convict on and thin coverage still means 'review, leaning bad'.

    With the band at 31 the window for this is narrow — the composite has to be
    dragged over 31 by dimensions that are each under it — which is why the
    scores here sit just below DIM_COUNTERFEIT rather than at 60."""
    edge = scoring.DIM_COUNTERFEIT - 1
    dims = [dim("Label", edge, confidence=0.8, coverage=0.4),
            dim("Logo", edge - 2, confidence=0.8, coverage=0.4)]
    dims += [dim(d, None, "abstain", 0.0) for d in DIMENSIONS
             if d not in ("Label", "Logo")]
    c = agg(dims)
    assert all(d["score"] is None or d["score"] < scoring.DIM_COUNTERFEIT
               for d in c["dimension_states"])
    assert c["verdict_label"] in ("Inconclusive — Suspicious", "Insufficient Evidence",
                                 "Inconclusive")


def test_an_unread_label_annotates_rather_than_suppresses():
    """REQUIRE_LABEL_FOR_VERDICT used to demote a counterfeit-band score to
    Inconclusive. That contradicted the rule that the coverage guard never
    suppresses suspicion; the verdict now stands and says the label is
    unverified."""
    dims = [dim("Logo", 70), dim("Stitching", 70), dim("Hardware", 70),
            dim("Label", None, "abstain", 0.0), dim("Material", 70)]
    c = agg(dims)
    assert c["verdict_label"] == "Suspected Counterfeit"
    assert "label unverified" in c["reason"]


# ---- the UPC ---------------------------------------------------------------
def test_missing_upc_image_does_not_move_the_score():
    """'No barcode photo supplied' is the absence of a check, not a finding.
    This previously added +6 to every run in the corpus."""
    assert all_measured(50, upc_status="not_provided")["deviation"] == 50
    assert all_measured(50, upc_status="unreadable")["deviation"] == 50
    assert all_measured(50, upc_status="no_master_record")["deviation"] == 50


def test_a_bad_barcode_applies_a_floor_not_a_nudge():
    """A code that resolves to a different product is strong evidence, and a
    floor cannot be averaged away the way a +/-6 nudge could."""
    assert all_measured(10, upc_status="mismatch")["deviation"] == 70
    assert all_measured(10, upc_status="nomatch")["deviation"] == 60
    # ...and it never LOWERS a deviation that is already higher.
    assert all_measured(90, upc_status="mismatch")["deviation"] == 90


def test_the_upc_master_record_is_per_product_not_per_brand():
    """One hardcoded barcode per brand put a Nuptse jacket's UPC on the
    'expected' line of a beanie, a swimsuit and two shirts — so the check had
    never actually run."""
    assert providers._expected_upc("TNF", "1996 Retro Nuptse Jacket") == "193393578024"
    assert providers._expected_upc("TNF", "Logo Box Beanie") == ""
    assert providers._expected_upc("TNF", "") == ""


def test_a_product_with_no_master_record_is_not_evidence(monkeypatch):
    monkeypatch.setattr(providers, "_chat",
                        lambda *a, **k: ({"upc": "999999999999", "readable": True}, 1, 1))
    res, _ = providers._chat_upc({"label": "t"}, "TNF", "b64", 0.0, product="Logo Box Beanie")
    assert res["status"] == "no_master_record"
    assert res["expected"] == ""
    # and the ladder must leave the composite alone for that status
    assert scoring.apply_upc(20, "no_master_record") == 20


# ---- the pairing gate ------------------------------------------------------
def test_reference_mismatch_voids_the_run(honest):
    dims = [dim(d, 90) for d in DIMENSIONS]
    c = agg(dims, pairing_status="mismatch")
    assert c["score"] is None
    assert c["band"] == "mismatch"
    assert "Cannot Compare" in c["verdict_label"]


def test_mismatch_short_circuits_dimension_agents(monkeypatch, honest):
    """A mismatched pairing must not spend a model call per dimension."""
    called = []
    monkeypatch.setattr(providers, "run_dimension_agent",
                        lambda *a, **k: called.append(1) or ({}, {}))
    monkeypatch.setattr(graph, "run_dimension_agent",
                        lambda *a, **k: called.append(1) or ({}, {}))
    out = graph.dimension_node("Logo", {"pairing": {"status": "mismatch", "note": "n"},
                                        "brand": "TNF", "case_id": "c"})
    assert called == []
    assert out["dimension_results"][0]["score"] is None
    assert out["dimension_results"][0]["status"] == "abstain"


# ---- the whole test1 shape, end to end ------------------------------------
def test_test1_case_produces_no_number():
    """The regression this whole change exists for.

    Suspect: folded macro of a down-jacket care label. Reference: cotton tee.
    Correct behaviour is a recapture request from every engine — not 53/33/42.
    """
    dims = [dim(d, None, "abstain", 0.0) for d in DIMENSIONS]
    c = agg(dims, pairing_status="mismatch")
    assert c["score"] is None, "a fabricated composite came back for an unscorable case"
    assert c["band"] in ("mismatch", "insufficient")

    # …and with the pairing gate unavailable, the evidence gate must still catch it.
    c2 = agg(dims, pairing_status="skipped")
    assert c2["score"] is None
    assert c2["band"] == "insufficient"


# ---- the forensic rubrics --------------------------------------------------
def prim(name, deviation, confidence="high", evidence="e"):
    return {"name": name, "deviation": deviation, "evidence": evidence,
            "confidence": confidence}


def logo(primitives, method="embroidery", reference_used=True, **extra):
    return providers._aggregate_logo({
        "reference_used": reference_used, "application_method": method,
        "primitives": primitives, "logo_deviation": None, "assessment": "",
        "top_deviations": [], "capture_issues": [], "recapture_instructions": ["r"],
        **extra})


def test_logo_without_a_reference_never_assesses():
    """HARD RULE 1 — no reference means no assessment, ever, not a guess from
    memory of what the brand's logo looks like."""
    a = logo([prim("arc_radius_ratios", 10)], reference_used=False)
    assert a["score"] is None
    assert a["assessment"] == "NO_REFERENCE"
    assert a["status"] == "abstain"


def test_logo_rollup_is_floored_by_the_worst_method_primitive():
    """One severe primitive must not be averaged away by many clean ones."""
    prims = [prim("arc_radius_ratios", 0), prim("inter_arc_gap", 0),
             prim("stroke_uniformity", 0), prim("bounding_box_ratio", 0),
             prim("satin_angle_consistency", 90), prim("stitch_density_cv", 0),
             prim("underlay_present", 0), prim("edge_thread_creep", 0),
             prim("thread_sheen", 0), prim("backing_visible_reverse", 0)]
    a = logo(prims)
    # group-normalised mean is well under 0.85*90 = 76.5, so the floor wins
    assert a["score"] == round(0.85 * 90)        # 76 (banker's rounding on .5)
    assert a["assessment"] == "deviation_significant"
    assert a["top_deviations"][0] == "satin_angle_consistency"


def test_geometry_gets_a_gentler_floor_than_method():
    """A flat 0.85 overfired on geometry, where a rumpled garment alone produces
    baseline_deviation ~90."""
    geo = logo([prim("baseline_deviation", 90), prim("satin_angle_consistency", 0),
                prim("stitch_density_cv", 0)])
    meth = logo([prim("baseline_deviation", 0), prim("satin_angle_consistency", 90),
                 prim("stitch_density_cv", 0)])
    assert geo["score"] < meth["score"]
    assert scoring.GROUP_FLOOR_FACTOR["geometry"] < scoring.GROUP_FLOOR_FACTOR["method"]


def test_an_undetermined_method_is_partial_not_an_abstention():
    """The class could not be detected, so the method group never ran. That is a
    real but discounted result, not 'no result' — and Layer 2 halves its weight
    and its ceiling rather than throwing the geometry away."""
    a = logo([prim("arc_radius_ratios", 40), prim("wordmark_kerning", 40)],
             method="UNKNOWN")
    assert a["score"] is not None
    assert a["state"] == scoring.DimState.PARTIAL
    assert a["internal_coverage"] == 0.3          # geometry's share only
    assert "PARTIAL" in a["finding"]


def test_a_partial_dimension_is_weighted_and_ceilinged_down():
    d = scoring.Dim("Logo", 60, scoring.DimState.PARTIAL, 0.8, internal_coverage=0.3)
    full = scoring.Dim("Logo", 60, scoring.DimState.MEASURED, 0.8, internal_coverage=0.3)
    assert d.effective_weight == pytest.approx(full.effective_weight * 0.40)
    assert d.diagnostic == pytest.approx(full.diagnostic * 0.50)


def test_low_confidence_primitives_are_excluded_entirely():
    """A row the model itself marked low confidence is an impression, not an
    observation, and must not enter the arithmetic."""
    a = logo([prim("arc_radius_ratios", 100, confidence="low"),
              prim("satin_angle_consistency", 0)])
    assert a["score"] == 0
    assert a["internal_coverage"] == 0.5          # method only; geometry dropped out


def test_damped_primitives_cannot_assert_more_than_suspicious():
    """Exposure moves thread_sheen more than authenticity does."""
    damped = logo([prim("thread_sheen", 100), prim("stitch_density_cv", 0)])
    real = logo([prim("edge_thread_creep", 100), prim("stitch_density_cv", 0)])
    assert damped["score"] <= scoring.DAMP_CEILING < real["score"]


def test_presence_primitives_are_tri_state():
    """genuine / tell / not-visible -> 0 / 100 / excluded. A mid value from the
    model is snapped; 'not visible' is never recorded as 'absent'."""
    seen = logo([prim("underlay_present", 30), prim("stitch_density_cv", 0)])
    tell = logo([prim("underlay_present", 70), prim("stitch_density_cv", 0)])
    unseen = logo([prim("underlay_present", "INSUFFICIENT"), prim("stitch_density_cv", 0)])
    assert seen["score"] == 0                          # 30 -> genuine
    assert tell["score"] == round(0.85 * 100)          # 70 -> tell, floors the score
    assert unseen["score"] == 0                        # excluded, not counted as absent


def test_hand_feel_proxy_is_gone():
    """Not resolvable from a photograph. Asking for it produced a number that
    looked like evidence and was not."""
    assert "hand_feel_proxy" in providers.REMOVED_PRIMITIVES
    assert "hand_feel_proxy" not in providers._MAT_CONSISTENCY
    assert "hand_feel_proxy" not in providers._RUBRICS["Material"]["prompt"]


def test_a_runtime_not_applicable_short_circuits_the_dimension():
    a = logo([prim("arc_radius_ratios", 40)], applicable=False)
    assert a["score"] is None
    assert a["state"] == scoring.DimState.NOT_APPLICABLE
    assert a["assessment"] == "NOT_APPLICABLE"


def test_not_applicable_is_never_filled_with_an_estimate():
    """ALWAYS_SCORE fills empty cells. It must not fill a cell whose honest
    answer is 'there is nothing here to score'."""
    a = logo([prim("arc_radius_ratios", 40)], applicable=False)
    a["estimate"] = 55
    r = providers._rubric_result("Logo", a)
    assert r["score"] is None
    assert r["state"] == scoring.DimState.NOT_APPLICABLE


def test_logo_clean_comparison_scores_consistent():
    prims = [prim("arc_radius_ratios", 2), prim("inter_arc_gap", 3),
             prim("satin_angle_consistency", 4), prim("stitch_density_cv", 2),
             prim("underlay_present", 0), prim("edge_thread_creep", 5),
             prim("thread_sheen", 3), prim("backing_visible_reverse", 1)]
    a = logo(prims)
    assert a["status"] == "scored"
    assert a["state"] == scoring.DimState.MEASURED
    assert a["assessment"] == "consistent_with_reference"
    assert a["score"] <= 30


def test_logo_never_emits_a_counterfeit_verdict():
    """HARD RULE 6 — the adverse ceiling is 'deviation_significant'."""
    a = logo([prim("arc_radius_ratios", 100), prim("satin_angle_consistency", 100),
              prim("stitch_density_cv", 100), prim("underlay_present", 100),
              prim("edge_thread_creep", 100), prim("thread_sheen", 100),
              prim("backing_visible_reverse", 100)])
    assert a["score"] >= 85
    assert a["assessment"] == "deviation_significant"
    assert "counterfeit" not in a["finding"].lower()


def test_logo_prompt_carries_every_rubric_primitive():
    """The prompt the model actually receives must name every primitive."""
    for name in providers._LOGO_PRIMITIVE_NAMES:
        assert name in providers._LOGO_PROMPT, f"{name} missing from the Logo prompt"
    for rule in ("NO_REFERENCE", "INSUFFICIENT", "deviation_significant",
                 "scale-invariant", "weight 3x"):
        assert rule in providers._LOGO_PROMPT


def test_logo_abstention_flows_through_to_the_composite(honest):
    """An INSUFFICIENT_CAPTURE logo must reach the aggregator as score=None."""
    a = logo([prim("satin_angle_consistency", "INSUFFICIENT"),
              prim("stitch_density_cv", "INSUFFICIENT")])
    result = providers._logo_result(a)
    assert result["score"] is None and result["status"] == "abstain"
    c = agg([result, dim("Stitching", 40), dim("Hardware", 40),
             dim("Label", 40), dim("Material", 40)])
    assert c["coverage"]["assessed"] == 4
    assert "Logo" in c["coverage"]["abstained"]


# ---- the same rubric treatment across all four dimensions -----------------
# (dimension, a valid method, a method-group primitive, a geometry primitive)
RUBRIC_CASES = [
    ("Logo", "embroidery", "satin_angle_consistency", "arc_radius_ratios"),
    ("Stitching", "overlock", "thread_count_in_overlock", "stitch_pitch_ratio"),
    ("Hardware", "zip", "foundry_code", "pull_dimension_ratios"),
    ("Material", "woven", "weave_type", "drape_fold_radius"),
]
RUBRIC_IDS = [c[0] for c in RUBRIC_CASES]


def rubric(dim, primitives, method, reference_used=True):
    spec = providers._RUBRICS[dim]
    return providers._aggregate_rubric(dim, {
        "reference_used": reference_used, spec["method_key"]: method,
        "primitives": primitives, spec["dev_key"]: None, "assessment": "",
        "top_deviations": [], "capture_issues": [], "recapture_instructions": ["r"]})


def test_every_dimension_runs_a_rubric():
    """Logo, Stitching, Hardware and Material all route to the forensic rubric;
    Label keeps its own check-list rubric. Nothing falls back to the generic
    'give me a number' prompt."""
    assert set(providers.RUBRIC_DIMENSIONS) == {"Logo", "Stitching", "Hardware", "Material"}
    assert set(DIMENSIONS) == set(providers.RUBRIC_DIMENSIONS) | {"Label"}


@pytest.mark.parametrize("dim,method,heavy,light", RUBRIC_CASES, ids=RUBRIC_IDS)
def test_rubric_prompt_is_complete(dim, method, heavy, light):
    """Each prompt must carry its own primitives, the hard rules, the roll-up
    formula and the OUTPUT contract."""
    spec = providers._RUBRICS[dim]
    p = spec["prompt"]
    for name in providers._heavy_names(dim):
        assert name in p, f"{dim}: method primitive {name} missing from prompt"
    for names in spec["light"].values():
        for name in names:
            assert name in p, f"{dim}: light primitive {name} missing from prompt"
    for rule in ("NO_REFERENCE", "INSUFFICIENT is a correct answer",
                 "scale-invariant", "Never output a counterfeit verdict",
                 "deviation_significant", "weight 3x", "weight 1x",
                 "0.85 * max_single_deviation", "INSUFFICIENT_CAPTURE",
                 "valid JSON only", "recapture_instructions"):
        assert rule in p, f"{dim}: prompt is missing {rule!r}"
    assert spec["dev_key"] in p and spec["method_key"] in p


@pytest.mark.parametrize("dim,method,heavy,light", RUBRIC_CASES, ids=RUBRIC_IDS)
def test_rubric_prompt_carries_the_stage_two_contract(dim, method, heavy, light):
    """Applicability and the tri-state presence rule are the two instructions
    that stop an absence of observation being logged as an observation of
    absence."""
    p = providers._RUBRICS[dim]["prompt"]
    assert "applicable" in p
    assert "'Not visible' is never 'absent'." in p


@pytest.mark.parametrize("dim,method,heavy,light", RUBRIC_CASES, ids=RUBRIC_IDS)
def test_rubric_schema_is_wellformed(dim, method, heavy, light):
    spec = providers._RUBRICS[dim]
    s = providers._rubric_schema(dim)
    assert set(s["required"]) == set(s["properties"])       # strict-mode requirement
    assert method in s["properties"][spec["method_key"]]["enum"]
    assert "UNKNOWN" in s["properties"][spec["method_key"]]["enum"]
    assert s["properties"]["applicable"]["type"] == "boolean"
    assert s["additionalProperties"] is False


@pytest.mark.parametrize("dim,method,heavy,light", RUBRIC_CASES, ids=RUBRIC_IDS)
def test_rubric_no_reference_never_assesses(dim, method, heavy, light):
    a = rubric(dim, [prim(light, 10)], method, reference_used=False)
    assert a["score"] is None and a["assessment"] == "NO_REFERENCE"


@pytest.mark.parametrize("dim,method,heavy,light", RUBRIC_CASES, ids=RUBRIC_IDS)
def test_rubric_unknown_method_is_partial(dim, method, heavy, light):
    a = rubric(dim, [prim(light, 5)], "UNKNOWN")
    assert a["state"] == scoring.DimState.PARTIAL
    assert a["internal_coverage"] < 1.0


@pytest.mark.parametrize("dim,method,heavy,light", RUBRIC_CASES, ids=RUBRIC_IDS)
def test_rubric_method_group_carries_half_the_dimension(dim, method, heavy, light):
    assert providers._primitive_group(dim, heavy, method) == "method"
    assert providers._primitive_group(dim, light, method) == "geometry"
    assert scoring.GROUP_SHARES["method"] == 50


@pytest.mark.parametrize("dim,method,heavy,light", RUBRIC_CASES, ids=RUBRIC_IDS)
def test_rubric_worst_primitive_floor_applies(dim, method, heavy, light):
    a = rubric(dim, [prim(light, 0), prim(heavy, 80)], method)
    # group mean = .5/.8*80 + .3/.8*0 = 50; method floor = 0.85*80 = 68 -> floor wins
    assert a["score"] == 68


@pytest.mark.parametrize("dim,method,heavy,light", RUBRIC_CASES, ids=RUBRIC_IDS)
def test_rubric_never_emits_a_counterfeit_verdict(dim, method, heavy, light):
    a = rubric(dim, [prim(light, 100), prim(heavy, 100)], method)
    assert a["assessment"] == "deviation_significant"
    assert "counterfeit" not in a["finding"].lower()


@pytest.mark.parametrize("dim,method,heavy,light", RUBRIC_CASES, ids=RUBRIC_IDS)
def test_rubric_clean_comparison_scores_consistent(dim, method, heavy, light):
    a = rubric(dim, [prim(light, 3), prim(heavy, 4)], method)
    assert a["status"] == "scored"
    assert a["assessment"] == "consistent_with_reference"


@pytest.mark.parametrize("dim,method,heavy,light", RUBRIC_CASES, ids=RUBRIC_IDS)
def test_rubric_dimension_skips_the_call_without_a_reference(dim, method, heavy, light,
                                                             monkeypatch, honest):
    calls = []
    monkeypatch.setattr(providers, "_chat",
                        lambda *a, **k: calls.append(1) or ({}, 0, 0))
    res, usage = providers._rubric_dimension({"label": "t"}, dim, ["img"], [], 0.0)
    assert calls == [], f"{dim} spent a model call with no reference image"
    assert res["score"] is None and res["status"] == "abstain"
    assert res["dimension"] == dim and usage["agent"] == dim


@pytest.mark.parametrize("dim,method,heavy,light", RUBRIC_CASES, ids=RUBRIC_IDS)
def test_chat_dimension_routes_to_the_rubric(dim, method, heavy, light, monkeypatch):
    """The router must send each dimension to its rubric, not the generic prompt."""
    seen = {}

    def fake_chat(cfg, content, schema, name, timeout):
        seen["schema_name"] = name
        seen["prompt"] = content[0]["text"]
        spec = providers._RUBRICS[dim]
        return ({"error": "", "reference_used": True, "applicable": True,
                 spec["method_key"]: method,
                 "primitives": [prim(light, 10), prim(heavy, 10)],
                 spec["dev_key"]: None, "assessment": "", "top_deviations": [],
                 "capture_issues": [], "recapture_instructions": []}, 10, 5)

    monkeypatch.setattr(providers, "_chat", fake_chat)
    res, _ = providers._chat_dimension({"label": "t"}, dim, ["img"], ["ref"], 0.0)
    assert seen["schema_name"] == dim.lower()
    assert seen["prompt"] == providers._RUBRICS[dim]["prompt"]
    assert res["score"] == 10 and res["method"] == method
    assert res["state"] == scoring.DimState.MEASURED


# ---- the Label rubric ------------------------------------------------------
def lcheck(cid, status, confidence=0.9):
    return {"id": cid, "status": status, "evidence": "e", "confidence": confidence}


def test_label_internal_coverage_is_severity_weighted():
    """A Label score of 0 built from L11 alone is not the same evidence as one
    built from L3/L8/L10, and reporting both as fully covered is how a single
    supporting check cleared an item."""
    thin = providers._aggregate_label([lcheck("L11", "genuine")])
    assert thin["score"] == 0
    assert thin["internal_coverage"] < 0.15

    thick = providers._aggregate_label(
        [lcheck(cid, "genuine") for cid, _s, _d in providers._LABEL_CHECKS])
    assert thick["internal_coverage"] == 1.0


def test_a_not_applicable_check_leaves_the_denominator():
    """L7 on a non-Gore-Tex item is not an unmet check — it is not a check."""
    with_na = providers._aggregate_label(
        [lcheck("L3", "genuine"), lcheck("L7", "not_applicable")])
    without = providers._aggregate_label(
        [lcheck("L3", "genuine"), lcheck("L7", "not_visible")])
    assert with_na["internal_coverage"] > without["internal_coverage"]


def test_a_critical_tell_still_floors_the_label_at_85():
    a = providers._aggregate_label([lcheck("L3", "counterfeit_tell"),
                                    lcheck("L5", "genuine"), lcheck("L6", "genuine")])
    assert a["score"] >= 85


def test_l9_moved_out_of_the_vision_rubric():
    """'Does this style number resolve to a real model' is a database lookup, not
    a vision judgement, and it was being averaged in at strong weight beside
    typeface guesses."""
    assert "L9" not in providers._LABEL_IDS
    import label_rules
    ids = [c["id"] for c in label_rules.validate({"style_number": "NF0A3JQC"})["checks"]]
    assert "S2" in ids


def test_style_resolution_is_unknown_without_a_catalogue():
    """An unknown must never contribute to a hard fail: 'we have no data' is not
    'the product does not exist'."""
    import label_rules
    r = label_rules.check_style_resolution("NF0A3JQC", catalog=set())[0]
    assert r["status"] == label_rules.UNKNOWN
    hit = label_rules.check_style_resolution("NF0A3JQC", catalog={"NF0A3JQC"})[0]
    assert hit["status"] == label_rules.PASS
    miss = label_rules.check_style_resolution("NF0A9ZZZ", catalog={"NF0A3JQC"})[0]
    assert miss["status"] == label_rules.FAIL


# ---- ALWAYS_SCORE: every cell filled, audit trail preserved ----------------
def test_an_unassessable_dimension_gets_no_number_by_default():
    """ALWAYS_SCORE is OFF by default, so a dimension the model could not see
    carries score None and NOT_ASSESSABLE — never a figure that an exporter can
    print as though it were a reading."""
    parsed = {"assessable": False, "insufficient_reason": "logo not visible",
              "score": 0, "finding": "x", "reasoning": "y", "confidence": 0.9,
              "best_estimate_deviation": 64}
    result, _ = providers._dim_result("Logo", parsed, "test-model", 0, 0, 0.0)
    assert result["score"] is None
    assert result["state"] == scoring.DimState.NOT_ASSESSABLE
    assert result["internal_coverage"] == 0.0


def test_always_score_when_switched_on_still_marks_the_cell_as_an_estimate(monkeypatch):
    """The demo path. Even switched on it must never produce 'scored', so the
    coverage count, the export and the ladder can all still tell the two apart."""
    monkeypatch.setattr(providers, "ALWAYS_SCORE", True)
    parsed = {"assessable": False, "insufficient_reason": "logo not visible",
              "score": 0, "finding": "x", "reasoning": "y", "confidence": 0.9,
              "best_estimate_deviation": 64}
    result, _ = providers._dim_result("Logo", parsed, "test-model", 0, 0, 0.0)
    assert result["score"] == 64
    assert result["status"] == "estimated"
    assert result["state"] == scoring.DimState.ESTIMATED
    assert result["internal_coverage"] == 0.0
    assert result["confidence"] <= 0.3
    assert "ESTIMATE" in result["finding"]


def test_always_score_leaves_a_real_measurement_alone():
    parsed = {"assessable": True, "insufficient_reason": "", "score": 82,
              "finding": "x", "reasoning": "y", "confidence": 0.9,
              "best_estimate_deviation": 12}
    result, _ = providers._dim_result("Material", parsed, "test-model", 0, 0, 0.0)
    assert result["score"] == 82 and result["status"] == "scored"


def test_coverage_still_reports_only_evidence_backed_dimensions():
    """A fully populated grid must not report 5/5 assessed when only one number
    was measured."""
    dims = [dim("Logo", 55, "estimated"), dim("Stitching", 60, "estimated"),
            dim("Hardware", 50, "estimated"), dim("Label", 36, "scored"),
            dim("Material", 58, "estimated")]
    c = agg(dims)
    assert c["coverage"]["assessed"] == 1
    assert len(c["coverage"]["estimated"]) == 4
    # only the one MEASURED dimension carries weight
    assert c["deviation"] == 36


# ---- graph wiring ----------------------------------------------------------
def test_no_node_name_collides_with_a_state_key():
    """LangGraph forbids a node named after a state key. Older pinned versions
    raise at build_graph() and newer ones do not, so a local build passing is no
    guarantee — this took production down once. Assert it directly."""
    nodes = {n for n in graph.build_graph().get_graph().nodes
             if not n.startswith("__")}
    keys = set(graph.RunState.__annotations__)
    assert not (nodes & keys), f"node names collide with state keys: {nodes & keys}"


def test_graph_has_every_expected_node():
    nodes = {n for n in graph.build_graph().get_graph().nodes if not n.startswith("__")}
    expected = {"intake", "locate", "check_pairing", "label_identity", "upc",
                "aggregate", "synthesize", "build_report"} | {f"dim_{d}" for d in DIMENSIONS}
    assert nodes == expected


# ---- deterministic label failure outranks the vision tier ------------------
def test_label_hard_fail_overrides_a_clean_vision_result():
    """An RN that resolves to another company is decisive on its own — it must
    not be averaged against five reassuring dimension scores."""
    dims = [dim(d, 5) for d in DIMENSIONS]          # every dimension says 'clean'
    c = agg(dims, hard_fail=True)
    assert c["band"] == "hard_fail"
    assert c["verdict_label"] == "Counterfeit — Label Validation Failed"
    assert c["deterministic"] is True
    assert c["failed_checks"] == ["R3"]
    assert c["lane"] == "REJECTED"


def test_label_hard_fail_stands_even_with_no_assessable_dimensions():
    """These checks need no reference and no legible garment — only the tag."""
    dims = [dim(d, None, "abstain", 0.0) for d in DIMENSIONS]
    c = agg(dims, hard_fail=True)
    assert c["band"] == "hard_fail"


def test_no_hard_fail_leaves_the_normal_path_untouched():
    """Asserts the LANE, not the band: the band name moves whenever the ladder is
    retuned, but 'nothing convicted' is the property under test."""
    dims = [dim(d, 5) for d in DIMENSIONS]
    assert agg(dims, hard_fail=False)["lane"] == "CLEARED"


# ---- the verifier ----------------------------------------------------------
def test_the_verifier_classifies_independently():
    """It used to be told to REFUTE the verdict, and refuted 5 cases out of 5 —
    a model told to refute will refute, so the column carried no information."""
    p = providers._chat_verdict.__doc__ or ""
    assert "refute" not in p.lower()
    for label in providers._REVIEW_LABELS:
        assert label in ("Authentic", "Likely Authentic", "Inconclusive",
                         "Insufficient Evidence", "Suspected Counterfeit")


@pytest.mark.parametrize("reviewer,verdict,votes,confirmed", [
    # agreement is about the DECISION, not the wording: 'Authentic' and 'Likely
    # Authentic' both mean cleared, so either agrees with either.
    ("Likely Authentic", "Likely Authentic", "3/3", True),
    ("Authentic", "Likely Authentic", "3/3", True),
    ("Suspected Counterfeit", "Likely Authentic", "0/3", False),
    ("Insufficient Evidence", "Inconclusive", "0/3", False),
])
def test_agreement_is_computed_in_code_not_reported_by_the_model(
        monkeypatch, reviewer, verdict, votes, confirmed):
    def fake(provider, kind, brand, composite, dimensions, upc):
        if kind == "synthesize":
            return {"summary": "s", "key_evidence": []}, {"agent": "Verdict synth."}
        return {"classification": reviewer, "reason": "r"}, {"agent": "Verify"}

    monkeypatch.setattr(providers, "_verdict_call", fake)
    monkeypatch.setattr(providers, "VERIFY_VOTES", 3)
    v, _ = providers.run_verdict("openai", "TNF",
                                 {"verdict_label": verdict, "band": "caution",
                                  "score": 80}, [], {"status": "not_provided"})
    assert v["verifier_votes"] == votes
    assert v["verifier_confirmed"] is confirmed
    assert v["reviewer_labels"] == [reviewer] * 3


# ---- the scale itself ------------------------------------------------------
def test_dimensions_keep_the_deviation_scale():
    """Only the VERDICT is reported as authenticity. A dimension still reports
    deviation, so a near-perfect logo is a LOW dimension number."""
    parsed = {"assessable": True, "insufficient_reason": "", "score": 8,
              "finding": "x", "reasoning": "y", "confidence": 0.9}
    r, _ = providers._dim_result("Logo", parsed, "m", 0, 0, 0.0)
    assert r["score"] == 8                      # unchanged, not inverted
    assert r["band"] != "counterfeit"           # low deviation is not a tell


def test_the_composite_is_reported_on_the_dimensions_own_scale():
    """No conversion anywhere. A composite of 85 means the same thing as a Label
    of 85 — a confirmed critical tell — rather than the opposite."""
    for deviation in (0, 12, 37, 64, 91, 100):
        c = all_measured(deviation)
        assert c["deviation"] == deviation
        assert c["score"] == deviation


def test_the_label_critical_floor_reads_the_same_in_both_places():
    """The regression this scale change exists to prevent: 85 must not mean
    'confirmed critical tell' on the dimension and 'nearly genuine' on the
    composite, on the same row of the same sheet."""
    c = all_measured(85)
    assert c["score"] == 85
    assert c["band"] == "counterfeit"


def test_every_band_has_a_verdict_label():
    for b in ("authentic", "likely_authentic", "caution", "likely_counterfeit",
              "counterfeit", "insufficient", "mismatch", "hard_fail"):
        assert graph._VERDICT_LABEL[b]
        assert scoring.LANE_FOR_BAND[b] in ("CLEARED", "REVIEW", "REJECTED")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))


# ---- rungs 3b / 3c: deterministic material contradictions ------------------
def _mat(care="", fiber="", family=""):
    return {"care_text": care, "fiber_content": fiber, "product_family": family}


def test_spec_contradiction_convicts_over_a_clean_vision_result():
    """GORE-TEX and DRYVENT on one care tag. Both strings were read and both are
    in the mutually-exclusive table — no model judgement is involved."""
    dims = [dim(d, 5) for d in DIMENSIONS]              # every dimension says clean
    c = agg(dims, label_fields=_mat(care="GORE-TEX® PRODUCT. DRYVENT™ 2L SHELL."))
    assert c["verdict_label"] == "Counterfeit — Specification Contradiction"
    assert c["band"] == "hard_fail" and c["lane"] == "REJECTED"
    assert c["deterministic"] is True


def test_spec_contradiction_stands_with_nothing_assessable():
    """Same reason rung 3 sits where it does: it needs the tag, not the garment.
    Convicts from one legible macro with zero dimensions scored."""
    dims = [dim(d, None, "abstain", 0.0) for d in DIMENSIONS]
    c = agg(dims, label_fields=_mat(care="GORE-TEX® AND HYVENT® SHELL"))
    assert c["band"] == "hard_fail"
    assert c["lane"] == "REJECTED"


def test_label_hard_fail_still_wins_over_a_spec_contradiction():
    """Rung 3 is tried before 3b, and the ordering is load-bearing."""
    dims = [dim(d, 5) for d in DIMENSIONS]
    c = agg(dims, hard_fail=True,
            label_fields=_mat(care="GORE-TEX® PRODUCT. DRYVENT™ SHELL."))
    assert c["verdict_label"] == "Counterfeit — Label Validation Failed"


def test_a_clean_material_tag_leaves_the_normal_path_untouched():
    dims = [dim(d, 5) for d in DIMENSIONS]
    c = agg(dims, label_fields=_mat(care="GORE-TEX® PRODUCT. MACHINE WASH.",
                                    fiber="SHELL: 100% NYLON"))
    assert c["lane"] == "CLEARED"


def test_unreadable_material_fields_never_convict():
    """The rule the whole system runs on: absence of evidence is not evidence."""
    dims = [dim(d, 5) for d in DIMENSIONS]
    assert agg(dims, label_fields={})["lane"] == "CLEARED"
    assert agg(dims, label_fields=None)["lane"] == "CLEARED"


def test_new_verdicts_are_adverse_across_engines():
    """An engine reporting either new verdict must drive the combined answer,
    exactly as the existing hard fail does."""
    for v in ("Counterfeit — Specification Contradiction",
              "Counterfeit — Impossible Product"):
        assert v in scoring._ADVERSE
        assert scoring.LANE_FOR_BAND[scoring.BAND_FOR_VERDICT[v]] == "REJECTED"


# ---- Phase 2: deterministic results reaching the dimension ------------------
def test_sub_critical_material_fail_raises_the_material_dimension():
    """GORE-TEX with no registered mark is graded STRONG, so it does not convict
    at rung 3b — but it must still show up on the dimension it concerns."""
    dims = [dim(d, 5) for d in DIMENSIONS]
    c = agg(dims, label_fields=_mat(care="GORE-TEX PRODUCT", family="RESOLVE JACKET"))
    mat = next(d for d in c["dimension_states"] if d["dimension"] == "Material")
    assert mat["state"] == scoring.DimState.MEASURED
    assert mat["deterministic"] is True
    assert c["driver"] == "Material"
    assert c["band"] != "authentic"            # it was 'authentic' without the rule


def test_a_passing_material_check_injects_nothing():
    """GUARDRAIL 1. A clean care tag says nothing about the FABRIC. If a pass
    could buy coverage or lower the score, a counterfeiter with a working
    printer would have bought a clearance."""
    dims = [dim(d, 5) for d in DIMENSIONS]
    clean = agg(dims, label_fields=_mat(care="GORE-TEX® PRODUCT. MACHINE WASH.",
                                        fiber="SHELL: 100% NYLON"))
    none = agg(dims, label_fields={})
    assert clean["coverage_pct"] == none["coverage_pct"]
    assert clean["score"] == none["score"]
    assert clean["band"] == none["band"]
    assert clean["lane"] == "CLEARED"


def test_injection_raises_only_and_never_lowers():
    """A deterministic rule may add suspicion to a dimension and may never
    subtract it."""
    dims = [dim(d, 5) for d in DIMENSIONS]
    dims = [dim("Material", 90) if d["dimension"] == "Material" else d for d in dims]
    c = agg(dims, label_fields=_mat(care="GORE-TEX PRODUCT", family="RESOLVE JACKET"))
    mat = next(d for d in c["dimension_states"] if d["dimension"] == "Material")
    assert mat["score"] == 90                  # the rubric already said worse
    assert mat["deterministic"] is False



def test_deterministic_measurement_cannot_satisfy_the_evidence_gate():
    """GUARDRAIL 2, asserted against the gate itself.

    The deterministic rules read the CARE TAG — the same tag that satisfies the
    label half of this gate. Letting a text check satisfy the other half would
    make the gate assert 'the label, plus the label'.

    Tested on the function rather than through a verdict, because a
    deterministic injection scores 70+ and rung 4b now convicts before the
    ladder ever reaches the gate."""
    label = scoring.Dim("Label", 5, scoring.DimState.MEASURED, 0.8, 1.0)
    det = scoring.Dim("Material", 70, scoring.DimState.MEASURED, 0.85, 0.5,
                      deterministic=True)
    ok, why = scoring.evidence_gate([label, det])
    assert ok is False
    assert "forensic dimension" in why
    assert "Material" not in why          # the deterministic one does not count

    # Two rubric-measured forensic dimensions clear the gate; the deterministic
    # one alongside them is neither required nor counted.
    rubric = scoring.Dim("Material", 5, scoring.DimState.MEASURED, 0.8, 1.0)
    rubric2 = scoring.Dim("Stitching", 5, scoring.DimState.MEASURED, 0.8, 1.0)
    assert scoring.evidence_gate([label, rubric, rubric2])[0] is True
    # ...and one of them on its own does not, which is the change.
    assert scoring.evidence_gate([label, rubric])[0] is False


def test_a_rubric_measured_dimension_does_satisfy_the_gate():
    """The control for the test above — nothing else about the gate moved.

    Two forensic dimensions besides the Label, because clearance now requires
    MIN_FORENSIC_DIMS_FOR_CLEARANCE of them. One legible logo on a garment whose
    stitching, hardware and material were never resolved is not an examination."""
    dims = [dim("Label", 5, coverage=1.0), dim("Material", 5, coverage=1.0),
            dim("Stitching", 5, coverage=1.0)] + [
        dim(d, None, "abstain", 0.0) for d in DIMENSIONS
        if d not in ("Label", "Material", "Stitching")]
    c = agg(dims, label_fields={})
    assert scoring.evidence_gate(
        [scoring.Dim.from_record(d["dimension"], d) for d in dims])[0] is True


def test_dim_defaults_to_non_deterministic():
    """Every stored run predates this flag, so the default must be the value
    that changes nothing."""
    assert scoring.Dim("Logo", 10).deterministic is False
    assert scoring.Dim.from_record("Logo", {"score": 10}).deterministic is False


def test_exporter_prints_every_verdict_verbatim():
    """The Verdict column is the client-facing deliverable. It used to normalise
    an unrecognised wording through a band->verdict map built by INVERTING
    BAND_FOR_VERDICT — and three verdicts now share the hard_fail band, so the
    inversion kept only the last and relabelled every specification
    contradiction as an impossible product."""
    import exporter
    for v in scoring.BAND_FOR_VERDICT:
        rec = {"verdict": v, "band": scoring.BAND_FOR_VERDICT[v]}
        assert exporter._verdict_enum(rec) == v, v


def test_legacy_hard_fail_wording_falls_back_to_the_original_verdict():
    """Every stored run with band=hard_fail predates the other two hard fails."""
    import exporter
    assert exporter._verdict_enum({"verdict": "Counterfeit", "band": "hard_fail"}) ==         "Counterfeit — Label Validation Failed"


def test_every_verdict_has_a_band_and_a_lane():
    for v, band in scoring.BAND_FOR_VERDICT.items():
        assert band in scoring.LANE_FOR_BAND, v
        assert scoring.LANE_FOR_BAND[band] in ("CLEARED", "REVIEW", "REJECTED"), v


# ---- rung 4b: any one dimension in the counterfeit band --------------------
def test_every_dimension_convicts_alone_at_the_band():
    """The mandatory rule. All five, no exceptions, no corroboration."""
    for name in DIMENSIONS:
        dims = [dim(d, (61 if d == name else 2)) for d in DIMENSIONS]
        c = agg(dims)
        assert c["verdict_label"] == "Suspected Counterfeit", name
        assert c["lane"] == "REJECTED", name
        assert c["driver"] == name, name



def test_one_below_the_band_does_not_convict():
    """One point under DIM_COUNTERFEIT is still the caution band."""
    edge = scoring.DIM_COUNTERFEIT - 1
    for name in DIMENSIONS:
        dims = [dim(d, (edge if d == name else 2)) for d in DIMENSIONS]
        assert agg(dims)["verdict_label"] != "Suspected Counterfeit", name



def test_a_partial_dimension_does_not_convict_through_rung_4b():
    """Rung 4b still excludes partials — but that is no longer the whole story.

    A partial never resolved its method class, so it scored on geometry and
    placement, and a rumpled garment alone produces baseline_deviation near 90.
    Rung 4b refuses to convict on that.

    The COMPOSITE does not refuse. A partial Material carries a diagnostic of
    30%, so at a counterfeit floor of 11 any partial above roughly 36 pushes the
    composite into the band and rung 5 convicts anyway. The guard survives on
    the rung it was written for and is bypassed everywhere else; lowering
    DAMP_CEILING and GROUP_FLOOR_FACTOR["geometry"] is what would restore it.
    """
    for score in (61, 80, 99, 100):
        dims = [dim("Material", score, state=scoring.DimState.PARTIAL)]
        dims += [dim(d, 2) for d in DIMENSIONS if d != "Material"]
        c = agg(dims)
        # not via rung 4b — the reason names the composite, not the dimension
        assert "is in the counterfeit band" not in c["reason"], score

    # and below the point where the composite floor reaches the band, nothing
    # convicts at all
    dims = [dim("Material", 20, state=scoring.DimState.PARTIAL)]
    dims += [dim(d, 2) for d in DIMENSIONS if d != "Material"]
    assert agg(dims)["lane"] == "CLEARED"


def test_an_estimated_dimension_never_convicts():
    """A filled cell is not an observation. Four guesses must not outvote one
    measurement, and one guess must not convict on its own."""
    dims = [dim("Material", 95, "estimated", 0.3)]
    dims += [dim(d, 2) for d in DIMENSIONS if d != "Material"]
    assert agg(dims)["verdict_label"] != "Suspected Counterfeit"


def test_a_dimension_below_the_confidence_floor_never_convicts():
    dims = [dim("Material", 95, confidence=0.30)]
    dims += [dim(d, 2) for d in DIMENSIONS if d != "Material"]
    assert agg(dims)["verdict_label"] != "Suspected Counterfeit"


def test_the_band_convicts_regardless_of_coverage():
    """Rung 4b sits above every coverage gate: it does not consult the
    composite, so there is nothing for thin coverage to moderate."""
    dims = [dim("Hardware", 75, coverage=0.2)]
    dims += [dim(d, None, "abstain", 0.0) for d in DIMENSIONS if d != "Hardware"]
    c = agg(dims)
    assert c["verdict_label"] == "Suspected Counterfeit"
    assert c["coverage_pct"] < 0.35


def test_the_worst_dimension_becomes_the_driver():
    dims = [dim("Logo", 65), dim("Material", 88), dim("Label", 70)]
    dims += [dim(d, 2) for d in DIMENSIONS if d not in ("Logo", "Material", "Label")]
    assert agg(dims)["driver"] == "Material"


def test_the_new_knobs_live_in_the_config_block():
    for key in ("DIM_COUNTERFEIT", "PARTIAL_MAY_CONVICT"):
        assert key in scoring.SCORING_CONSTANTS
        assert getattr(scoring, key) == scoring.SCORING_CONSTANTS[key]
    assert scoring.DIM_COUNTERFEIT == scoring.BAND_COUNTERFEIT   # same band, both scales


# ---- clearance may never be cheaper than conviction ------------------------
#
# Four routes by which an item reached a CLEARED verdict on evidence that would
# not have been good enough to reject it. Each was demonstrated against the
# ladder before the guards below existed; each test states the escape it closes.

def test_clearance_needs_the_same_confidence_as_conviction():
    """Five dimensions at 0.35 confidence, all scoring 0, certified Authentic.

    MIN_DIM_CONFIDENCE (35) is the floor to CONTRIBUTE a number. It was also,
    by omission, the floor to release goods — while rejecting them needed
    DISPOSITIVE_CONFIDENCE (60). The asymmetry ran the wrong way round."""
    dims = [dim(d, 0, confidence=0.35) for d in DIMENSIONS]
    c = agg(dims)
    assert c["verdict_label"] == "Insufficient Evidence"
    assert c["rule"] == "R7"
    # ...and the same evidence at clearance confidence still clears, so the
    # guard is a floor and not a blanket refusal. (Certification additionally
    # requires the UPC lookup — without it this is Likely Authentic.)
    assert agg([dim(d, 0, confidence=0.8) for d in DIMENSIONS],
               upc_status="match")["band"] == "authentic"


def test_a_thin_dimension_is_not_a_forensic_examination():
    """The gate counted `state == MEASURED` and nothing else, so a dimension
    that resolved 5% of its own checks stood in for a full one. The Label half
    of the same gate had demanded real internal coverage all along."""
    dims = [dim("Label", 0, confidence=0.8, coverage=1.0),
            dim("Logo", 0, confidence=0.8, coverage=0.05),
            dim("Stitching", 0, confidence=0.8, coverage=0.05)]
    dims += [dim(d, None, "abstain", 0.0) for d in ("Hardware", "Material")]
    ok, why = scoring.evidence_gate([scoring.Dim.from_record(d["dimension"], d)
                                     for d in dims])
    assert not ok and "clearance standard" in why
    assert agg(dims)["verdict_label"] == "Insufficient Evidence"


def test_a_partial_that_cannot_convict_cannot_clear_either():
    """PARTIAL_MAY_CONVICT is False because a partial ran on geometry. That
    same distrust has to point both ways: before R8b, three clean dimensions
    beside a PARTIAL reporting 60/100 returned Likely Authentic."""
    dims = [dim("Label", 0), dim("Logo", 0), dim("Stitching", 0),
            dim("Material", 40, state=scoring.DimState.PARTIAL),
            dim("Hardware", None, "not_applicable", 0.0)]
    c = agg(dims)
    assert c["verdict_label"] == "Inconclusive — Suspicious"
    assert c["rule"] == "R8b"
    assert c["lane"] == "REVIEW"
    # Past PARTIAL_DISPOSITIVE the same shape stops being ambiguous: 55+ on
    # geometry and placement is twice the photographic-noise ceiling, and R4c
    # convicts rather than parking it in review.
    dims[3] = dim("Material", 60, state=scoring.DimState.PARTIAL)
    c = agg(dims)
    assert c["verdict_label"] == "Suspected Counterfeit" and c["rule"] == "R4c"


def test_a_visible_critical_tell_can_never_average_into_a_clearance():
    """An L3 fibre-content misspelling at confidence 0.55, beside seven genuine
    checks, averaged to a Label score of 20 — Likely Authentic. The same tell at
    0.60 floored to 85. A five-point confidence swing decided release."""
    others = [lcheck(c, "genuine") for c in ("L1", "L2", "L5", "L6", "L8", "L10", "L11")]
    for conf in (0.55, 0.60, 0.95):
        agg_l = providers._aggregate_label([lcheck("L3", "counterfeit_tell", conf)] + others)
        assert agg_l["score"] >= scoring.DIM_COUNTERFEIT, conf
    # Graded, not flattened: an uncertain tell must not claim the certainty of
    # a confident one.
    assert (providers._aggregate_label([lcheck("L3", "counterfeit_tell", 0.55)] + others)["score"]
            < providers._aggregate_label([lcheck("L3", "counterfeit_tell", 0.60)] + others)["score"])


def test_not_applicable_cannot_shrink_the_label_denominator():
    """`not_applicable` is the one status that LEAVES the denominator, which
    makes it the most valuable string a model can emit to get an item cleared.
    Marking the care-tag checks not_applicable instead of not_visible took
    internal coverage from 0.25 to 1.00 on identical evidence, clearing the
    gate whose whole purpose is to require that the care tag was read."""
    care = ("L3", "L4", "L5", "L6", "L8", "L10", "L11")
    seen = [lcheck("L1", "genuine"), lcheck("L2", "genuine"),
            lcheck("L7", "not_applicable")]          # genuinely inapplicable: cotton tee
    honest = providers._aggregate_label(
        [lcheck(c, "not_visible") for c in care] + seen)
    gamed = providers._aggregate_label(
        [lcheck(c, "not_applicable") for c in care] + seen)
    assert gamed["internal_coverage"] == honest["internal_coverage"]
    assert gamed["internal_coverage"] < scoring._pc(scoring.LABEL_EVIDENCE_COVERAGE)
    # L7 is the sole whitelisted exception and must still work.
    assert "L7" in providers._LABEL_NA_ALLOWED and len(providers._LABEL_NA_ALLOWED) == 1


def test_the_clearance_floors_live_in_the_config_block():
    for key in ("MIN_CONFIDENCE_FOR_CLEARANCE", "MIN_INTERNAL_COVERAGE_FOR_CLEARANCE",
                "PARTIAL_MAY_CLEAR"):
        assert key in scoring.SCORING_CONSTANTS
        assert getattr(scoring, key) == scoring.SCORING_CONSTANTS[key]
    # Releasing an item must never be easier than rejecting one.
    assert scoring.MIN_CONFIDENCE_FOR_CLEARANCE >= scoring.DISPOSITIVE_CONFIDENCE
    assert scoring.MIN_CONFIDENCE_FOR_CLEARANCE > scoring.MIN_DIM_CONFIDENCE
    # A state distrusted for conviction must be distrusted for clearance.
    assert scoring.PARTIAL_MAY_CLEAR == scoring.PARTIAL_MAY_CONVICT
