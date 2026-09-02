"""VERITAS scoring — Layer 2 aggregation, decision ladder, routing.

Layer 1 (primitives -> one dimension score) lives in providers.py, next to the
rubrics that produce the primitives. Everything from "five dimension results" to
"a verdict, a lane and a shot list" lives here.

Scale is DEVIATION, everywhere, with no conversion anywhere: 0 = matches the
verified-authentic reference, 100 = clearly different. Every primitive reports
it this way, the roll-up `max(weighted_mean, 85% x worst)` is only a floor
because higher means worse, the label critical floor is 85 and the counterfeit
band is the single constant DIM_COUNTERFEIT. The composite is reported on the
same scale as the dimensions that produced it.

The design principle, which every rule below serves: the system must clear an
item on the PRESENCE of evidence, never on the ABSENCE of findings. A
counterfeit escapes when it is called Likely Authentic — not when it is called
Insufficient Evidence and handed to a person.
"""
from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# THE config block. Every threshold, weight and factor in the scoring spec is
# here and nowhere else.
#
# ALL OF THESE ARE UNFITTED DEFAULTS. Not one has been fitted against labelled
# outcome data — they are the spec's stated starting points, gathered in one
# place precisely so they CAN be fitted later without hunting through modules.
# Do not tune them by eye against individual cases.
#
# EVERY VALUE HERE IS A WHOLE NUMBER 0-100 — the same scale as every score the
# system reports, so a reader never has to work out which of two scales a line
# is on. Weights, shares, floors and confidence thresholds are all percentages.
# The arithmetic below needs fractions; `_pc()` is the ONLY place that converts,
# so a value can be read straight off this block and quoted to a client.
# ---------------------------------------------------------------------------
SCORING_CONSTANTS = {
    # ---- Layer 1: primitives -> a dimension score -------------------------
    # Fixed group shares. These replace per-primitive 3x/1x weighting, under
    # which the method group's share drifted with how many primitives that
    # method happened to have (embroidery 55%, rubberised 50%, screen 44%) —
    # two items scored on different instruments, then compared as if identical.
    "GROUP_SHARES": {"method": 50, "geometry": 30, "placement": 20},
    # Tiered worst-primitive floor. A flat 85 overfires on geometry, where a
    # rumpled garment alone produces baseline_deviation ~90.
    #
    # geometry was 60. Against that ~90 baseline it produced a floor of 54 —
    # above every conviction band this system has ever run, so CREASING ALONE
    # CONVICTED. It is 30 so that the same rumpled garment lands at 27, under
    # DIM_COUNTERFEIT with margin. test_a_rumpled_garment_cannot_convict holds
    # this invariant; it is not a number to tune by eye.
    "GROUP_FLOOR_FACTOR": {"method": 85, "geometry": 30, "placement": 60},
    "PRIMITIVE_MIN_CONFIDENCE": 50,
    # Exposure moves these more than authenticity does, so they may suggest
    # "suspicious" and never more. Mirrors the L1/L2 damping on the Label rubric.
    #
    # Was 50. Through the method group's 85% floor that is 42 — above the
    # conviction band, so a CAP WHOSE ENTIRE PURPOSE WAS TO PREVENT CONVICTION
    # was convicting: glare on a shell fabric rejected genuine stock. At 25 the
    # worst a fully-damped primitive can assert is 21, under DIM_COUNTERFEIT.
    # test_no_damped_primitive_can_convict holds this.
    "DAMP_CEILING": 25,

    # ---- Layer 2: dimensions -> a composite -------------------------------
    "DIM_WEIGHTS": {"Label": 30, "Logo": 25, "Hardware": 20,
                    "Stitching": 15, "Material": 10},
    # How much ONE dimension is allowed to assert on its own, as a floor under
    # the weighted mean. A dispositive label tell must not be averaged away.
    "DIM_DIAGNOSTIC": {"Label": 90, "Logo": 85, "Hardware": 80,
                       "Stitching": 70, "Material": 60},
    # A dimension that lost its method group ran on the primitives a competent
    # counterfeiter gets right and a camera angle gets wrong. Weight and
    # ceiling both drop.
    "PARTIAL_WEIGHT_FACTOR": 40,
    "PARTIAL_DIAGNOSTIC_FACTOR": 50,
    "MIN_DIM_CONFIDENCE": 35,

    # ---- the ladder --------------------------------------------------------
    "DISPOSITIVE_THRESHOLD": 85,
    "DISPOSITIVE_CONFIDENCE": 60,
    # How many MEASURED forensic dimensions besides the Label an item must have
    # before it may be cleared. One was not enough: it let a single legible
    # logo, on a garment whose stitching, hardware and material were never
    # resolved, stand in for an examination. Measured on 249 labelled
    # counterfeits, moving this 1 -> 2 alongside COVERAGE_FOR_LIKELY_AUTH
    # 60 -> 75 cut false clearances from 11 to 2.
    "MIN_FORENSIC_DIMS_FOR_CLEARANCE": 2,
    # ANY ONE DIMENSION in the counterfeit band convicts the item, without
    # corroboration and without a coverage requirement. Held equal to
    # BAND_COUNTERFEIT so "the counterfeit band" means the same number whether
    # it is applied to one dimension or to the composite; kept as its own key so
    # the two can be fitted apart later.
    "DIM_COUNTERFEIT": 31,
    # Whether a PARTIAL dimension may convict on its own. False by design: a
    # partial ran on geometry and placement, and a rumpled garment alone
    # produces baseline_deviation around 90 (see GROUP_FLOOR_FACTOR). Turning
    # this on converts every badly-photographed genuine item into a rejection.
    "PARTIAL_MAY_CONVICT": False,
    # ...but distrust of partials is bounded by the size of the noise they can
    # carry, and that size is known: the rumpled-garment ceiling is
    # GROUP_FLOOR_FACTOR["geometry"] x baseline 90 = 27. Two escapes from that
    # blanket distrust, both sitting strictly ABOVE the noise ceiling:
    #
    # A single PARTIAL at 2x the noise ceiling. Geometry and placement wrong by
    # 55+ points is not a fold in the fabric — it is a different garment. The
    # coherence suite asserts this stays >= 2x the ceiling.
    "PARTIAL_DISPOSITIVE": 55,
    # Or corroboration: this many PARTIALs, each independently in the
    # counterfeit band. One elevated partial is a camera angle; the same story
    # told by two different dimensions of the same garment is not.
    "PARTIALS_FOR_CORROBORATION": 2,
    # The band ladder.
    #        0-3   authentic          (all applicable dimensions MEASURED)
    #       4-25   likely authentic
    #      26-30   inconclusive       (the photographic-noise range — a person looks)
    #       31+    suspected counterfeit
    #
    # HOW THESE WERE CHOSEN, because the last two rebands were not.
    #
    # Replayed over 249 runs of KNOWN COUNTERFEITS (backend/_replay_labelled.py),
    # the false-clearance rate — the only number that must reach zero — was:
    #
    #     band 61   4.4%      band 31   4.4%      band 11   2.8%
    #
    # Lowering the band bought essentially nothing, because measured deviations
    # on this corpus are BIMODAL: when a tell is resolved it scores 76-100, and
    # when it is not it scores 0-6. p25 is 0 and p90 is 91. Nothing real lives
    # between 11 and 31 — only photographic noise, which is why band 11
    # convicted genuine stock for creasing (54) and glare (42).
    #
    # What actually drove false clearances to zero was evidence, not the band:
    # COVERAGE_FOR_LIKELY_AUTH 60 -> 75, MIN_FORENSIC_DIMS_FOR_CLEARANCE 1 -> 2,
    # and AUTHENTIC_REQUIRES_ALL_MEASURED. The band is back at 31 because that
    # is where the signal is; the safety lives below, in the clearance gates.
    #
    # The two constants that used to sit above the conviction floor —
    # DAMP_CEILING and GROUP_FLOOR_FACTOR["geometry"] — are now under it by
    # construction, and two tests hold them there.
    "BAND_AUTHENTIC": 3,
    # 30 -> 25: the clearance band now ends BELOW the ~27 photographic-noise
    # ceiling. A composite of 26-30 is exactly the range where creasing and
    # glare live, and an item there used to clear; it now falls through to
    # Inconclusive and a person looks at it. Costs review volume on badly
    # photographed genuine stock, never a false rejection — which is the
    # direction this ladder is instructed to be wrong in.
    "BAND_LIKELY_AUTH": 25,
    "BAND_COUNTERFEIT": 31,
    # Authentic is a positive claim about a garment, made with no human in the
    # loop. It therefore requires that every dimension the product HAS was
    # actually measured — not merely that coverage crossed a percentage, which
    # partials and weightings can reach without any given dimension having been
    # examined. In practice this makes Authentic rare and Likely Authentic the
    # ordinary clearance, which is the intended shape.
    "AUTHENTIC_REQUIRES_ALL_MEASURED": True,
    # Authentic is a certification, and a certification should rest on at least
    # one piece of evidence that is a LOOKUP rather than a judgement. The UPC
    # is the only such input the intake collects, so full certification now
    # requires it to have been provided AND to resolve to the right master
    # record. Everything else about a clean item still clears — as Likely
    # Authentic, which is the ordinary clearance by design.
    "AUTHENTIC_REQUIRES_UPC_MATCH": True,
    # The same lookup requirement extended to the ORDINARY clearance. Off by
    # default: Likely Authentic is deliberately reachable on photographs alone.
    # Turn it on (LIKELY_AUTH_REQUIRES_UPC_MATCH=1 in the environment, or here)
    # when an escape audit shows counterfeits clearing R10 without a barcode —
    # it is the one clearance gate no photography trick can satisfy. An item
    # that would have cleared falls to Inconclusive (a person looks), never to
    # a rejection, so its measured cost is review volume, not false convictions.
    "LIKELY_AUTH_REQUIRES_UPC_MATCH": False,
    # 35 -> 20. The asymmetry this ladder wants, stated as a number: suspicion
    # needs less of the item than clearance does. A composite in the counterfeit
    # band is a POSITIVE finding — the coverage requirement only expresses how
    # much context that finding needs before it convicts rather than routes to
    # review, and on the labelled corpus every R6 (thin-coverage review) case
    # was a known counterfeit. Genuine items are protected by what the
    # composite is made of, not by this gate: photographic noise is capped at
    # ~27 per dimension (GROUP_FLOOR_FACTOR / DAMP_CEILING, tests hold it), so
    # a composite of 31+ requires a real signal regardless of coverage.
    "COVERAGE_FOR_COUNTERFEIT": 20,
    "COVERAGE_FOR_CONCLUSION": 50,
    # 60 -> 75. THIS is the false-clearance lever, not the band. See the note on
    # the band ladder above for the measured effect on the labelled corpus.
    "COVERAGE_FOR_LIKELY_AUTH": 75,
    "COVERAGE_FOR_AUTHENTIC": 90,
    # The anti-escape rule: coverage alone is gameable, so clearance also
    # requires the care tag to have actually been read.
    "LABEL_EVIDENCE_COVERAGE": 50,

    # ---- clearance is not cheaper than conviction ---------------------------
    #
    # The three constants below close an asymmetry that ran the wrong way for
    # the whole life of this ladder: CONVICTING an item required
    # DISPOSITIVE_CONFIDENCE (60) and a MEASURED state, while CLEARING one
    # accepted anything over MIN_DIM_CONFIDENCE (35) and counted PARTIALs
    # toward coverage. Certifying a garment genuine is the more dangerous of
    # the two claims — it is the only verdict that releases goods with no human
    # in the loop — so it must be the more expensive one to reach.
    #
    # Demonstrated before the change: five dimensions at confidence 0.35, all
    # scoring 0, returned "Authentic" at R9.
    "MIN_CONFIDENCE_FOR_CLEARANCE": 60,
    # A dimension that resolved 5% of its own checks is a MEASURED state and
    # nothing more. The Label half of the gate already demanded real internal
    # coverage (LABEL_EVIDENCE_COVERAGE); the "two other forensic dimensions"
    # half demanded only that the state string said `measured`.
    "MIN_INTERNAL_COVERAGE_FOR_CLEARANCE": 50,
    # The mirror of PARTIAL_MAY_CONVICT, and it must agree with it. A PARTIAL
    # dimension lost its method group and ran on geometry and placement — the
    # primitives a rumpled garment moves as much as a counterfeiter does. That
    # is the stated reason it may not convict; it is exactly as good a reason
    # that it may not clear. Before this, three clean dimensions plus a PARTIAL
    # screaming 60/100 returned "Likely Authentic" at 93% coverage.
    "PARTIAL_MAY_CLEAR": False,

    # ---- UPC ---------------------------------------------------------------
    "UPC_MISMATCH_FLOOR": 70,      # reads to a DIFFERENT product
    "UPC_NOMATCH_FLOOR": 60,       # reads, but is in no master record

    # ---- cross-engine ------------------------------------------------------
    "ENGINE_SPREAD_FOR_REVIEW": 25,
}

