"""Regression tests for the insufficient-evidence handling.

The bug these lock down: a dimension agent that could not see its region was
forced to return an integer, and the aggregator averaged that fabricated number
into a composite. The canonical failure is the `test1` case — a folded macro
shot of a down-jacket care label paired against a cotton t-shirt reference —
which produced three different confident scores (53 / 33 / 42) across engines.

Run:  pytest backend/test_scoring.py -q      (from the repo root)
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import graph                                                  # noqa: E402
import providers                                              # noqa: E402
from references import DIMENSIONS                              # noqa: E402


# ---- helpers ---------------------------------------------------------------
def dim(name, score, status="scored", confidence=0.8):
    return {"dimension": name, "score": score, "band": graph._band(score),
            "finding": "f", "reasoning": "r", "box": None,
            "confidence": confidence, "status": status}


def state(dims, upc_status="not_provided", pairing_status="ok", hard_fail=False):
    return {"dimension_results": dims,
            "upc_result": {"status": upc_status},
            "pairing": {"status": pairing_status, "note": "n"},
            "label_id": {"validation": {"hard_fail": hard_fail, "failed": ["R3"],
                                        "summary": "RN resolves to another company."}}}


def agg(dims, **kw):
    return graph.aggregate_node(state(dims, **kw))["composite"]


# ---- the abstain gate ------------------------------------------------------
def test_unassessable_dimension_yields_no_score():
    """A model saying it cannot evaluate must not produce a number."""
    parsed = {"assessable": False, "insufficient_reason": "logo not visible",
              "score": 0, "finding": "x", "reasoning": "y", "confidence": 0.9}
    result, _usage = providers._dim_result("Logo", parsed, "test-model", 0, 0, 0.0)
    assert result["score"] is None
    assert result["status"] == "abstain"
    assert "INSUFFICIENT" in result["finding"]


def test_low_confidence_is_treated_as_abstention():
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
    assert result["band"] == "counterfeit"


# ---- the coverage floor ----------------------------------------------------
def test_too_few_assessed_dimensions_returns_recapture():
    """The test1 shape: most dimensions unassessable -> no composite at all."""
    dims = [dim("Logo", None, "abstain", 0.0), dim("Stitching", None, "abstain", 0.0),
            dim("Hardware", None, "abstain", 0.0), dim("Label", 0),
            dim("Material", None, "abstain", 0.0)]
    c = agg(dims)
    assert c["score"] is None
    assert c["band"] == "insufficient"
    assert c["verdict_label"] == "Insufficient Evidence — Recapture"
    assert c["coverage"]["assessed"] == 1


def test_enough_coverage_scores_over_assessed_only():
    """Abstentions are dropped, not counted as zeros."""
    dims = [dim("Logo", 80), dim("Stitching", 80), dim("Hardware", None, "abstain", 0.0),
            dim("Label", 80), dim("Material", None, "abstain", 0.0)]
    c = agg(dims)
    # 80 across every assessed dimension must average to 80 — if the two
    # abstentions were coerced to 0 this would come out near 48.
    assert c["score"] == 80
    assert c["coverage"]["assessed"] == 3
    assert set(c["coverage"]["abstained"]) == {"Hardware", "Material"}


# ---- the UPC bias ----------------------------------------------------------
def test_missing_upc_image_does_not_inflate_the_score():
    """'No barcode photo supplied' is the absence of a check, not a finding.
    This previously added +6 to every run in the corpus."""
    dims = [dim(d, 50) for d in DIMENSIONS]
    assert agg(dims, upc_status="not_provided")["score"] == 50
    assert agg(dims, upc_status="unreadable")["score"] == 50


def test_real_upc_mismatch_still_counts():
    dims = [dim(d, 50) for d in DIMENSIONS]
    assert agg(dims, upc_status="mismatch")["score"] == 56
    assert agg(dims, upc_status="nomatch")["score"] == 56


# ---- the pairing gate ------------------------------------------------------
def test_reference_mismatch_voids_the_run():
    dims = [dim(d, 90) for d in DIMENSIONS]
    c = agg(dims, pairing_status="mismatch")
    assert c["score"] is None
    assert c["band"] == "mismatch"
    assert "Cannot Compare" in c["verdict_label"]


def test_mismatch_short_circuits_dimension_agents(monkeypatch):
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


# ---- the label cap ---------------------------------------------------------
def test_counterfeit_verdict_capped_without_a_readable_label():
    dims = [dim("Logo", 90), dim("Stitching", 90), dim("Hardware", 90),
            dim("Label", None, "abstain", 0.0), dim("Material", 90)]
    c = agg(dims)
    assert c["score"] == 90
    assert c["band"] == "caution"       # held back from 'counterfeit'
    assert c["capped"] is True


def test_counterfeit_verdict_stands_with_a_label():
    dims = [dim(d, 90) for d in DIMENSIONS]
    c = agg(dims)
    assert c["band"] == "counterfeit"
    assert c["capped"] is False


# ---- the whole test1 shape, end to end ------------------------------------
def test_test1_case_produces_no_number():
    """The regression this whole change exists for.

    Suspect: folded macro of a down-jacket care label. Reference: cotton tee.
    Correct behaviour is a recapture request from every engine — not 53/33/42.
    """
    dims = [dim("Logo", None, "abstain", 0.0), dim("Stitching", None, "abstain", 0.0),
            dim("Hardware", None, "abstain", 0.0), dim("Label", None, "abstain", 0.0),
            dim("Material", None, "abstain", 0.0)]
    c = agg(dims, pairing_status="mismatch")
    assert c["score"] is None, "a fabricated composite came back for an unscorable case"
    assert c["band"] in ("mismatch", "insufficient")

    # …and with the pairing gate unavailable, the coverage floor must still catch it.
    c2 = agg(dims, pairing_status="skipped")
    assert c2["score"] is None
    assert c2["band"] == "insufficient"


# ---- the Logo forensic rubric ---------------------------------------------
def prim(name, deviation, confidence="high", evidence="e"):
    return {"name": name, "deviation": deviation, "evidence": evidence,
            "confidence": confidence}


def logo(primitives, method="embroidery", reference_used=True):
    return providers._aggregate_logo({
        "reference_used": reference_used, "application_method": method,
        "primitives": primitives, "logo_deviation": None, "assessment": "",
        "top_deviations": [], "capture_issues": [], "recapture_instructions": ["r"]})


def test_logo_without_a_reference_never_assesses():
    """HARD RULE 1 — no reference means no assessment, ever, not a guess from
    memory of what the brand's logo looks like."""
    a = logo([prim("arc_radius_ratios", 10)], reference_used=False)
    assert a["score"] is None
    assert a["assessment"] == "NO_REFERENCE"
    assert a["status"] == "abstain"


