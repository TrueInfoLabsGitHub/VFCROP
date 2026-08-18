"""Acceptance tests for the scoring correctness fix.

These are the ten checks the specification names, in its own order. Rows 39-43
are real rows from a production export in which the system cleared four
counterfeits; each is reproduced here as a fixture and must now fail closed.

The invariants at the end matter more than the individual rows:

  * NO ESCAPE — nothing may be cleared unless the care tag was actually read
    and one other forensic dimension was measured;
  * NO DILUTION — adding a guess must not move the composite by one point;
  * MONOTONICITY — raising a contributing dimension must never lower the score.

Run:  pytest backend/test_acceptance.py -q      (from the repo root)
"""
import itertools
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import scoring                                                 # noqa: E402
from scoring import DimState                                    # noqa: E402

DIMS = ("Logo", "Stitching", "Hardware", "Label", "Material")


def d(name, score, state=DimState.ESTIMATED, coverage=0.0, confidence=None):
    """One dimension result. Confidence defaults to what each state implies: a
    measurement is confident, an estimate is not."""
    if confidence is None:
        confidence = 0.8 if state in (DimState.MEASURED, DimState.PARTIAL) else 0.2
    return scoring.Dim(name, score, state, confidence, coverage)


def decide(dims, category="", **kw):
    return scoring.decide(list(dims), category=category, **kw)


# ---------------------------------------------------------------------------
# 1-4. The production rows that were cleared and should not have been.
# ---------------------------------------------------------------------------
def test_1_row_43_label_tell_is_no_longer_averaged_away():
    """Row 43 — Logo T-Shirt. Label = 85 is the critical-tell floor: a
    high-confidence CRITICAL tell (L3 fibre content or L7 Gore-Tex) was
    confirmed. The plain mean averaged it against four guesses down to 28 and
    cleared the item as Likely Authentic."""
    r = decide([
        d("Logo", 18), d("Stitching", 20), d("Hardware", 0),
        d("Label", 85, DimState.MEASURED, 0.44, 0.8), d("Material", 18),
    ], category="t-shirt")
    assert r["verdict_label"] == "Suspected Counterfeit"
    assert r["driver"] == "Label"
    assert r["lane"] == "REJECTED"


def test_2_rows_40_and_41_thin_label_coverage_is_not_a_clearance():
    """Rows 40 (kids' swimsuit) and 41 (Nuptse jacket). Label = 0 MEASURED, but
    built from 12% of the label's severity weight — one supporting check. Both
    were cleared as Likely Authentic on that."""
    for name, cat, dims in [
        ("40 swimsuit", "swimsuit", [d("Logo", 28), d("Stitching", 30), d("Hardware", 15),
                                     d("Label", 0, DimState.MEASURED, 0.12, 0.8),
                                     d("Material", 35)]),
        ("41 nuptse", "jacket", [d("Logo", 28), d("Stitching", 15), d("Hardware", 20),
                                 d("Label", 0, DimState.MEASURED, 0.12, 0.8),
                                 d("Material", 20)]),
    ]:
        r = decide(dims, category=cat)
        assert r["verdict_label"] == "Insufficient Evidence", name
        assert r["coverage_pct"] < 0.10, name
        assert r["recapture"], f"{name}: no shot list was emitted"


def test_3_row_39_five_estimates_produce_no_number_at_all():
    """Row 39 — Evolution T-Shirt. Nothing was measured. The old pipeline emitted
    a composite anyway, which reads to whoever opens the sheet exactly like one
    backed by five measurements."""
    r = decide([d("Logo", 70), d("Stitching", 20), d("Hardware", 0),
                d("Label", 50), d("Material", 20)], category="t-shirt")
    assert r["verdict_label"] == "Insufficient Evidence"
    assert r["deviation"] is None, "a composite was emitted from five guesses"
    assert r["score"] is None
    assert r["coverage_pct"] == 0.0