_C = SCORING_CONSTANTS


def _pc(v):
    """A config percentage as the fraction the arithmetic wants.

    Every threshold in SCORING_CONSTANTS is a whole number 0-100. Every value
    they are compared against — confidence, internal coverage, effective
    weight — is a 0-1 fraction, and those travel into stored records, so the
    conversion happens here rather than by rescaling what gets persisted."""
    return v / 100.0


GROUP_SHARES = _C["GROUP_SHARES"]
GROUP_FLOOR_FACTOR = _C["GROUP_FLOOR_FACTOR"]
PRIMITIVE_MIN_CONFIDENCE = _C["PRIMITIVE_MIN_CONFIDENCE"]
DAMP_CEILING = _C["DAMP_CEILING"]
DIM_WEIGHTS = _C["DIM_WEIGHTS"]
DIM_DIAGNOSTIC = _C["DIM_DIAGNOSTIC"]
PARTIAL_WEIGHT_FACTOR = _C["PARTIAL_WEIGHT_FACTOR"]
PARTIAL_DIAGNOSTIC_FACTOR = _C["PARTIAL_DIAGNOSTIC_FACTOR"]
MIN_DIM_CONFIDENCE = _C["MIN_DIM_CONFIDENCE"]
DISPOSITIVE_THRESHOLD = _C["DISPOSITIVE_THRESHOLD"]
DISPOSITIVE_CONFIDENCE = _C["DISPOSITIVE_CONFIDENCE"]
MIN_FORENSIC_DIMS_FOR_CLEARANCE = _C["MIN_FORENSIC_DIMS_FOR_CLEARANCE"]
AUTHENTIC_REQUIRES_ALL_MEASURED = _C["AUTHENTIC_REQUIRES_ALL_MEASURED"]
AUTHENTIC_REQUIRES_UPC_MATCH = _C["AUTHENTIC_REQUIRES_UPC_MATCH"]
# Environment override so the lever can be flipped on a deploy without an edit;
# the config value stays the shipped default.
LIKELY_AUTH_REQUIRES_UPC_MATCH = (
    os.environ.get("LIKELY_AUTH_REQUIRES_UPC_MATCH", "").strip().lower() in ("1", "true", "yes", "on")
    or _C["LIKELY_AUTH_REQUIRES_UPC_MATCH"])