def test_logo_rollup_uses_max_of_mean_and_85pct_of_worst():
    """One severe primitive must not be averaged away by many clean ones."""
    prims = [prim("arc_radius_ratios", 0), prim("inter_arc_gap", 0),
             prim("stroke_uniformity", 0), prim("bounding_box_ratio", 0),
             prim("satin_angle_consistency", 90), prim("stitch_density_cv", 0),
             prim("underlay_present", 0), prim("edge_thread_creep", 0),
             prim("thread_sheen", 0), prim("backing_visible_reverse", 0)]
    a = logo(prims)
    # weighted mean is well under 0.85*90 = 76.5, so the worst-primitive floor wins
    assert a["score"] == round(0.85 * 90)        # 76 (banker's rounding on .5)
    assert a["assessment"] == "deviation_significant"
    assert a["top_deviations"][0] == "satin_angle_consistency"


def test_logo_application_primitives_weigh_triple():
    """Application evidence is 3x geometry — a clean outline cannot outvote a
    bad application."""
    heavy = logo([prim("arc_radius_ratios", 0), prim("satin_angle_consistency", 40),
                  prim("stitch_density_cv", 40), prim("underlay_present", 40),
                  prim("edge_thread_creep", 40), prim("thread_sheen", 40),
                  prim("backing_visible_reverse", 40)])
    assert heavy["weighted_mean"] > 35           # 1x zero barely moves it