def test_4_row_42_a_beanie_has_no_hardware():
    """Row 42 — Logo Box Beanie. Hardware scored 0, which the mean read as
    'assessed and clean' and which inflated the denominator."""
    dims = [d("Logo", 35), d("Stitching", 18), d("Hardware", 0),
            d("Label", 50, DimState.MEASURED, 0.33, 0.8), d("Material", 14)]
    r = decide(dims, category="beanie")
    hardware = next(x for x in r["dimension_states"] if x["dimension"] == "Hardware")
    assert hardware["state"] == DimState.NOT_APPLICABLE
    assert hardware["score"] is None, "a dimension the product does not have scored 0"
    assert hardware["effective_weight"] == 0
    assert r["verdict_label"] != "Likely Authentic"


def test_4b_a_runtime_applicable_false_beats_the_category_table():
    """The agent looked at the photographs; the table did not."""
    dims = [d("Logo", 10, DimState.MEASURED, 1.0), d("Stitching", 10),
            d("Hardware", 0), d("Label", 5, DimState.MEASURED, 1.0),
            d("Material", 10)]
    r = decide(dims, category="jacket", runtime_not_applicable=["Hardware"])
    hardware = next(x for x in r["dimension_states"] if x["dimension"] == "Hardware")
    assert hardware["state"] == DimState.NOT_APPLICABLE


def test_4c_a_measured_dimension_survives_the_category_table():
    """If the agent found and scored hardware, the item has hardware, whatever
    the category says it should have."""
    dims = [d("Hardware", 40, DimState.MEASURED, 1.0)]
    r = decide(dims, category="t-shirt")
    hardware = next(x for x in r["dimension_states"] if x["dimension"] == "Hardware")
    assert hardware["state"] == DimState.MEASURED


# ---------------------------------------------------------------------------
# 5. The one that matters most.
# ---------------------------------------------------------------------------
_STATES = (DimState.MEASURED, DimState.PARTIAL, DimState.ESTIMATED,
           DimState.NOT_ASSESSABLE, DimState.NOT_APPLICABLE, DimState.FAILED)
_SCORES = (None, 0, 5, 30, 60, 90)
_COVERAGES = (0.0, 0.12, 0.5, 1.0)


def _cleared(verdict):
    return verdict in ("Authentic", "Likely Authentic")


def test_5_no_escape_invariant():
    """Property test: NO input may produce Authentic or Likely Authentic unless
    Label is MEASURED with internal_coverage >= 0.5 and at least one other
    forensic dimension is MEASURED.

    Coverage alone is gameable — 60% is reachable from Logo and Material without
    the care tag ever having been examined, and the care tag is where the tells
    live."""
    checked = 0
    for lbl_state, lbl_cov, lbl_score in itertools.product(_STATES, _COVERAGES, _SCORES):
        for other_state, other_score in itertools.product(_STATES, _SCORES):
            dims = [d("Label", lbl_score, lbl_state, lbl_cov),
                    d("Logo", other_score, other_state, 1.0),
                    d("Stitching", other_score, other_state, 1.0),
                    d("Hardware", other_score, other_state, 1.0),
                    d("Material", other_score, other_state, 1.0)]
            r = decide(dims)
            checked += 1
            if not _cleared(r["verdict_label"]):
                continue
            label = next(x for x in r["dimension_states"] if x["dimension"] == "Label")
            assert label["state"] == DimState.MEASURED, \
                f"cleared with a {label['state']} label: {r['verdict_label']}"
            assert label["internal_coverage"] >= scoring.LABEL_EVIDENCE_COVERAGE / 100, \
                f"cleared on {label['internal_coverage']:.0%} label coverage"
            others = [x for x in r["dimension_states"]
                      if x["dimension"] != "Label" and x["state"] == DimState.MEASURED
                      and x["score"] is not None]
            assert others, "cleared on the label alone"
    assert checked > 500, "the property test did not actually explore the space"