DIM_COUNTERFEIT = _C["DIM_COUNTERFEIT"]
PARTIAL_MAY_CONVICT = _C["PARTIAL_MAY_CONVICT"]
PARTIAL_DISPOSITIVE = _C["PARTIAL_DISPOSITIVE"]
PARTIALS_FOR_CORROBORATION = _C["PARTIALS_FOR_CORROBORATION"]
BAND_AUTHENTIC = _C["BAND_AUTHENTIC"]
BAND_LIKELY_AUTH = _C["BAND_LIKELY_AUTH"]
BAND_COUNTERFEIT = _C["BAND_COUNTERFEIT"]
COVERAGE_FOR_COUNTERFEIT = _C["COVERAGE_FOR_COUNTERFEIT"]
COVERAGE_FOR_CONCLUSION = _C["COVERAGE_FOR_CONCLUSION"]
COVERAGE_FOR_LIKELY_AUTH = _C["COVERAGE_FOR_LIKELY_AUTH"]
COVERAGE_FOR_AUTHENTIC = _C["COVERAGE_FOR_AUTHENTIC"]
LABEL_EVIDENCE_COVERAGE = _C["LABEL_EVIDENCE_COVERAGE"]
MIN_CONFIDENCE_FOR_CLEARANCE = _C["MIN_CONFIDENCE_FOR_CLEARANCE"]
MIN_INTERNAL_COVERAGE_FOR_CLEARANCE = _C["MIN_INTERNAL_COVERAGE_FOR_CLEARANCE"]
PARTIAL_MAY_CLEAR = _C["PARTIAL_MAY_CLEAR"]
UPC_MISMATCH_FLOOR = _C["UPC_MISMATCH_FLOOR"]
UPC_NOMATCH_FLOOR = _C["UPC_NOMATCH_FLOOR"]
ENGINE_SPREAD_FOR_REVIEW = _C["ENGINE_SPREAD_FOR_REVIEW"]

DIMENSION_NAMES = ("Logo", "Stitching", "Hardware", "Label", "Material")


# ---------------------------------------------------------------------------
# States and verdicts
# ---------------------------------------------------------------------------
class DimState:
    MEASURED = "measured"              # class detected, method group scored
    PARTIAL = "partial"                # class undetermined — geometry/placement only
    ESTIMATED = "estimated"            # a filled cell, not a measurement
    NOT_ASSESSABLE = "not_assessable"
    NOT_APPLICABLE = "not_applicable"  # the product does not have this dimension
    FAILED = "failed"


CONTRIBUTING_STATES = (DimState.MEASURED, DimState.PARTIAL)

# Band keys, unchanged from the rest of the codebase so the colour maps in the
# exporter and the UI keep working. The ladder picks the band; nothing derives
# a band from a number any more.
BAND_FOR_VERDICT = {
    "Authentic": "authentic",
    "Likely Authentic": "likely_authentic",
    "Inconclusive": "caution",
    "Inconclusive — Suspicious": "likely_counterfeit",
    "Insufficient Evidence": "insufficient",
    "Suspected Counterfeit": "counterfeit",
    "Counterfeit — Label Validation Failed": "hard_fail",
    "Counterfeit — Specification Contradiction": "hard_fail",
    "Counterfeit — Impossible Product": "hard_fail",
    "Reference Mismatch — Cannot Compare": "mismatch",
    "Run Failed": "error",
}

LANE_FOR_BAND = {
    "counterfeit": "REJECTED", "hard_fail": "REJECTED",
    "authentic": "CLEARED", "likely_authentic": "CLEARED",
    "caution": "REVIEW", "likely_counterfeit": "REVIEW",
    "insufficient": "REVIEW", "mismatch": "REVIEW", "error": "REVIEW",
}

# Every rung of the ladder, by id. decide() stamps the id that fired onto the
# result, and the export prints it beside the verdict.
#
# This is the field whose absence made this system hard to argue with. A row
# reading "COUNTERFEIT" next to a green 12 is indefensible; a row reading
# "COUNTERFEIT · R4b" is a claim you can check against one sentence — and if
# the sentence is wrong, you know exactly which constant to move.
RULES = {
    "R1":   "the engine returned an error — not a verdict about the product",
    "R1b":  "every dimension agent errored — the engine never examined this item",
    "R2":   "suspect and reference are different products — nothing was comparable",
    "R3":   "a deterministic label check failed (no model judgement involved)",
    "R3b":  "the item's own printed markings contradict each other",
    "R3c":  "the item claims something that did not exist when it was made",
    "R4":   "one measured dimension carried a dispositive defect (>= "
            "DISPOSITIVE_THRESHOLD at high confidence)",
    "R4b":  "one measured dimension sat in the counterfeit band (>= DIM_COUNTERFEIT) — "
            "not averaged against the others",
    "R5":   "the composite reached the counterfeit band over enough of the item",
    "R6":   "deviation was significant but too little of the item was measured to convict",
    "R3d":  "the UPC resolves to a different product — a lookup, not a judgement",
    "R4c":  "one unresolved dimension deviates past PARTIAL_DISPOSITIVE — twice the "
            "photographic-noise ceiling",
    "R4d":  "multiple unresolved dimensions independently in the counterfeit band — "
            "corroboration substitutes for method resolution",
    "R7":   "cannot be cleared — the evidence gate was not satisfied",
    "R8":   "cannot be concluded — effective coverage below COVERAGE_FOR_CONCLUSION",
    "R8b":  "an unresolved (PARTIAL) dimension is in the counterfeit band — too "
            "unreliable to convict on, and therefore too unreliable to clear past",
    "R9":   "cleared and certified — every applicable dimension measured, all near zero",
    "R10":  "cleared with minor deviation at sufficient coverage",
    "R10a": "cleared, but not certified Authentic — a dimension was never measured",
    "R10b": "would have cleared, but clearance is configured to require a verified "
            "UPC (LIKELY_AUTH_REQUIRES_UPC_MATCH) — routed to review instead",
    "R11":  "genuinely ambiguous",
}