def test_logo_insufficient_application_primitive_blocks_scoring():
    """Any unresolved application primitive -> INSUFFICIENT_CAPTURE."""
    a = logo([prim("arc_radius_ratios", 5), prim("inter_arc_gap", 5),
              prim("satin_angle_consistency", "INSUFFICIENT")])
    assert a["score"] is None
    assert a["assessment"] == "INSUFFICIENT_CAPTURE"
    assert "satin_angle_consistency" in a["finding"]


def test_logo_three_insufficient_primitives_blocks_scoring():
    a = logo([prim("arc_radius_ratios", 5), prim("inter_arc_gap", "INSUFFICIENT"),
              prim("stroke_uniformity", "INSUFFICIENT"),
              prim("terminal_geometry", "INSUFFICIENT")])
    assert a["score"] is None
    assert a["assessment"] == "INSUFFICIENT_CAPTURE"


def test_logo_unknown_application_method_blocks_scoring():
    a = logo([prim("arc_radius_ratios", 5), prim("inter_arc_gap", 5)], method="UNKNOWN")
    assert a["score"] is None
    assert a["assessment"] == "INSUFFICIENT_CAPTURE"


def test_logo_clean_comparison_scores_consistent():
    prims = [prim("arc_radius_ratios", 2), prim("inter_arc_gap", 3),
             prim("satin_angle_consistency", 4), prim("stitch_density_cv", 2),
             prim("underlay_present", 0), prim("edge_thread_creep", 5),
             prim("thread_sheen", 3), prim("backing_visible_reverse", 1)]
    a = logo(prims)
    assert a["status"] == "scored"
    assert a["assessment"] == "consistent_with_reference"
    assert a["score"] <= 30


def test_logo_never_emits_a_counterfeit_verdict():
    """HARD RULE 6 — the adverse ceiling is 'deviation_significant'."""
    a = logo([prim("arc_radius_ratios", 100), prim("satin_angle_consistency", 100),
              prim("stitch_density_cv", 100), prim("underlay_present", 100),
              prim("edge_thread_creep", 100), prim("thread_sheen", 100),
              prim("backing_visible_reverse", 100)])
    assert a["score"] == 100
    assert a["assessment"] == "deviation_significant"
    assert "counterfeit" not in a["finding"].lower()


def test_logo_prompt_carries_every_rubric_primitive():
    """The prompt the model actually receives must name every primitive."""
    for name in (_LOGO_ALL := providers._LOGO_PRIMITIVE_NAMES):
        assert name in providers._LOGO_PROMPT, f"{name} missing from the Logo prompt"
    for rule in ("NO_REFERENCE", "INSUFFICIENT", "deviation_significant",
                 "scale-invariant", "weight 3x"):
        assert rule in providers._LOGO_PROMPT


def test_logo_abstention_flows_through_to_the_composite():
    """An INSUFFICIENT_CAPTURE logo must reach the aggregator as score=None."""
    a = logo([prim("satin_angle_consistency", "INSUFFICIENT")])
    result = providers._logo_result(a)
    assert result["score"] is None and result["status"] == "abstain"
    c = agg([result, dim("Stitching", 40), dim("Hardware", 40),
             dim("Label", 40), dim("Material", 40)])
    assert c["coverage"]["assessed"] == 4
    assert "Logo" in c["coverage"]["abstained"]