def test_5b_the_gate_reports_why_it_refused():
    dims = [d("Label", 0, DimState.MEASURED, 0.12, 0.8),
            d("Logo", 10, DimState.MEASURED, 1.0, 0.8)]
    r = decide(dims)
    assert r["verdict_label"] == "Insufficient Evidence"
    assert "label checks" in r["reason"]


# ---------------------------------------------------------------------------
# 6-8. Arithmetic invariants.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("guess", [0, 25, 50, 75, 100])
def test_6_dilution_invariant(guess):
    """Adding an ESTIMATED dimension must not move the composite by one point.
    Four guesses used to outvote one measurement."""
    base = [d("Label", 40, DimState.MEASURED, 1.0), d("Logo", 20, DimState.MEASURED, 1.0)]
    before = decide(list(base))["deviation"]
    for name in ("Stitching", "Hardware", "Material"):
        base.append(d(name, guess))
        assert decide(list(base))["deviation"] == before, \
            f"an ESTIMATED {name}={guess} moved the composite"


def test_6b_non_contributing_states_are_all_inert():
    base = [d("Label", 40, DimState.MEASURED, 1.0), d("Logo", 20, DimState.MEASURED, 1.0)]
    before = decide(list(base))["deviation"]
    for state in (DimState.ESTIMATED, DimState.NOT_ASSESSABLE,
                  DimState.NOT_APPLICABLE, DimState.FAILED):
        assert decide(base + [d("Hardware", 95, state, 1.0)])["deviation"] == before, state


def test_7_monotonicity():
    """Raising any contributing dimension's score must never lower the composite."""
    for target in DIMS:
        last = -1
        for score in (0, 10, 20, 40, 60, 80, 100):
            dims = [d(n, 30 if n != target else score, DimState.MEASURED, 1.0)
                    for n in DIMS]
            dev = decide(dims)["deviation"]
            assert dev >= last, f"raising {target} to {score} lowered the composite"
            last = dev


def test_8_group_normalisation():
    """An embroidery logo (6 method primitives) and a screen logo (4) with
    identical PER-GROUP mean deviations must produce identical dimension scores.

    Under per-primitive weighting the method group's share drifted with how many
    primitives the method happened to have — embroidery 55%, rubberised 50%,
    screen 44% — so two items were scored on different instruments and then
    compared as if they were the same."""
    def prims(n_method, method_dev, geo_dev):
        rows = [{"name": f"m{i}", "deviation": method_dev, "group": "method",
                 "confidence": 0.9} for i in range(n_method)]
        rows += [{"name": f"g{i}", "deviation": geo_dev, "group": "geometry",
                  "confidence": 0.9} for i in range(2)]
        return rows

    six = scoring.roll_up_primitives(prims(6, 40, 10))
    four = scoring.roll_up_primitives(prims(4, 40, 10))
    assert six[0] == four[0]
    assert six[2] == four[2] == 0.8          # method .50 + geometry .30


def test_8b_a_missing_group_lowers_internal_coverage():
    geometry_only = scoring.roll_up_primitives(
        [{"name": "g", "deviation": 40, "group": "geometry", "confidence": 0.9}])
    assert geometry_only[1] == DimState.PARTIAL
    assert geometry_only[2] == 0.3


# ---------------------------------------------------------------------------
# 9. Cross-engine.
# ---------------------------------------------------------------------------
def test_9_one_engine_with_a_dispositive_defect_decides_the_case():
    """Never average engines — that is the dilution bug one level up. The single
    engine that actually resolved the foundry code must not be voted down by the
    two that could not."""
    case = scoring.combine_engines({
        "GPT-5.5": {"verdict_label": "Likely Authentic", "band": "likely_authentic",
                    "score": 80, "coverage_pct": 0.7},
        "Gemini 3.1 Pro": {"verdict_label": "Likely Authentic", "band": "likely_authentic",
                           "score": 78, "coverage_pct": 0.7},
        "Kimi K2.6": {"verdict_label": "Suspected Counterfeit", "band": "counterfeit",
                      "score": 22, "coverage_pct": 0.6, "reason": "foundry code wrong"},
    })
    assert case["verdict_label"] == "Suspected Counterfeit"
    assert case["lane"] == "REJECTED"
    assert "Kimi K2.6" in case["reason"]