# Converting an unknown into a known beats any threshold change, so an
# Insufficient Evidence verdict names the exact photographs that would resolve it.
RECAPTURE_SHOTS = {
    "Label": ("Interior care tag, flat and in focus — fibre content, RN number "
              "and style number legible"),
    "Logo": ("Logo macro at <=15 cm under raking light, so the application "
             "method is resolvable"),
    "Stitching": ("Named seam macro (side seam or armhole) showing thread count "
                  "and construction class"),
    "Hardware": ("Zip slider face and reverse, or a snap/rivet, close enough to "
                 "read the foundry code"),
    "Material": "Fabric surface at <=10 cm, weave or knit structure resolvable",
}

# A dimension the product does not have. Excluded from BOTH the numerator and
# the denominator — scoring it 0 reads as "assessed and clean" and inflates
# coverage, which is how a T-shirt with no hardware got cleared.
CATEGORY_EXCLUSIONS = {
    "t-shirt": {"Hardware"}, "shirt": {"Hardware"}, "beanie": {"Hardware"},
    "hat": {"Hardware"}, "scarf": {"Hardware"}, "socks": {"Hardware"},
}

# Free OCR/vision text -> a category key. Longest match wins, so "swim shirt"
# does not silently become "shirt".
_CATEGORY_ALIASES = [
    ("t-shirt", "t-shirt"), ("tshirt", "t-shirt"), ("t shirt", "t-shirt"),
    ("tee shirt", "t-shirt"), ("tee", "t-shirt"),
    ("beanie", "beanie"), ("knit hat", "beanie"), ("hat", "hat"), ("cap", "hat"),
    ("scarf", "scarf"), ("sock", "socks"),
    ("polo", "shirt"), ("button-down", "shirt"), ("button down", "shirt"),
    ("shirt", "shirt"),
    # Categories that DO have hardware — listed so a match here stops a
    # substring like "shirt" inside "swim shirt" from excluding hardware.
    ("jacket", "jacket"), ("parka", "jacket"), ("hoodie", "hoodie"),
    ("pant", "pants"), ("short", "shorts"), ("backpack", "backpack"),
    ("bag", "backpack"), ("boot", "footwear"), ("shoe", "footwear"),
    ("swimsuit", "swimsuit"), ("swim", "swimsuit"), ("glove", "gloves"),
    ("vest", "vest"), ("fleece", "fleece"),
]


def normalise_category(*texts) -> str:
    """Best category key from whatever free text we have (product name, the
    pairing agent's item description, the OCR'd product family). Returns "" when
    nothing matches — an unknown category excludes nothing, which is the safe
    direction: it keeps Hardware in the denominator rather than out of it."""
    blob = " ".join(str(t or "") for t in texts).lower()
    best = ("", -1)
    for token, key in _CATEGORY_ALIASES:
        i = blob.find(token)
        if i >= 0 and len(token) > best[1]:
            best = (key, len(token))
    return best[0]


# ---------------------------------------------------------------------------
# Layer 1 roll-up — primitives to one dimension score.
#
# Called from providers.py once the rubric response has been parsed into
# (name, deviation, group, confidence) rows.
# ---------------------------------------------------------------------------
def roll_up_primitives(prims):
    """prims: iterable of dicts with name / deviation / group / confidence.

    Returns (score, state, internal_coverage, worst_name, weighted_mean) or
    (None, ...) when nothing was usable. Deviation scale throughout.

    The mean is normalised across GROUP SHARES, not across primitive counts, so
    an embroidery logo (6 method primitives) and a screen logo (4) are scored on
    the same instrument. The floor then lets one bad primitive assert itself,
    at a strength that depends on which group it came from.
    """
    usable = [p for p in prims
              if p.get("deviation") is not None
              and float(p.get("confidence") or 0) >= _pc(PRIMITIVE_MIN_CONFIDENCE)
              and p.get("group") in GROUP_SHARES]
    if not usable:
        return None, DimState.NOT_ASSESSABLE, 0.0, "", None

    present = {p["group"] for p in usable}
    live = {g: GROUP_SHARES[g] for g in present}
    total_share = sum(live.values())

    weighted_mean = 0.0
    for g, share in live.items():
        members = [p for p in usable if p["group"] == g]
        weighted_mean += (share / total_share) * (
            sum(float(p["deviation"]) for p in members) / len(members))

    worst = max(usable,
                key=lambda p: _pc(GROUP_FLOOR_FACTOR[p["group"]]) * float(p["deviation"]))
    floor = _pc(GROUP_FLOOR_FACTOR[worst["group"]]) * float(worst["deviation"])
    score = max(weighted_mean, floor)

    # How much of the dimension's applicable evidence actually scored.
    internal = total_share / sum(GROUP_SHARES.values())
    state = DimState.MEASURED if "method" in present else DimState.PARTIAL
    return (int(round(max(0.0, min(100.0, score)))), state, round(internal, 2),
            worst.get("name", ""), round(weighted_mean, 1))


# ---------------------------------------------------------------------------
# Layer 2
# ---------------------------------------------------------------------------
def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


class Dim:
    """One dimension's result, as Layer 2 sees it.

    score / state / internal_coverage always travel together. Reading a score
    without its state is what produced 'Likely Authentic' from four guesses.
    """

    __slots__ = ("name", "score", "state", "confidence", "internal_coverage", "finding",
                 "deterministic")

    def __init__(self, name, score=None, state=DimState.ESTIMATED, confidence=0.0,
                 internal_coverage=0.0, finding="", deterministic=False):
        self.name = name
        self.score = None if score is None else float(score)
        self.state = state
        self.confidence = _f(confidence)
        self.internal_coverage = _f(internal_coverage)
        self.finding = finding or ""
        # True when this dimension was MEASURED by a deterministic rule
        # rather than by comparison against the reference photographs. It
        # still scores and still contributes, but it cannot stand in for a
        # forensic examination in the evidence gate — see evidence_gate().
        self.deterministic = bool(deterministic)

    @classmethod
    def from_record(cls, name, rec):
        """Tolerant of stored runs written before this scoring existed: a record
        with no `state` is an ESTIMATE and a record with no `internal_coverage`
        contributes nothing. Both defaults are the conservative direction — an
        old run can never be promoted into a clearance by reloading it."""
        rec = rec or {}
        state = rec.get("state")
        if state not in (DimState.MEASURED, DimState.PARTIAL, DimState.ESTIMATED,
                         DimState.NOT_ASSESSABLE, DimState.NOT_APPLICABLE,
                         DimState.FAILED):
            # Legacy records carried `status` instead: scored / estimated /
            # abstain / error. Map what we can, default to ESTIMATED.
            state = {"scored": DimState.MEASURED, "estimated": DimState.ESTIMATED,
                     "abstain": DimState.NOT_ASSESSABLE,
                     "error": DimState.FAILED}.get(rec.get("status"), DimState.ESTIMATED)
        return cls(name, rec.get("score"), state, rec.get("confidence"),
                   rec.get("internal_coverage", 0.0), rec.get("finding", ""),
                   rec.get("deterministic", False))

    @property
    def applicable(self):
        return self.state != DimState.NOT_APPLICABLE

    @property
    def contributes(self):
        return (self.state in CONTRIBUTING_STATES
                and self.score is not None
                and self.confidence >= _pc(MIN_DIM_CONFIDENCE)
                and self.effective_weight > 0)

    @property
    def effective_weight(self):
        """Base weight, scaled by how much of the dimension was actually
        examined, and discounted again when the method group never ran."""
        w = _pc(DIM_WEIGHTS.get(self.name, 0)) * self.internal_coverage
        if self.state == DimState.PARTIAL:
            w *= _pc(PARTIAL_WEIGHT_FACTOR)
        return w

    @property
    def diagnostic(self):
        d = _pc(DIM_DIAGNOSTIC.get(self.name, 0))
        if self.state == DimState.PARTIAL:
            d *= _pc(PARTIAL_DIAGNOSTIC_FACTOR)
        return d

    def as_dict(self):
        return {"dimension": self.name, "score": self.score, "state": self.state,
                "confidence": round(self.confidence, 2),
                "internal_coverage": round(self.internal_coverage, 2),
                "effective_weight": round(self.effective_weight, 4),
                "deterministic": self.deterministic}