# ---- the same rubric treatment across all four dimensions -----------------
# (dimension, a valid method, a heavy 3x primitive, a light 1x primitive)
RUBRIC_CASES = [
    ("Logo", "embroidery", "satin_angle_consistency", "arc_radius_ratios"),
    ("Stitching", "overlock", "thread_count_in_overlock", "stitch_pitch_ratio"),
    ("Hardware", "zip", "foundry_code", "pull_dimension_ratios"),
    ("Material", "woven", "weave_type", "sheen_at_angle"),
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
        assert name in p, f"{dim}: heavy primitive {name} missing from prompt"
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
def test_rubric_schema_is_wellformed(dim, method, heavy, light):
    spec = providers._RUBRICS[dim]
    s = providers._rubric_schema(dim)
    assert set(s["required"]) == set(s["properties"])       # strict-mode requirement
    assert s["additionalProperties"] is False
    assert method in s["properties"][spec["method_key"]]["enum"]
    assert "UNKNOWN" in s["properties"][spec["method_key"]]["enum"]


@pytest.mark.parametrize("dim,method,heavy,light", RUBRIC_CASES, ids=RUBRIC_IDS)
def test_rubric_no_reference_never_assesses(dim, method, heavy, light):
    a = rubric(dim, [prim(light, 10)], method, reference_used=False)
    assert a["score"] is None and a["assessment"] == "NO_REFERENCE"


@pytest.mark.parametrize("dim,method,heavy,light", RUBRIC_CASES, ids=RUBRIC_IDS)
def test_rubric_unknown_method_blocks_scoring(dim, method, heavy, light):
    a = rubric(dim, [prim(light, 5)], "UNKNOWN")
    assert a["score"] is None and a["assessment"] == "INSUFFICIENT_CAPTURE"


@pytest.mark.parametrize("dim,method,heavy,light", RUBRIC_CASES, ids=RUBRIC_IDS)
def test_rubric_insufficient_heavy_primitive_blocks_scoring(dim, method, heavy, light):
    a = rubric(dim, [prim(light, 5), prim(heavy, "INSUFFICIENT")], method)
    assert a["score"] is None and a["assessment"] == "INSUFFICIENT_CAPTURE"
    assert heavy in a["finding"]


@pytest.mark.parametrize("dim,method,heavy,light", RUBRIC_CASES, ids=RUBRIC_IDS)
def test_rubric_heavy_group_weighs_triple(dim, method, heavy, light):
    assert providers._rubric_weight(dim, heavy, method) == 3
    assert providers._rubric_weight(dim, light, method) == 1


@pytest.mark.parametrize("dim,method,heavy,light", RUBRIC_CASES, ids=RUBRIC_IDS)
def test_rubric_worst_primitive_floor_applies(dim, method, heavy, light):
    a = rubric(dim, [prim(light, 0), prim(heavy, 80)], method)
    # weighted mean = (0*1 + 80*3)/4 = 60; floor = 0.85*80 = 68 -> floor wins
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
                                                             monkeypatch):
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
        return ({"error": "", "reference_used": True, spec["method_key"]: method,
                 "primitives": [prim(light, 10), prim(heavy, 10)],
                 spec["dev_key"]: None, "assessment": "", "top_deviations": [],
                 "capture_issues": [], "recapture_instructions": []}, 10, 5)

    monkeypatch.setattr(providers, "_chat", fake_chat)
    res, _ = providers._chat_dimension({"label": "t"}, dim, ["img"], ["ref"], 0.0)
    assert seen["schema_name"] == dim.lower()
    assert seen["prompt"] == providers._RUBRICS[dim]["prompt"]
    assert res["score"] == 10 and res["method"] == method


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


def test_label_hard_fail_stands_even_with_no_assessable_dimensions():
    """These checks need no reference and no legible garment — only the tag."""
    dims = [dim(d, None, "abstain", 0.0) for d in DIMENSIONS]
    c = agg(dims, hard_fail=True, pairing_status="mismatch")
    assert c["band"] == "hard_fail"


def test_no_hard_fail_leaves_the_normal_path_untouched():
    dims = [dim(d, 5) for d in DIMENSIONS]
    assert agg(dims, hard_fail=False)["band"] == "authentic"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
