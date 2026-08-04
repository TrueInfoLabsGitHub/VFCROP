"""VERITAS scoring — Layer 2 aggregation, decision ladder, routing.

Layer 1 (primitives -> one dimension score) lives in providers.py, next to the
rubrics that produce the primitives. Everything from "five dimension results" to
"a verdict, a lane and a shot list" lives here.

Scale, internally, is DEVIATION: 0 = matches the verified-authentic reference,
100 = clearly different. The API and the workbook report the inverse
(AUTHENTICITY, 100 = matches) because that is the scale the product owner asked
for; `to_authenticity()` is the single conversion point and the dimension cells
are never converted at all.

The design principle, which every rule below serves: the system must clear an
item on the PRESENCE of evidence, never on the ABSENCE of findings. A
counterfeit escapes when it is called Likely Authentic — not when it is called
Insufficient Evidence and handed to a person.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# THE config block. Every threshold, weight and factor in the scoring spec is
# here and nowhere else.
#
# ALL OF THESE ARE UNFITTED DEFAULTS. Not one has been fitted against labelled
# outcome data — they are the spec's stated starting points, gathered in one
# place precisely so they CAN be fitted later without hunting through modules.
# Do not tune them by eye against individual cases.
# ---------------------------------------------------------------------------
SCORING_CONSTANTS = {
    # ---- Layer 1: primitives -> a dimension score -------------------------
    # Fixed group shares. These replace per-primitive 3x/1x weighting, under
    # which the method group's share drifted with how many primitives that
    # method happened to have (embroidery 55%, rubberised 50%, screen 44%) —
    # two items scored on different instruments, then compared as if identical.
    "GROUP_SHARES": {"method": 0.50, "geometry": 0.30, "placement": 0.20},
    # Tiered worst-primitive floor. A flat 0.85 overfires on geometry, where a
    # rumpled garment alone produces baseline_deviation ~90.
    "GROUP_FLOOR_FACTOR": {"method": 0.85, "geometry": 0.60, "placement": 0.60},
    "PRIMITIVE_MIN_CONFIDENCE": 0.50,
    # Exposure moves these more than authenticity does, so they may suggest
    # "suspicious" and never more. Mirrors the L1/L2 damping on the Label rubric.
    "DAMP_CEILING": 50,

    # ---- Layer 2: dimensions -> a composite -------------------------------
    "DIM_WEIGHTS": {"Label": 0.30, "Logo": 0.25, "Hardware": 0.20,
                    "Stitching": 0.15, "Material": 0.10},
    # How much ONE dimension is allowed to assert on its own, as a floor under
    # the weighted mean. A dispositive label tell must not be averaged away.
    "DIM_DIAGNOSTIC": {"Label": 0.90, "Logo": 0.85, "Hardware": 0.80,
                       "Stitching": 0.70, "Material": 0.60},
    # A dimension that lost its method group ran on the primitives a competent
    # counterfeiter gets right and a camera angle gets wrong. Weight and
    # ceiling both drop.
    "PARTIAL_WEIGHT_FACTOR": 0.40,
    "PARTIAL_DIAGNOSTIC_FACTOR": 0.50,
    "MIN_DIM_CONFIDENCE": 0.35,

    # ---- the ladder --------------------------------------------------------
    "DISPOSITIVE_THRESHOLD": 85,
    "DISPOSITIVE_CONFIDENCE": 0.60,
    "BAND_AUTHENTIC": 5,
    "BAND_LIKELY_AUTH": 30,
    "BAND_COUNTERFEIT": 61,
    "COVERAGE_FOR_COUNTERFEIT": 0.35,
    "COVERAGE_FOR_CONCLUSION": 0.50,
    "COVERAGE_FOR_LIKELY_AUTH": 0.60,
    "COVERAGE_FOR_AUTHENTIC": 0.75,
    # The anti-escape rule: coverage alone is gameable, so clearance also
    # requires the care tag to have actually been read.
    "LABEL_EVIDENCE_COVERAGE": 0.50,

    # ---- UPC ---------------------------------------------------------------
    "UPC_MISMATCH_FLOOR": 70,      # reads to a DIFFERENT product
    "UPC_NOMATCH_FLOOR": 60,       # reads, but is in no master record

    # ---- cross-engine ------------------------------------------------------
    "ENGINE_SPREAD_FOR_REVIEW": 25,
}

_C = SCORING_CONSTANTS
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
BAND_AUTHENTIC = _C["BAND_AUTHENTIC"]
BAND_LIKELY_AUTH = _C["BAND_LIKELY_AUTH"]
BAND_COUNTERFEIT = _C["BAND_COUNTERFEIT"]
COVERAGE_FOR_COUNTERFEIT = _C["COVERAGE_FOR_COUNTERFEIT"]
COVERAGE_FOR_CONCLUSION = _C["COVERAGE_FOR_CONCLUSION"]
COVERAGE_FOR_LIKELY_AUTH = _C["COVERAGE_FOR_LIKELY_AUTH"]
COVERAGE_FOR_AUTHENTIC = _C["COVERAGE_FOR_AUTHENTIC"]
LABEL_EVIDENCE_COVERAGE = _C["LABEL_EVIDENCE_COVERAGE"]
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
    "Reference Mismatch — Cannot Compare": "mismatch",
    "Run Failed": "error",
}

LANE_FOR_BAND = {
    "counterfeit": "REJECTED", "hard_fail": "REJECTED",
    "authentic": "CLEARED", "likely_authentic": "CLEARED",
    "caution": "REVIEW", "likely_counterfeit": "REVIEW",
    "insufficient": "REVIEW", "mismatch": "REVIEW", "error": "REVIEW",
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
              and float(p.get("confidence") or 0) >= PRIMITIVE_MIN_CONFIDENCE
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

    worst = max(usable, key=lambda p: GROUP_FLOOR_FACTOR[p["group"]] * float(p["deviation"]))
    floor = GROUP_FLOOR_FACTOR[worst["group"]] * float(worst["deviation"])
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

    __slots__ = ("name", "score", "state", "confidence", "internal_coverage", "finding")

    def __init__(self, name, score=None, state=DimState.ESTIMATED, confidence=0.0,
                 internal_coverage=0.0, finding=""):
        self.name = name
        self.score = None if score is None else float(score)
        self.state = state
        self.confidence = _f(confidence)
        self.internal_coverage = _f(internal_coverage)
        self.finding = finding or ""

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
                   rec.get("internal_coverage", 0.0), rec.get("finding", ""))

    @property
    def applicable(self):
        return self.state != DimState.NOT_APPLICABLE

    @property
    def contributes(self):
        return (self.state in CONTRIBUTING_STATES
                and self.score is not None
                and self.confidence >= MIN_DIM_CONFIDENCE
                and self.effective_weight > 0)

    @property
    def effective_weight(self):
        """Base weight, scaled by how much of the dimension was actually
        examined, and discounted again when the method group never ran."""
        w = DIM_WEIGHTS.get(self.name, 0.0) * self.internal_coverage
        if self.state == DimState.PARTIAL:
            w *= PARTIAL_WEIGHT_FACTOR
        return w

    @property
    def diagnostic(self):
        d = DIM_DIAGNOSTIC.get(self.name, 0.0)
        if self.state == DimState.PARTIAL:
            d *= PARTIAL_DIAGNOSTIC_FACTOR
        return d

    def as_dict(self):
        return {"dimension": self.name, "score": self.score, "state": self.state,
                "confidence": round(self.confidence, 2),
                "internal_coverage": round(self.internal_coverage, 2),
                "effective_weight": round(self.effective_weight, 4)}


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
    total = sum(DIM_WEIGHTS.get(d.name, 0.0) for d in applicable)
    if not total:
        return 0.0
    return sum(d.effective_weight for d in applicable if d.contributes) / total


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
    if label.internal_coverage < LABEL_EVIDENCE_COVERAGE:
        return False, (f"only {label.internal_coverage:.0%} of the label checks could be "
                       f"run (need {LABEL_EVIDENCE_COVERAGE:.0%})")
    others = [d for d in dims
              if d.name != "Label" and d.state == DimState.MEASURED and d.score is not None]
    if not others:
        return False, "no forensic dimension besides the label was measured"
    return True, ""


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
    """The ONE conversion point. Internally everything is deviation (0 matches);
    the reported verdict score is authenticity (100 matches). Dimension cells
    are deliberately NOT converted — they keep the deviation logic they have
    always had, so a high Logo number still means 'the logo is wrong'."""
    return None if deviation is None else int(max(0, min(100, 100 - deviation)))


def decide(dims, *, category="", upc_status="not_provided", label_hard_fail=False,
           hard_fail_reason="", pairing_ok=True, pairing_note="", run_ok=True,
           run_error="", label_readable=True, runtime_not_applicable=()):
    """The decision ladder. First match wins and the ORDER IS LOAD-BEARING:

    escalation runs BEFORE the coverage gate, because a confirmed dispositive
    defect needs no corroboration; clearance runs after it AND additionally
    requires positive evidence.

    Returns a dict shaped for the composite: deviation, score (authenticity),
    band, verdict_label, coverage, lane, driver, recapture, reason.
    """
    dims = list(dims)

    def out(verdict, reason, *, score=None, driver=None, cov=0.0, contributing=(),
            recapture=()):
        band = BAND_FOR_VERDICT[verdict]
        return {
            "deviation": score,
            "score": to_authenticity(score),
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
        return out("Run Failed", run_error or "the engine returned an error")

    # 2. Incomparable inputs — every dimension score would be a comparison
    #    against the wrong thing.
    if not pairing_ok:
        return out("Reference Mismatch — Cannot Compare",
                   pairing_note or "suspect and reference are different product categories")

    dims = mark_applicability(dims, category, runtime_not_applicable)
    dev, driver = composite_score(dims)
    dev = apply_upc(dev, upc_status)
    cov = coverage(dims)
    live = [d.name for d in dims if d.contributes]
    shots = recapture_list(dims)

    def emit(verdict, reason):
        return out(verdict, reason, score=dev, driver=driver, cov=cov,
                   contributing=live,
                   recapture=shots if verdict == "Insufficient Evidence" else ())

    # 3. A deterministic label check failed. No model judgement is involved, so
    #    it stands even when nothing else was assessable.
    if label_hard_fail:
        return emit("Counterfeit — Label Validation Failed",
                    hard_fail_reason or "a deterministic label check failed")

    # 4. One confirmed dispositive defect is enough. Only a MEASURED dimension
    #    may trigger this — a PARTIAL one is running on geometry, which
    #    photography moves as much as authenticity does.
    dispositive = [d for d in dims
                   if d.state == DimState.MEASURED and d.contributes
                   and d.score >= DISPOSITIVE_THRESHOLD
                   and d.confidence >= DISPOSITIVE_CONFIDENCE]
    if dispositive:
        d = max(dispositive, key=lambda x: x.score)
        res = emit("Suspected Counterfeit",
                   f"dispositive defect on {d.name} ({d.score:.0f}/100 deviation) at "
                   f"confidence {d.confidence:.2f}" +
                   ("" if label_readable else " — label unverified"))
        res["driver"] = d.name          # the defect drove it, whatever the mean said
        return res

    # 5/6. Significant deviation. Suspicion is never suppressed for want of
    #      coverage; thin coverage only downgrades it to a review item.
    if dev is not None and dev >= BAND_COUNTERFEIT:
        if cov >= COVERAGE_FOR_COUNTERFEIT:
            note = "" if label_readable else " (label unverified)"
            return emit("Suspected Counterfeit",
                        f"composite deviation {dev}/100 over {cov:.0%} of the item{note}")
        return emit("Inconclusive — Suspicious",
                    f"deviation is significant ({dev}/100) but only {cov:.0%} of the "
                    f"item was measured")

    # 7. The anti-escape rule, ahead of the coverage number.
    gate_ok, gate_why = evidence_gate(dims)
    if not gate_ok:
        return emit("Insufficient Evidence",
                    f"cannot clear this item — {gate_why}. Contributing: "
                    f"{', '.join(live) or 'none'}.")

    # 8. Enough was measured to be sure it is the same item, but not enough to
    #    say anything about it.
    if cov < COVERAGE_FOR_CONCLUSION:
        return emit("Insufficient Evidence",
                    f"effective coverage {cov:.0%} (need {COVERAGE_FOR_CONCLUSION:.0%}). "
                    f"Contributing: {', '.join(live) or 'none'}.")

    # 9/10. Clearance, on the presence of evidence.
    if dev is not None:
        if dev <= BAND_AUTHENTIC and cov >= COVERAGE_FOR_AUTHENTIC:
            return emit("Authentic",
                        f"no deviation on any measured dimension, {cov:.0%} coverage")
        if dev <= BAND_LIKELY_AUTH and cov >= COVERAGE_FOR_LIKELY_AUTH:
            return emit("Likely Authentic",
                        f"minor deviation only ({dev}/100) at {cov:.0%} coverage")

    # 11. Genuinely ambiguous.
    return emit("Inconclusive", "genuinely ambiguous — routed to human review")


# ---------------------------------------------------------------------------
# Stage 7 — cross-engine combination.
#
# Compare mode runs the whole graph once per engine. Averaging their scores
# would be the dilution bug again, one level up: a single engine that actually
# resolved the foundry code would be voted down by two that could not.
# ---------------------------------------------------------------------------
_ADVERSE = ("Suspected Counterfeit", "Counterfeit — Label Validation Failed")
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