def mark_applicability(dims, category, runtime_not_applicable=()):
    """A dimension the product does not have is NOT_APPLICABLE.

    A runtime `applicable: false` from the vision agent wins over the category
    table — the agent looked at the photographs, the table did not. A dimension
    that was genuinely MEASURED stays measured either way: if the agent found
    and scored hardware, the item has hardware, whatever the category says."""
    excluded = set(CATEGORY_EXCLUSIONS.get((category or "").lower().strip(), set()))
    excluded |= {d for d in runtime_not_applicable if d}
    for d in dims:
        if d.name in excluded and d.state != DimState.MEASURED:
            d.state, d.score, d.internal_coverage = DimState.NOT_APPLICABLE, None, 0.0
    return dims


def composite_score(dims):
    """(score, driver). Estimates are displayed in the sheet but never enter
    the arithmetic — four guesses must not outvote one measurement."""
    live = [d for d in dims if d.contributes]
    if not live:
        return None, None
    den = sum(d.effective_weight for d in live)
    weighted_mean = sum(d.effective_weight * d.score for d in live) / den
    floor_dim = max(live, key=lambda d: d.diagnostic * d.score)
    floor = floor_dim.diagnostic * floor_dim.score
    if floor > weighted_mean:
        return int(round(floor)), floor_dim.name
    return int(round(weighted_mean)), None


def coverage(dims):
    """Share of the item's diagnostic weight that was actually measured."""
    applicable = [d for d in dims if d.applicable]
    total = sum(_pc(DIM_WEIGHTS.get(d.name, 0)) for d in applicable)
    if not total:
        return 0.0
    # Rounded before it is compared against a gate. The weights are percentages
    # turned into fractions, so a run that covers exactly 90% of the item sums
    # to 0.8999999999999999 and fails `>= 0.90` — the gate would be off by one
    # float ulp in the strict direction, silently, forever. Four places is far
    # finer than any threshold here and removes the whole class of problem.
    return round(sum(d.effective_weight for d in applicable if d.contributes) / total, 4)


def apply_upc(score, upc_status):
    """A barcode that resolves to a different product, or to nothing at all, is
    evidence — as a FLOOR, so it cannot be averaged away. 'not_provided' and
    'unreadable' are the absence of a check and move nothing."""
    if score is None:
        return None
    if upc_status == "mismatch":
        return max(score, UPC_MISMATCH_FLOOR)
    if upc_status == "nomatch":
        return max(score, UPC_NOMATCH_FLOOR)
    return score


def evidence_gate(dims):
    """The anti-escape rule. An item may be CLEARED only if its care tag was
    actually read and at least one other forensic dimension was measured.

    Coverage alone is gameable: 60% is reachable from Logo and Material without
    the label ever having been examined, and the label is where the tells live.
    Returns (ok, why_not)."""
    by = {d.name: d for d in dims}
    label = by.get("Label")
    if not label or label.state != DimState.MEASURED:
        return False, "the care/neck label was not measured"
    if label.internal_coverage < _pc(LABEL_EVIDENCE_COVERAGE):
        return False, (f"only {label.internal_coverage:.0%} of the label checks could be "
                       f"run (need {LABEL_EVIDENCE_COVERAGE}%)")
    # A tag the engine is 35% sure it read is not a tag that was read. This
    # floor is DISPOSITIVE_CONFIDENCE, deliberately: the confidence needed to
    # release an item is now the same as the confidence needed to reject one.
    if label.confidence < _pc(MIN_CONFIDENCE_FOR_CLEARANCE):
        return False, (f"the label was read at only {label.confidence:.0%} confidence "
                       f"(need {MIN_CONFIDENCE_FOR_CLEARANCE}% to clear)")
    # `not d.deterministic` is load-bearing. The deterministic rules read the
    # CARE TAG — the same tag that satisfies the label half of this gate. If a
    # text check could also satisfy this half, one legible tag would clear
    # both conditions and the gate would be asserting "the label, plus the
    # label". It exists to require a genuinely separate forensic examination.
    #
    # The three trailing conditions are the same quality bar the Label half has
    # always been held to. Without them `state == MEASURED` was the entire test,
    # so a dimension that resolved 5% of its own checks at 0.35 confidence
    # counted as a full forensic examination — and two of those, beside a
    # legible tag, cleared the item.
    others = [d for d in dims
              if d.name != "Label" and d.state == DimState.MEASURED
              and d.score is not None and not d.deterministic
              and d.confidence >= _pc(MIN_CONFIDENCE_FOR_CLEARANCE)
              and d.internal_coverage >= _pc(MIN_INTERNAL_COVERAGE_FOR_CLEARANCE)]
    need = MIN_FORENSIC_DIMS_FOR_CLEARANCE
    if len(others) < need:
        have = ", ".join(d.name for d in others) or "none"
        return False, (f"only {len(others)} forensic dimension(s) besides the label were "
                       f"measured to clearance standard (need {need}; "
                       f"≥{MIN_CONFIDENCE_FOR_CLEARANCE}% confidence and "
                       f"≥{MIN_INTERNAL_COVERAGE_FOR_CLEARANCE}% of the dimension's own "
                       f"checks). Qualifying: {have}")
    return True, ""


def all_applicable_measured(dims):
    """(ok, why_not). Every dimension the product HAS was actually measured.

    Coverage is a weighted percentage, so it can clear 90% with a whole
    dimension unexamined — a heavy Label plus a heavy Logo carry more than half
    the weight between them. Authentic is a positive claim, so it asks the
    question directly instead of inferring it from a total."""
    missing = [d.name for d in dims
               if d.applicable and d.state != DimState.MEASURED]
    if missing:
        return False, "not measured: " + ", ".join(missing)
    # ...and examined to the standard a positive certification requires, not
    # merely carrying the string `measured`. R9 is the only rung that certifies,
    # so it applies the clearance floors to EVERY dimension rather than to the
    # three the evidence gate names.
    thin = [d.name for d in dims
            if d.applicable
            and (d.confidence < _pc(MIN_CONFIDENCE_FOR_CLEARANCE)
                 or d.internal_coverage < _pc(MIN_INTERNAL_COVERAGE_FOR_CLEARANCE))]
    if thin:
        return False, ("measured below certification standard: " + ", ".join(thin))
    return True, ""