def test_9b_a_wide_spread_forces_human_review():
    case = scoring.combine_engines({
        "GPT-5.5": {"verdict_label": "Likely Authentic", "band": "likely_authentic",
                    "score": 90, "coverage_pct": 0.8},
        "Gemini 3.1 Pro": {"verdict_label": "Authentic", "band": "authentic",
                           "score": 20, "coverage_pct": 0.8},
    })
    assert case["spread"] == 70
    assert case["lane"] == "REVIEW"


def test_9c_unanimous_clearance_stays_cleared():
    case = scoring.combine_engines({
        "GPT-5.5": {"verdict_label": "Likely Authentic", "band": "likely_authentic",
                    "score": 88, "coverage_pct": 0.8},
        "Gemini 3.1 Pro": {"verdict_label": "Likely Authentic", "band": "likely_authentic",
                           "score": 84, "coverage_pct": 0.7},
    })
    assert case["lane"] == "CLEARED"


def test_9d_an_old_record_is_read_by_its_band_not_its_wording():
    """Runs stored before the ladder existed say 'Counterfeit', not 'Suspected
    Counterfeit'. The band is the stable identifier; the wording is not."""
    case = scoring.combine_engines({
        "GPT-5.5": {"verdict_label": "Counterfeit", "band": "counterfeit", "score": 20},
    })
    assert case["lane"] == "REJECTED"


# ---------------------------------------------------------------------------
# 10. Backward compatibility.
# ---------------------------------------------------------------------------
def test_10_a_stored_run_without_state_or_coverage_still_loads():
    """Old run JSON lacks both fields. Missing state is ESTIMATED and missing
    internal_coverage is 0.0 — both the conservative direction, so reloading an
    old run can never promote it into a clearance."""
    old = {"score": 20, "finding": "f"}
    dim = scoring.Dim.from_record("Label", old)
    assert dim.state == DimState.ESTIMATED
    assert dim.internal_coverage == 0.0
    assert dim.contributes is False

    r = decide([scoring.Dim.from_record(n, {"score": 20}) for n in DIMS])
    assert r["verdict_label"] == "Insufficient Evidence"
    assert r["deviation"] is None


def test_10b_a_legacy_status_field_maps_to_a_state():
    """Records written between the two designs carry `status`, not `state`."""
    assert scoring.Dim.from_record("Logo", {"status": "scored"}).state == DimState.MEASURED
    assert scoring.Dim.from_record("Logo", {"status": "abstain"}).state == DimState.NOT_ASSESSABLE
    assert scoring.Dim.from_record("Logo", {"status": "error"}).state == DimState.FAILED
    assert scoring.Dim.from_record("Logo", {}).state == DimState.ESTIMATED


def test_10c_an_empty_record_does_not_crash():
    assert scoring.Dim.from_record("Logo", None).score is None
    assert scoring.combine_engines({})["verdict_label"] == "Run Failed"
    assert scoring.decide([])["verdict_label"] == "Insufficient Evidence"


# ---------------------------------------------------------------------------
# The constants block itself.
# ---------------------------------------------------------------------------
def test_every_constant_is_an_unfitted_default_in_one_place():
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "scoring.py"), encoding="utf-8").read()
    assert "UNFITTED DEFAULTS" in src, \
        "the config block must say plainly that nothing here has been fitted"
    # every name the ladder uses is derived from the block, not defined twice
    for key, value in scoring.SCORING_CONSTANTS.items():
        assert getattr(scoring, key) == value, f"{key} drifted from SCORING_CONSTANTS"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
