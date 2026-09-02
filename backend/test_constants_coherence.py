"""Invariants between the scoring constants, asserted as arithmetic.

WHY THIS FILE EXISTS. The bands were changed twice — 61 -> 31 -> 11 — and each
time two other constants were left where they were:

  * DAMP_CEILING (50) caps exposure-sensitive primitives so glare and sheen
    could "suggest suspicious and never more". Through the method group's 85%
    floor that is 42, which was FOUR TIMES a conviction floor of 11. A cap whose
    whole purpose was to prevent conviction was convicting.
  * GROUP_FLOOR_FACTOR["geometry"] (60) against the ~90 baseline_deviation a
    rumpled garment produces is 54, also far above 11. Creasing alone convicted.

Both were recorded in scoring.py's own comments as known hazards, and both
shipped anyway, because nothing checked the RELATIONSHIP between the constants —
only their individual values. These tests check the relationships. A future
reband that reopens either hole fails here instead of reaching a customer.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import scoring                                                    # noqa: E402
from scoring import Dim, DimState, decide, roll_up_primitives     # noqa: E402

# The geometry deviation a creased-but-genuine garment produces on its own.
# Recorded in scoring.py's GROUP_FLOOR_FACTOR comment; kept here as the number
# the invariant is measured against rather than as a magic constant in a test.
RUMPLED_GARMENT_BASELINE = 90


def _pc(v):
    return v / 100.0


def _measured(name, score, conf=0.8):
    return Dim(name, score, DimState.MEASURED, conf, 1.0)


# ---------------------------------------------------------------------------
# The two hazards that shipped
# ---------------------------------------------------------------------------
def test_no_damped_primitive_can_convict_on_its_own():
    """A primitive capped by DAMP_CEILING is one the system has already decided
    it does not trust — exposure moves it more than authenticity does. It must
    not be able to reach the conviction floor through the group floor."""
    worst = _pc(scoring.GROUP_FLOOR_FACTOR["method"]) * scoring.DAMP_CEILING
    assert worst < scoring.DIM_COUNTERFEIT, (
        f"a fully-damped exposure primitive rolls up to {worst:.0f}, at or above "
        f"the conviction floor of {scoring.DIM_COUNTERFEIT}. Lighting alone would "
        f"convict. Lower DAMP_CEILING or raise DIM_COUNTERFEIT.")


def test_a_rumpled_garment_cannot_convict_on_its_own():
    """Creasing is a property of the photograph, not of the garment."""
    worst = _pc(scoring.GROUP_FLOOR_FACTOR["geometry"]) * RUMPLED_GARMENT_BASELINE
    assert worst < scoring.DIM_COUNTERFEIT, (
        f"a rumpled genuine garment rolls up to {worst:.0f}, at or above the "
        f"conviction floor of {scoring.DIM_COUNTERFEIT}. Creasing alone would "
        f"convict. Lower GROUP_FLOOR_FACTOR['geometry'] or raise DIM_COUNTERFEIT.")


def test_the_glare_case_end_to_end():
    """The invariant above, run through the real roll-up rather than asserted
    on the constants — one glare-affected primitive, everything else clean."""
    prims = [{"name": "sheen_at_angle", "deviation": scoring.DAMP_CEILING,
              "group": "method", "confidence": 0.8},
             {"name": "weave_type", "deviation": 2, "group": "geometry", "confidence": 0.8},
             {"name": "offset", "deviation": 1, "group": "placement", "confidence": 0.8}]
    score, state, ic, _worst, _mean = roll_up_primitives(prims)
    dims = [Dim("Material", score, state, 0.8, ic)] + [
        _measured(n, 2) for n in ("Logo", "Stitching", "Hardware", "Label")]
    assert decide(dims, category="jacket")["lane"] != "REJECTED"


def test_the_creased_garment_case_end_to_end():
    prims = [{"name": "baseline_deviation", "deviation": RUMPLED_GARMENT_BASELINE,
              "group": "geometry", "confidence": 0.8},
             {"name": "stitch_method", "deviation": 3, "group": "method", "confidence": 0.8},
             {"name": "offset", "deviation": 2, "group": "placement", "confidence": 0.8}]
    score, state, ic, _worst, _mean = roll_up_primitives(prims)
    dims = [Dim("Stitching", score, state, 0.8, ic)] + [
        _measured(n, 2) for n in ("Logo", "Hardware", "Label", "Material")]
    assert decide(dims, category="jacket")["lane"] != "REJECTED"


# ---------------------------------------------------------------------------
# The ladder still has to be a ladder
# ---------------------------------------------------------------------------
def test_the_bands_are_ordered():
    assert scoring.BAND_AUTHENTIC < scoring.BAND_LIKELY_AUTH < scoring.BAND_COUNTERFEIT


def test_the_coverage_gates_are_ordered():
    """Clearing an item must never be easier than merely concluding about it,
    and certifying it must never be easier than clearing it."""
    assert (scoring.COVERAGE_FOR_CONCLUSION
            <= scoring.COVERAGE_FOR_LIKELY_AUTH
            <= scoring.COVERAGE_FOR_AUTHENTIC)


def test_one_dimension_convicts_at_the_band_and_not_below_it():
    """Rung 4b, asserted at the boundary from both sides. This is the rule the
    whole system rests on: one real defect is never averaged away."""
    for name in scoring.DIMENSION_NAMES:
        clean = [_measured(n, 0) for n in scoring.DIMENSION_NAMES if n != name]
        at = decide([_measured(name, scoring.DIM_COUNTERFEIT)] + clean, category="jacket")
        below = decide([_measured(name, scoring.DIM_COUNTERFEIT - 1)] + clean, category="jacket")
        assert at["lane"] == "REJECTED", f"{name} at the band did not convict"
        assert at["driver"] == name
        assert below["lane"] != "REJECTED", f"{name} convicted below the band"


def test_a_single_defect_survives_four_perfect_dimensions():
    """The dilution bug, as a test. A measured Hardware of 68 next to four
    dimensions at exactly zero must still reject — this is the shape that
    cleared case 2026VFC0031358 as Likely Authentic."""
    dims = [_measured("Hardware", 68)] + [
        _measured(n, 0) for n in ("Logo", "Stitching", "Label", "Material")]
    out = decide(dims, category="jacket")
    assert out["lane"] == "REJECTED"
    assert out["driver"] == "Hardware"


def test_an_estimate_can_never_convict_however_bad():
    """A filled cell is not an observation. If this ever flips, every
    unphotographable garment becomes a rejection."""
    dims = [Dim("Logo", 100, DimState.ESTIMATED, 0.9, 0.0)] + [
        _measured(n, 2) for n in ("Stitching", "Hardware", "Label", "Material")]
    assert decide(dims, category="jacket")["lane"] != "REJECTED"


# ---------------------------------------------------------------------------
# Clearance has to remain possible, and has to remain earned
# ---------------------------------------------------------------------------
def test_authentic_is_reachable():
    """A band that nothing can reach is not a band. BAND_AUTHENTIC was 0 for a
    while, which required a pixel-identical match on every primitive and
    produced zero Authentic verdicts across 249 runs."""
    dims = [_measured(n, scoring.BAND_AUTHENTIC) for n in scoring.DIMENSION_NAMES]
    assert decide(dims, category="jacket",
                  upc_status="match")["verdict_label"] == "Authentic"
    # Without the UPC lookup the same evidence still CLEARS — it is simply not
    # CERTIFIED. Judgements alone may release an item; they may not certify it.
    without = decide(dims, category="jacket")
    assert without["verdict_label"] == "Likely Authentic"
    assert without["lane"] == "CLEARED"


def test_authentic_requires_every_applicable_dimension_measured():
    """Coverage is a weighted total and can cross 90% with a whole dimension
    unexamined. Authentic asks the question directly instead."""
    dims = [_measured(n, 0) for n in ("Logo", "Stitching", "Hardware", "Label")]
    dims.append(Dim("Material", None, DimState.NOT_ASSESSABLE, 0.0, 0.0))
    out = decide(dims, category="jacket")
    assert out["verdict_label"] != "Authentic"
    assert out["lane"] != "REJECTED"          # clean evidence is not adverse evidence


def test_clearance_needs_more_than_one_forensic_dimension():
    """The gate that actually drove false clearances down on the labelled set."""
    label = _measured("Label", 0)
    one = decide([label, _measured("Logo", 0)], category="jacket")
    assert one["lane"] != "CLEARED"
    two = decide([label, _measured("Logo", 0), _measured("Material", 0),
                  _measured("Stitching", 0), _measured("Hardware", 0)], category="jacket")
    assert two["lane"] == "CLEARED"


# ---------------------------------------------------------------------------
# An engine failure is not a statement about the product
# ---------------------------------------------------------------------------
def test_every_dimension_failing_is_a_run_failure_not_insufficient_evidence():
    """Nine consecutive cases in the August batch were a quota outage and were
    reported as though the photographs were at fault. The two need opposite
    responses, so they must not share a verdict."""
    failed = [Dim(n, None, DimState.FAILED, 0.0, 0.0) for n in scoring.DIMENSION_NAMES]
    assert decide(failed, category="jacket")["verdict_label"] == "Run Failed"

    unseen = [Dim(n, None, DimState.NOT_ASSESSABLE, 0.0, 0.0) for n in scoring.DIMENSION_NAMES]
    assert decide(unseen, category="jacket")["verdict_label"] == "Insufficient Evidence"


def test_a_partial_engine_failure_is_still_a_verdict_about_the_product():
    """One agent dying must not turn a real finding into a re-run ticket."""
    dims = [_measured("Logo", 90)] + [
        Dim(n, None, DimState.FAILED, 0.0, 0.0)
        for n in ("Stitching", "Hardware", "Label", "Material")]
    assert decide(dims, category="jacket")["lane"] == "REJECTED"


# ---- the asymmetric ladder: conviction loosened only above the noise --------
def test_partial_dispositive_sits_above_what_photography_can_produce():
    """R4c convicts on a single PARTIAL. The entire safety argument is that its
    threshold clears the rumpled-garment ceiling with 2x margin — the same
    ceiling the geometry floor was sized against. If either constant moves,
    this is the test that says which failure comes back: creasing convicting
    genuine stock."""
    noise_ceiling = _pc(scoring.GROUP_FLOOR_FACTOR["geometry"]) * RUMPLED_GARMENT_BASELINE
    assert scoring.PARTIAL_DISPOSITIVE >= 2 * noise_ceiling
    # ...and corroboration (R4d) requires each partial to clear the band, which
    # itself sits above the ceiling.
    assert scoring.DIM_COUNTERFEIT > noise_ceiling
    assert scoring.PARTIALS_FOR_CORROBORATION >= 2


def test_a_rumpled_garment_survives_the_partial_rungs():
    """The scenario that created PARTIAL_MAY_CONVICT=False, run against the
    rungs that partially supersede it: every dimension PARTIAL at the noise
    ceiling — the worst a badly photographed genuine garment produces."""
    ceiling = _pc(scoring.GROUP_FLOOR_FACTOR["geometry"]) * RUMPLED_GARMENT_BASELINE
    dims = [Dim(n, ceiling, DimState.PARTIAL, 0.8, 1.0)
            for n in scoring.DIMENSION_NAMES]
    out = decide(dims, category="jacket")
    assert out["lane"] != "REJECTED"
    assert out["lane"] != "CLEARED"           # ...nor does creasing certify anything


def test_the_clearing_band_ends_below_the_noise_ceiling():
    """An item whose composite sits IN the photographic-noise range (26-30) is
    exactly as consistent with a creased genuine garment as with a counterfeit
    whose tells never resolved. It must go to a person — in either direction,
    deciding it automatically is a guess."""
    noise_ceiling = _pc(scoring.GROUP_FLOOR_FACTOR["geometry"]) * RUMPLED_GARMENT_BASELINE
    assert scoring.BAND_LIKELY_AUTH < noise_ceiling
    dims = [_measured(n, 27) for n in scoring.DIMENSION_NAMES]
    out = decide(dims, category="jacket", upc_status="match")
    assert out["lane"] == "REVIEW"


def test_upc_mismatch_convicts_with_no_coverage_at_all():
    """R3d. The barcode resolving to a DIFFERENT product is a lookup — the same
    class of evidence as the deterministic label rungs — and it must convict
    even when nothing else was assessable."""
    dims = [Dim(n, None, DimState.NOT_ASSESSABLE, 0.0, 0.0)
            for n in scoring.DIMENSION_NAMES]
    out = decide(dims, category="jacket", upc_status="mismatch")
    assert out["verdict_label"] == "Suspected Counterfeit"
    assert out["rule"] == "R3d"
    # nomatch is weaker evidence (master data has gaps) and stays a floor, not
    # an unconditional conviction.
    assert decide(dims, category="jacket", upc_status="nomatch")["rule"] != "R3d"


def test_corroborated_partials_convict_where_one_goes_to_review():
    """R4d. One partial in the band is a camera angle; two dimensions telling
    the same story is the garment."""
    base = [_measured("Label", 2), _measured("Logo", 2)]
    one = base + [Dim("Stitching", scoring.DIM_COUNTERFEIT + 5, DimState.PARTIAL, 0.8, 1.0),
                  Dim("Material", 5, DimState.PARTIAL, 0.8, 1.0),
                  Dim("Hardware", 2, DimState.MEASURED, 0.8, 1.0)]
    two = base + [Dim("Stitching", scoring.DIM_COUNTERFEIT + 5, DimState.PARTIAL, 0.8, 1.0),
                  Dim("Material", scoring.DIM_COUNTERFEIT + 2, DimState.PARTIAL, 0.8, 1.0),
                  Dim("Hardware", 2, DimState.MEASURED, 0.8, 1.0)]
    assert decide(one, category="jacket")["lane"] == "REVIEW"
    out = decide(two, category="jacket")
    assert out["verdict_label"] == "Suspected Counterfeit"
    assert out["rule"] == "R4d"


def test_certification_requires_a_lookup_not_judgements_alone():
    """Authentic is the one verdict that releases goods with a certification
    attached. Vision judgements alone may clear (Likely Authentic); the
    certificate additionally requires the UPC to have resolved to the right
    master record."""
    dims = [_measured(n, 0) for n in scoring.DIMENSION_NAMES]
    for status in ("not_provided", "unreadable", None):
        out = decide(dims, category="jacket", upc_status=status)
        assert out["verdict_label"] == "Likely Authentic", status
        assert "UPC" in out["reason"]
    assert decide(dims, category="jacket",
                  upc_status="match")["verdict_label"] == "Authentic"