def clearance_coverage(dims):
    """Coverage counting only what may legitimately CLEAR an item.

    `coverage()` is the honest picture of how much was looked at, and the
    conviction rungs use it — suspicion must never be suppressed for want of
    evidence. Clearance is the opposite case: it is a claim, and a PARTIAL
    dimension cannot support one for the same reason PARTIAL_MAY_CONVICT is
    False. Anything under the clearance confidence floor drops out here too."""
    applicable = [d for d in dims if d.applicable]
    total = sum(_pc(DIM_WEIGHTS.get(d.name, 0)) for d in applicable)
    if not total:
        return 0.0
    ok = [d for d in applicable
          if d.contributes
          and (d.state == DimState.MEASURED or PARTIAL_MAY_CLEAR)
          and d.confidence >= _pc(MIN_CONFIDENCE_FOR_CLEARANCE)]
    return round(sum(d.effective_weight for d in ok) / total, 4)


def recapture_list(dims):
    """The specific photographs that would turn this run's unknowns into knowns."""
    out = []
    for d in dims:
        if d.applicable and d.state not in (DimState.MEASURED,):
            shot = RECAPTURE_SHOTS.get(d.name)
            if shot:
                out.append(f"{d.name}: {shot}")
    return out


def to_authenticity(deviation):
    """100 - deviation. Kept for callers that want the inverse for display, but
    NOT used to produce the reported score.

    The service reported the composite on this inverted scale for a while and it
    was a mistake. Every primitive returns deviation; the roll-up
    `max(weighted_mean, 85% x worst)` is only a floor if higher means worse;
    the label critical floor is 85 and the counterfeit band is DIM_COUNTERFEIT.
    Reporting
    the composite the other way up meant 85 read as 'confirmed critical tell' in
    the methodology and 'nearly genuine' in the workbook, on the same row.
    Making the whole system consistent the other way would mean flipping five
    dimension columns, both constants and the meaning of every stored number —
    a large, error-prone change to gain a display preference."""
    return None if deviation is None else int(max(0, min(100, 100 - deviation)))


def decide(dims, *, category="", upc_status="not_provided", label_hard_fail=False,
           hard_fail_reason="", pairing_ok=True, pairing_note="", run_ok=True,
           run_error="", label_readable=True, runtime_not_applicable=(),
           spec_hard_fail=False, spec_fail_reason="",
           provenance_hard_fail=False, provenance_fail_reason=""):
    """The decision ladder. First match wins and the ORDER IS LOAD-BEARING:

    escalation runs BEFORE the coverage gate, because a confirmed dispositive
    defect needs no corroboration; clearance runs after it AND additionally
    requires positive evidence.

    Rung 4b is the widest of the escalations: ANY single measured dimension in
    the counterfeit band convicts, without corroboration and without coverage.
    It sits above the composite rungs on purpose — the composite averages, and
    an average is how one real defect is diluted by four clean dimensions.

    Rung 1b separates an ENGINE failure from an EVIDENCE failure. Both used to
    surface as Insufficient Evidence, which made a quota outage indistinguishable
    from a bad photograph on the report.

    Returns a dict shaped for the composite: deviation, score (authenticity),
    band, verdict_label, coverage, lane, driver, recapture, reason.
    """
    dims = list(dims)

    def out(verdict, reason, *, rule="", score=None, driver=None, cov=0.0,
            contributing=(), recapture=()):
        band = BAND_FOR_VERDICT[verdict]
        return {
            # WHICH RUNG FIRED. The single most useful field on the whole
            # record, and the one that was missing: a verdict nobody can trace
            # to a rule is a verdict nobody can argue with or debug. Read
            # RULES[rule] for the sentence.
            "rule": rule,
            # ONE scale, end to end: 0 = matches the reference, 100 = clearly
            # different. `deviation` is an explicit alias of the same number so
            # a reader never has to work out which way up a column is.
            "deviation": score,
            "score": score,
            "band": band,
            "verdict_label": verdict,
            "lane": LANE_FOR_BAND[band],
            "coverage_pct": round(cov, 2),
            "driver": driver,
            "contributing": list(contributing),
            "recapture": list(recapture),
            "reason": reason,
            "dimension_states": [d.as_dict() for d in dims],
        }

    # 1. The run itself failed. Not a verdict about the product.
    if not run_ok:
        return out("Run Failed", run_error or "the engine returned an error", rule="R1")

    # 1b. Every dimension the product has came back FAILED. The engine did not
    #     look at this garment — a rate limit, an exhausted quota, an expired
    #     key — so this is not a statement about the product at all.
    #
    #     It must NOT share a verdict with Insufficient Evidence. They read the
    #     same on a sheet and need opposite responses: this one needs the run
    #     repeating, that one needs new photographs from the submitter. Nine
    #     consecutive cases in the August batch were this, reported as though
    #     the photographs were at fault.
    _applicable = [d for d in dims if d.applicable]
    if _applicable and all(d.state == DimState.FAILED for d in _applicable):
        return out("Run Failed",
                   run_error or ("every dimension agent errored — the engine did not "
                                 "examine this item; re-run required"), rule="R1b")

    # 2. Incomparable inputs — every dimension score would be a comparison
    #    against the wrong thing.
    if not pairing_ok:
        return out("Reference Mismatch — Cannot Compare",
                   pairing_note or "suspect and reference are different product categories",
                   rule="R2")

    dims = mark_applicability(dims, category, runtime_not_applicable)
    dev, driver = composite_score(dims)
    dev = apply_upc(dev, upc_status)
    cov = coverage(dims)
    live = [d.name for d in dims if d.contributes]
    shots = recapture_list(dims)

    def emit(verdict, reason, rule=""):
        return out(verdict, reason, rule=rule, score=dev, driver=driver, cov=cov,
                   contributing=live,
                   recapture=shots if verdict == "Insufficient Evidence" else ())

    # 3. A deterministic label check failed. No model judgement is involved, so
    #    it stands even when nothing else was assessable.
    if label_hard_fail:
        return emit("Counterfeit — Label Validation Failed",
                    hard_fail_reason or "a deterministic label check failed", "R3")

    # 3b. The item's own printed text contradicts itself, or contradicts the
    #     specification its own markings claim. Both strings were read; the
    #     contradiction is looked up, not judged. Sits here for the same
    #     reason rung 3 does — no model judgement is involved, so it stands
    #     even when nothing else was assessable.
    if spec_hard_fail:
        return emit("Counterfeit — Specification Contradiction",
                    spec_fail_reason or "the item's markings contradict each other", "R3b")

    # 3c. The item claims a product, technology or era that did not exist
    #     when it was made. A dated trademark is a fact about the world, not
    #     an observation about this garment.
    if provenance_hard_fail:
        return emit("Counterfeit — Impossible Product",
                    provenance_fail_reason
                    or "the item claims something that did not exist when it was made",
                    "R3c")

    # 3d. The barcode on the item resolves to a DIFFERENT product in the master
    #     record. This is a database lookup, not a model judgement — the same
    #     class of evidence as rungs 3-3c — so it convicts on its own, with no
    #     coverage requirement. (nomatch — a code absent from the record — stays
    #     a score floor via apply_upc rather than a conviction: master data has
    #     gaps, and an absence is weaker evidence than a contradiction.)
    if upc_status == "mismatch":
        return emit("Suspected Counterfeit",
                    "the item's UPC resolves to a different product in the master "
                    "record — the barcode contradicts the garment it is sewn to",
                    "R3d")

    # 4. One confirmed dispositive defect is enough. Only a MEASURED dimension
    #    may trigger this — a PARTIAL one is running on geometry, which
    #    photography moves as much as authenticity does.
    dispositive = [d for d in dims
                   if d.state == DimState.MEASURED and d.contributes
                   and d.score >= DISPOSITIVE_THRESHOLD
                   and d.confidence >= _pc(DISPOSITIVE_CONFIDENCE)]
    if dispositive:
        d = max(dispositive, key=lambda x: x.score)
        res = emit("Suspected Counterfeit",
                   f"dispositive defect on {d.name} ({d.score:.0f}/100 deviation) at "
                   f"confidence {d.confidence:.2f}" +
                   ("" if label_readable else " — label unverified"), "R4")
        res["driver"] = d.name          # the defect drove it, whatever the mean said
        return res

    # 4b. ANY ONE DIMENSION in the counterfeit band is enough, on its own.
    #
    #     This is the widest rule on the ladder and it is deliberate: a single
    #     forensic dimension in the counterfeit band convicts the item, with no
    #     corroboration from the other four and no coverage requirement. It
    #     supersedes what the composite would have said, because the composite
    #     AVERAGES — and averaging is how one real defect gets diluted by four
    #     dimensions that happened to look fine.
    #
    #     Two guards remain, and both are about what a score MEANS rather than
    #     how large it is:
    #       * the dimension must CONTRIBUTE — an estimate is a filled cell, not
    #         an observation, and a dimension under the confidence floor is an
    #         impression;
    #       * it must be MEASURED unless PARTIAL_MAY_CONVICT is set. A partial
    #         never resolved its method class, so it scored on geometry and
    #         placement — the primitives a rumpled garment moves as much as a
    #         counterfeiter does.
    adverse = [d for d in dims
               if d.contributes
               and d.score is not None
               and d.score >= DIM_COUNTERFEIT
               and (d.state == DimState.MEASURED or PARTIAL_MAY_CONVICT)]
    if adverse:
        d = max(adverse, key=lambda x: x.score)
        note = "" if label_readable else " — label unverified"
        res = emit("Suspected Counterfeit",
                   f"{d.name} is in the counterfeit band ({d.score:.0f}/100 deviation) "
                   f"at confidence {d.confidence:.2f}. One dimension in the band is "
                   f"enough — it is not averaged against the others{note}.", "R4b")
        res["driver"] = d.name          # the dimension that convicted, not the mean
        return res

    # 4c/4d. PARTIAL dimensions, above the noise they were distrusted for.
    #
    #     PARTIAL_MAY_CONVICT is False because a partial runs on geometry and
    #     placement, and photography moves those — but by a KNOWN amount: the
    #     rumpled-garment ceiling is ~27 (GROUP_FLOOR_FACTOR, held by the
    #     coherence tests). Distrust past that ceiling is not caution, it is
    #     throwing evidence away. Two ways a partial signal exceeds it:
    #
    #     R4c — one partial at PARTIAL_DISPOSITIVE (2x the ceiling). Geometry
    #     and placement off by 55+ points is not a fold in the fabric.
    p_adverse = [d for d in dims
                 if d.state == DimState.PARTIAL and d.contributes
                 and d.score is not None]
    strong = [d for d in p_adverse if d.score >= PARTIAL_DISPOSITIVE]
    if strong:
        d = max(strong, key=lambda x: x.score)
        res = emit("Suspected Counterfeit",
                   f"{d.name} deviates {d.score:.0f}/100 on geometry and placement "
                   f"alone — {PARTIAL_DISPOSITIVE}+ is twice what photography can "
                   f"produce on a genuine garment", "R4c")
        res["driver"] = d.name
        return res

    #     R4d — corroboration: several partials, each independently in the
    #     counterfeit band. One elevated partial is a camera angle; the same
    #     story from different dimensions of the same garment is the garment.
    corro = [d for d in p_adverse if d.score >= DIM_COUNTERFEIT]
    if len(corro) >= PARTIALS_FOR_CORROBORATION:
        worst = max(corro, key=lambda x: x.score)
        res = emit("Suspected Counterfeit",
                   f"{len(corro)} unresolved dimensions independently in the "
                   f"counterfeit band ({', '.join(f'{d.name} {d.score:.0f}' for d in corro)}) "
                   f"— corroboration across dimensions substitutes for method "
                   f"resolution", "R4d")
        res["driver"] = worst.name
        return res

    # 5/6. Significant deviation. Suspicion is never suppressed for want of
    #      coverage; thin coverage only downgrades it to a review item.
    if dev is not None and dev >= BAND_COUNTERFEIT:
        if cov >= _pc(COVERAGE_FOR_COUNTERFEIT):
            note = "" if label_readable else " (label unverified)"
            return emit("Suspected Counterfeit",
                        f"composite deviation {dev}/100 over {cov:.0%} of the item{note}",
                        "R5")
        return emit("Inconclusive — Suspicious",
                    f"deviation is significant ({dev}/100) but only {cov:.0%} of the "
                    f"item was measured", "R6")

    # 7. The anti-escape rule, ahead of the coverage number.
    gate_ok, gate_why = evidence_gate(dims)
    if not gate_ok:
        return emit("Insufficient Evidence",
                    f"cannot clear this item — {gate_why}. Contributing: "
                    f"{', '.join(live) or 'none'}.", "R7")

    # 8. Enough was measured to be sure it is the same item, but not enough to
    #    say anything about it.
    if cov < _pc(COVERAGE_FOR_CONCLUSION):
        return emit("Insufficient Evidence",
                    f"effective coverage {cov:.0%} (need {COVERAGE_FOR_CONCLUSION}%). "
                    f"Contributing: {', '.join(live) or 'none'}.", "R8")

    # 8b. AN UNRESOLVED DIMENSION IS IN THE ADVERSE RANGE.
    #
    #     PARTIAL_MAY_CONVICT is False, and rightly: a partial lost its method
    #     group and scored on geometry, which a rumpled garment moves as much as
    #     a counterfeiter does. But "not reliable enough to convict" was silently
    #     read as "safe to ignore", and the dimension then went on to be averaged
    #     into a clearance. Three clean dimensions beside a PARTIAL reporting
    #     60/100 returned Likely Authentic.
    #
    #     A reading the ladder distrusts in one direction it must distrust in
    #     both. It cannot convict, so it does not; it cannot clear either, so
    #     the item goes to a person.
    unresolved_adverse = [d for d in dims
                          if d.state == DimState.PARTIAL and d.contributes
                          and d.score is not None and d.score >= DIM_COUNTERFEIT]
    if unresolved_adverse:
        d = max(unresolved_adverse, key=lambda x: x.score)
        return emit("Inconclusive — Suspicious",
                    f"{d.name} reads {d.score:.0f}/100 but was never resolved past "
                    f"geometry, so it can neither convict nor clear — re-shoot "
                    f"{d.name.lower()} and re-run", "R8b")

    # 9/10. Clearance, on the presence of evidence.
    #
    # `ccov`, not `cov`. Rungs 5 and 6 above are allowed to convict on the full
    # picture, PARTIALs included, because suppressing suspicion for want of
    # evidence is the one failure this ladder must never have. Clearance is the
    # opposite claim and gets the stricter number: MEASURED dimensions, at
    # clearance confidence, only.
    ccov = clearance_coverage(dims)
    if dev is not None:
        if dev <= BAND_AUTHENTIC and ccov >= _pc(COVERAGE_FOR_AUTHENTIC):
            # Authentic is the only verdict that positively certifies a garment,
            # and it is issued with no human behind it. It therefore asks
            # directly whether every dimension the product HAS was examined,
            # rather than trusting a weighted coverage total to imply it.
            all_ok, all_why = (True, "")
            if AUTHENTIC_REQUIRES_ALL_MEASURED:
                all_ok, all_why = all_applicable_measured(dims)
            # A certification should rest on at least one lookup, not on
            # judgements alone. The UPC is the only lookup intake collects.
            if all_ok and AUTHENTIC_REQUIRES_UPC_MATCH and upc_status != "match":
                all_ok, all_why = False, ("the UPC was not verified against the "
                                          "master record (status: "
                                          f"{upc_status or 'not_provided'})")
            if all_ok:
                return emit("Authentic",
                            f"no deviation on any measured dimension, {ccov:.0%} clearance "
                            f"coverage, every applicable dimension measured", "R9")
            # Everything else about the item says clean; it simply was not
            # examined completely enough to certify. Fall through to Likely
            # Authentic rather than manufacturing a claim.
            if dev <= BAND_LIKELY_AUTH and ccov >= _pc(COVERAGE_FOR_LIKELY_AUTH):
                if LIKELY_AUTH_REQUIRES_UPC_MATCH and upc_status != "match":
                    return emit("Inconclusive",
                                f"clean at {ccov:.0%} coverage, but clearance is "
                                f"configured to require a verified UPC (status: "
                                f"{upc_status or 'not_provided'}) — routed to review",
                                "R10b")
                return emit("Likely Authentic",
                            f"minor deviation only ({dev}/100) at {ccov:.0%} clearance "
                            f"coverage — not certified Authentic because {all_why}", "R10a")
        if dev <= BAND_LIKELY_AUTH and ccov >= _pc(COVERAGE_FOR_LIKELY_AUTH):
            # The optional lookup gate on the ORDINARY clearance. When enabled,
            # an item that would have cleared on photographs alone goes to a
            # person instead — never to a rejection, so the cost is review
            # volume, which is the direction this ladder is instructed to be
            # wrong in.
            if LIKELY_AUTH_REQUIRES_UPC_MATCH and upc_status != "match":
                return emit("Inconclusive",
                            f"clean at {ccov:.0%} coverage, but clearance is "
                            f"configured to require a verified UPC (status: "
                            f"{upc_status or 'not_provided'}) — routed to review",
                            "R10b")
            return emit("Likely Authentic",
                        f"minor deviation only ({dev}/100) at {ccov:.0%} clearance "
                        f"coverage", "R10")

    # 11. Genuinely ambiguous.
    return emit("Inconclusive", "genuinely ambiguous — routed to human review", "R11")


# ---------------------------------------------------------------------------
# Stage 7 — cross-engine combination.
#
# Compare mode runs the whole graph once per engine. Averaging their scores
# would be the dilution bug again, one level up: a single engine that actually
# resolved the foundry code would be voted down by two that could not.
# ---------------------------------------------------------------------------
_ADVERSE = ("Suspected Counterfeit", "Counterfeit — Label Validation Failed",
            "Counterfeit — Specification Contradiction",
            "Counterfeit — Impossible Product")
_CLEARED = ("Authentic", "Likely Authentic")

# Band -> the canonical verdict text. Used to read a stored run written before
# the ladder existed, whose verdict text was 'Counterfeit' or 'Likely
# Counterfeit'. The band is the stable identifier; the wording is not.
_VERDICT_FOR_BAND = {v: k for k, v in BAND_FOR_VERDICT.items()}


def _verdict_of(comp):
    label = (comp.get("verdict_label") or "").strip()
    if label in BAND_FOR_VERDICT:
        return label
    return _VERDICT_FOR_BAND.get(comp.get("band") or "", label)


def combine_engines(results):
    """results: {engine_label: composite dict}. Returns the case-level verdict.

    A composite dict is what decide() returns (or an equivalently-shaped stored
    record). Missing keys are tolerated so old runs can be combined too."""
    live = {k: v for k, v in (results or {}).items() if isinstance(v, dict)}
    if not live:
        return {"verdict_label": "Run Failed", "band": "error", "lane": "REVIEW",
                "reason": "no engine produced a result", "engines": {}, "spread": None}

    labels = {k: _verdict_of(v) for k, v in live.items()}
    scores = [v.get("score") for v in live.values() if isinstance(v.get("score"), (int, float))]
    spread = (max(scores) - min(scores)) if len(scores) >= 2 else None

    def result(verdict, reason):
        band = BAND_FOR_VERDICT.get(verdict, "caution")
        # A wide disagreement always routes to a person, even when the verdict
        # itself reads as settled — the engines are not looking at the same thing.
        lane = LANE_FOR_BAND[band]
        if spread is not None and spread > ENGINE_SPREAD_FOR_REVIEW and lane == "CLEARED":
            lane = "REVIEW"
            reason += (f"; engines disagree by {spread:.0f} points — forced to human "
                       f"review")
        return {"verdict_label": verdict, "band": band, "lane": lane,
                "reason": reason, "engines": labels, "spread": spread}

    # Any engine that escalated, with a dispositive defect behind it, decides
    # the case. One engine resolving the tell is the whole point of running three.
    for k, v in live.items():
        if labels[k] in _ADVERSE:
            return result(labels[k],
                          f"{k} reported {labels[k]}: {v.get('reason') or 'no reason recorded'}")

    if all(labels[k] in _CLEARED for k in live):
        best = max(live.items(),
                   key=lambda kv: kv[1].get("coverage_pct") or 0)
        return result(labels[best[0]],
                      f"every engine cleared the item ({', '.join(sorted(set(labels.values())))})")

    if spread is not None and spread > ENGINE_SPREAD_FOR_REVIEW:
        return result("Inconclusive",
                      f"engines disagree by {spread:.0f} points ({', '.join(f'{k} {v}' for k, v in labels.items())})")

    if any(labels[k] == "Insufficient Evidence" for k in live):
        return result("Insufficient Evidence",
                      "at least one engine could not gather enough evidence")

    return result("Inconclusive",
                  f"engines did not agree on a clearance ({', '.join(f'{k} {v}' for k, v in labels.items())})")
