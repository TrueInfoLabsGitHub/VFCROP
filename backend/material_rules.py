"""Deterministic material validation — runs OUTSIDE the model.

The sibling of `label_rules.py`, over the same OCR'd fields. Fabric technology
names are DATED TRADEMARKS and membrane licences are EXCLUSIVE, so a care tag
that names two competing membranes, or names a technology that did not exist
when the garment was made, is stating a fact about itself that cannot be true.
That is not a similarity judgement and it does not belong in a vision rubric.

The same split as Label: the model's only job is OCR into structured fields;
everything here is plain Python over that text.

Three statuses, and the third is load-bearing:
  pass     — the check ran and the evidence is consistent
  fail     — the check ran and the evidence is inconsistent (a real tell)
  unknown  — the check could not run (field absent, no date code, tech name not
             mentioned). Never treated as either a pass or a fail.

Two hard-fail CLASSES, because they answer different questions and land on
different rungs:
  spec        — the item's own printed text contradicts itself
                -> "Counterfeit — Specification Contradiction"   (rung 3b)
  provenance  — the item claims something that did not exist yet
                -> "Counterfeit — Impossible Product"            (rung 3c)

NOT implemented here, deliberately:
  * Fibre arithmetic (the spec's M-D4) is ALREADY `label_rules.check_fiber_content`
    F1, which splits on language markers and component headers first. Its
    comments record two false-positive incidents that shaped it. Re-implementing
    that logic here would mean maintaining two copies of hard-won behaviour and
    would double-fail every tag that trips it.
  * Anything needing geometry, loft, sheen or hand-feel. Those have no external
    right answer, so they stay in the vision rubric and cap at Suspected.
"""
import re

import sku_registry
from label_rules import CRITICAL, STRONG, SUPPORTING, PASS, FAIL, UNKNOWN, _result

# Fail classes -> the ladder rung they arm.
SPEC = "spec"
PROVENANCE = "provenance"

_WEIGHT = {CRITICAL: 3, STRONG: 2, SUPPORTING: 1}


# ---------------------------------------------------------------------------
# Text handling
#
# Tech names are matched against a de-punctuated copy, because a care tag may
# print GORE-TEX, GORE TEX or GORETEX and OCR adds its own variance on top. The
# spaced copy is kept for the phrase checks, where word order is the evidence.
# ---------------------------------------------------------------------------
def _spaced(text):
    return re.sub(r"\s+", " ", (text or "").upper())


def _squashed(text):
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


# Membranes and insulations, by the string that identifies them once squashed.
_GORE = "GORETEX"
_TNF_MEMBRANES = {"DRYVENT": "DryVent", "HYVENT": "HyVent",
                  "FUTURELIGHT": "FUTURELIGHT"}
_SYNTHETIC_FILLS = {"THERMOBALL": "ThermoBall", "HEATSEEKER": "Heatseeker"}

# First use in commerce. A tech name printed on a garment made before its own
# launch is an impossible product — the strongest deterministic signal available
# on a material tag, because it needs no reference image and no registry.
_TECH_ERA = {
    "DRYVENT": (2016, 1, "DryVent"),
    "FUTURELIGHT": (2019, 10, "FUTURELIGHT"),
    "VENTRIX": (2017, 9, "Ventrix"),
    "THERMOBALL": (2013, 9, "ThermoBall"),
    "FLASHDRY": (2012, 1, "FlashDry"),
    "GORETEXINFINIUM": (2018, 9, "Gore-Tex Infinium"),
    "POLARTEC": (1991, 2, "Polartec"),
    "HYVENT": (1998, 1, "HyVent"),
}

# DryVent replaced HyVent, but not on a knife edge: the marks overlapped in
# retail through 2015-2016, the HyVent registration was renewed in 2017 and
# never abandoned, and TNF ran down old stock and regional SKUs for years after.
# So a late HyVent is a soft signal and an early DryVent is nothing at all.
_MEMBRANE_TRANSITION = (2015, 2016)
_HYVENT_LATE_FROM = 2017

# Budget lines TNF builds on DryVent. A GORE-TEX claim on one of these is a
# line mismatch — but only when we actually know the family, which is why the
# check returns unknown rather than passing when product_family is empty.
_DRYVENT_ONLY_FAMILIES = ("VENTURE", "RESOLVE", "ANTORA")

# RDS did not exist before Textile Exchange took it over on 2014-01-22, and
# TNF's first RDS product shipped Fall 2015.
_RDS_STANDARD_FROM = 2014
_RDS_TNF_FROM = 2015

_PCT_OF = r"(\d{1,3}(?:\.\d+)?)\s*%\s*(?:OF\s+)?"
_FILL_POWER = re.compile(r"\b(\d{3})\s*(?:FP\b|FILL\s*POWER)", re.I)
_FILL_POWER_ALT = re.compile(r"FILL\s*POWER[:\s]*(\d{3})", re.I)
# ThermoBall's marketing makes a COMPARABILITY claim to 600 fill — "comparable
# to 600 fill power down". That is not a fill-power declaration and must not be
# read as one, or every genuine ThermoBall jacket hard-fails on M-D7.
_COMPARABILITY = re.compile(
    r"(COMPARABLE|EQUIVALENT|SIMILAR|LIKE|MATCHES)\s+(?:\w+\s+){0,3}\d{3}\s*"
    r"(?:FP|FILL)", re.I)


def _text_of(fields):
    """Every OCR'd string that can carry a material claim, as one blob.

    care_text and fiber_content are the two the label OCR already returns
    verbatim, under an instruction not to correct anything — which is exactly
    what a deterministic text rule needs."""
    fields = fields if isinstance(fields, dict) else {}
    parts = [fields.get("care_text"), fields.get("fiber_content"),
             fields.get("product_family"), fields.get("material_text")]
    return " ".join(str(p) for p in parts if p)


def _year_of(fields, year=None):
    """Best manufacture year, or None. Never guessed.

    Phase 1 has no date-code field in the OCR schema, so the era checks below
    return UNKNOWN rather than passing. Adding `date_code` to _LABEL_ID_SCHEMA
    turns six of them on with no change here."""
    if year:
        try:
            return int(year)
        except (TypeError, ValueError):
            return None
    fields = fields if isinstance(fields, dict) else {}
    raw = str(fields.get("date_code") or "").strip()
    m = re.search(r"(19|20)\d{2}", raw)
    return int(m.group(0)) if m else None


# ---------------------------------------------------------------------------
# M-D2 — membrane exclusivity
# ---------------------------------------------------------------------------
def check_membrane_exclusivity(fields):
    """GORE-TEX co-labelled with a TNF in-house membrane.

    Gore licenses its mark on the condition that it is not mingled with a
    competitor's on the same product. DryVent, HyVent and FUTURELIGHT are TNF's
    own alternatives to Gore-Tex — a garment is built on one or the other, never
    both, and no genuine care tag names two."""
    squashed = _squashed(_text_of(fields))
    if not squashed:
        return [_result("M-D2", "Membrane exclusivity", UNKNOWN, CRITICAL,
                        "No care-tag or fibre text was extracted.")]
    if _GORE not in squashed:
        return [_result("M-D2", "Membrane exclusivity", UNKNOWN, CRITICAL,
                        "No GORE-TEX marking found — the check does not apply.")]
    clashes = [name for key, name in _TNF_MEMBRANES.items() if key in squashed]
    if clashes:
        return [_result("M-D2", "Membrane exclusivity", FAIL, CRITICAL,
                        f"GORE-TEX is printed alongside {' and '.join(clashes)} on the "
                        f"same item. These are competing membranes and Gore's licence "
                        f"forbids co-marking; no genuine garment carries both.")]
    return [_result("M-D2", "Membrane exclusivity", PASS, CRITICAL,
                    "GORE-TEX is named with no competing membrane alongside it.")]


# ---------------------------------------------------------------------------
# M-D3 — Gore-Tex trademark usage
# ---------------------------------------------------------------------------
def check_goretex_tm_usage(fields):
    """Gore's licence mandates adjective usage and a ®/™ at least once.

    Split by severity ON PURPOSE, against the spec, which grades the whole check
    critical. OCR routinely drops ® and ™ — they are small, they sit at glyph
    edges, and every transcription engine loses them sometimes. Hard-failing a
    genuine tag because a superscript did not survive OCR is precisely the
    "deterministic check that fires wrongly" failure `label_rules` already
    records being burned by, and it carries no model uncertainty for anything
    downstream to moderate.

    Noun and verb misuse is different: "made of Gore-Tex", "Gore-Texed" is word
    ORDER, which OCR reproduces reliably. That stays CRITICAL."""
    text = _text_of(fields)
    spaced, squashed = _spaced(text), _squashed(text)
    if not squashed:
        return [_result("M-D3", "Gore-Tex trademark usage", UNKNOWN, CRITICAL,
                        "No care-tag or fibre text was extracted.")]
    if _GORE not in squashed:
        return [_result("M-D3", "Gore-Tex trademark usage", UNKNOWN, CRITICAL,
                        "No GORE-TEX marking found — the check does not apply.")]

    out = []
    misuse = (re.search(r"MADE\s+(?:OF|FROM|WITH)\s+GORE[\s-]*TEX", spaced)
              or re.search(r"GORE[\s-]*TEXED", spaced)
              or re.search(_PCT_OF + r"GORE[\s-]*TEX", spaced))
    if misuse:
        out.append(_result(
            "M-D3", "Gore-Tex used as a noun or verb", FAIL, CRITICAL,
            f"GORE-TEX is used as a noun/verb ('{misuse.group(0).strip()}'). The "
            f"licence requires it as an adjective before a generic noun, e.g. "
            f"'GORE-TEX product'."))
    else:
        out.append(_result("M-D3", "Gore-Tex used as a noun or verb", PASS, CRITICAL,
                           "GORE-TEX is used adjectivally."))

    # OCR renders the glyphs as text at least as often as it reproduces them:
    # "(R)", "(TM)", a bare "R" in parentheses. Treating those as ABSENT would
    # fire this check on genuine tags whose transcription simply spelled the
    # symbol out — the same class of false positive the STRONG grade exists to
    # soften, so accept every rendering rather than rely on the grade.
    _MARK_FORMS = ("®", "™", "(R)", "(TM)", "(C)", "Ⓡ", "℗")
    if not any(m in text.upper() for m in _MARK_FORMS):
        out.append(_result(
            "M-D3b", "Gore-Tex registered mark present", FAIL, STRONG,
            "GORE-TEX appears with no ® or ™ anywhere in the transcribed text. "
            "Graded STRONG rather than critical: OCR frequently drops these "
            "glyphs, so absence is suggestive and not conclusive."))
    else:
        out.append(_result("M-D3b", "Gore-Tex registered mark present", PASS, STRONG,
                           "A registered/trademark symbol is present."))
    return out


# ---------------------------------------------------------------------------
# M-D5 — FTC down labelling
# ---------------------------------------------------------------------------
def check_down_ftc_compliance(fields):
    """FTC rules on the word 'down' (Down — but not out).

    Unqualified "down" requires more than 70% down by weight. "100% down" or
    "pure down" is legal only when the fill is exclusively down. These are
    federal labelling rules, so a violation is a fact about the tag rather than
    an opinion about the garment."""
    spaced = _spaced(_text_of(fields))
    if not spaced:
        return [_result("M-D5", "FTC down labelling", UNKNOWN, CRITICAL,
                        "No care-tag or fibre text was extracted.")]
    if not re.search(r"\bDOWN\b", spaced):
        return [_result("M-D5", "FTC down labelling", UNKNOWN, CRITICAL,
                        "No down claim found — the check does not apply.")]

    down = [float(m) for m in re.findall(_PCT_OF + r"DOWN\b", spaced)]
    feather = [float(m) for m in re.findall(_PCT_OF + r"(?:FEATHER|FEATHERS)\b", spaced)]
    absolute = re.search(r"\b(100\s*%\s*DOWN|PURE\s+DOWN)\b", spaced)

    if absolute and feather and max(feather) > 0:
        return [_result("M-D5", "FTC down labelling", FAIL, CRITICAL,
                        f"The tag claims '{absolute.group(0).strip()}' while also "
                        f"declaring {max(feather):g}% feather. An absolute down claim is "
                        f"permitted only when the fill is exclusively down.")]
    if down and max(down) <= 70 and not feather:
        return [_result("M-D5", "FTC down labelling", FAIL, CRITICAL,
                        f"The fill is described as down at {max(down):g}%, below the 70% "
                        f"the FTC requires before an unqualified 'down' claim may be "
                        f"made, with no qualifying fibre declared.")]
    if not down:
        return [_result("M-D5", "FTC down labelling", UNKNOWN, CRITICAL,
                        "A down claim is present but carries no percentage to check.")]
    return [_result("M-D5", "FTC down labelling", PASS, CRITICAL,
                    f"Down declared at {max(down):g}%, consistent with FTC labelling.")]


# ---------------------------------------------------------------------------
# M-D7 — synthetic insulation cannot have a fill power
# ---------------------------------------------------------------------------
def check_synthetic_no_fillpower(fields):
    """ThermoBall and Heatseeker are synthetic. Fill power measures the loft of
    DOWN; a synthetic insulation does not have one, and TNF never prints one.

    The exclusion that keeps this honest: ThermoBall's own copy claims warmth
    *comparable to* 600 fill power. That is a comparison, not a declaration."""
    text = _text_of(fields)
    spaced, squashed = _spaced(text), _squashed(text)
    if not squashed:
        return [_result("M-D7", "Synthetic fill has no fill power", UNKNOWN, CRITICAL,
                        "No care-tag or fibre text was extracted.")]
    present = [name for key, name in _SYNTHETIC_FILLS.items() if key in squashed]
    if not present:
        return [_result("M-D7", "Synthetic fill has no fill power", UNKNOWN, CRITICAL,
                        "No synthetic-insulation marking found — the check does not apply.")]
    if _COMPARABILITY.search(spaced):
        return [_result("M-D7", "Synthetic fill has no fill power", PASS, CRITICAL,
                        f"{present[0]} states a comparability claim, not a fill-power "
                        f"declaration — the documented and legitimate wording.")]

    fp = _FILL_POWER.search(spaced) or _FILL_POWER_ALT.search(spaced)
    down = re.findall(_PCT_OF + r"DOWN\b", spaced)
    if fp:
        return [_result("M-D7", "Synthetic fill has no fill power", FAIL, CRITICAL,
                        f"{present[0]} is a synthetic insulation but the tag declares a "
                        f"fill power of {fp.group(1)}. Fill power measures down loft and "
                        f"does not apply to synthetics.")]
    if down:
        return [_result("M-D7", "Synthetic fill has no fill power", FAIL, CRITICAL,
                        f"{present[0]} is a synthetic insulation but the tag also declares "
                        f"{down[0]}% down.")]
    return [_result("M-D7", "Synthetic fill has no fill power", PASS, CRITICAL,
                    f"{present[0]} is declared with no fill power or down content.")]


# ---------------------------------------------------------------------------
# M-D10 — brand collision
# ---------------------------------------------------------------------------
def check_brand_collision(fields):
    """Polartec and TKA are alternatives, never co-branded.

    TKA's 100/200/300 numbering deliberately mirrors Polartec's, which is what
    makes the two easy to conflate and what makes a tag naming BOTH a tell.

    The GORE-TEX-on-a-DryVent-line half returns UNKNOWN when product_family is
    empty rather than passing: an asymmetric rule, per the spec. A family we
    cannot read asserts nothing."""
    squashed = _squashed(_text_of(fields))
    family = _squashed((fields if isinstance(fields, dict) else {}).get("product_family"))
    out = []

    if not squashed:
        return [_result("M-D10", "Insulation brand collision", UNKNOWN, CRITICAL,
                        "No care-tag or fibre text was extracted.")]

    has_polartec, has_tka = "POLARTEC" in squashed, "TKA" in squashed
    if has_polartec and has_tka:
        out.append(_result("M-D10", "Insulation brand collision", FAIL, CRITICAL,
                           "The tag names both Polartec and TKA. These are alternative "
                           "fleece programmes and are never co-branded on one garment."))
    elif has_polartec or has_tka:
        out.append(_result("M-D10", "Insulation brand collision", PASS, CRITICAL,
                           "Exactly one fleece programme is named."))
    else:
        out.append(_result("M-D10", "Insulation brand collision", UNKNOWN, CRITICAL,
                           "Neither Polartec nor TKA is named — the check does not apply."))

    if _GORE not in squashed:
        out.append(_result("M-D10b", "Membrane matches the product line", UNKNOWN, STRONG,
                           "No GORE-TEX marking found — the check does not apply."))
    elif not family:
        out.append(_result("M-D10b", "Membrane matches the product line", UNKNOWN, STRONG,
                           "GORE-TEX is named but no product family was read, so the "
                           "line cannot be checked."))
    else:
        hit = next((f for f in _DRYVENT_ONLY_FAMILIES if f in family), None)
        if hit:
            out.append(_result(
                "M-D10b", "Membrane matches the product line", FAIL, STRONG,
                f"The tag claims GORE-TEX on the {hit.title()} line, which TNF builds "
                f"on DryVent."))
        else:
            out.append(_result("M-D10b", "Membrane matches the product line", PASS, STRONG,
                               "The named membrane is consistent with the product line."))
    return out


# ---------------------------------------------------------------------------
# M-D1 — technology name predates its own launch
# ---------------------------------------------------------------------------
def check_tech_name_era(fields, year=None):
    """A tech name printed on a garment made before that technology launched.

    Returns UNKNOWN with no date, which is every run until `date_code` is added
    to the OCR schema. That is the correct default: absence of a date is not
    evidence of an impossible product."""
    squashed = _squashed(_text_of(fields))
    y = _year_of(fields, year)
    if not squashed:
        return [_result("M-D1", "Technology name era", UNKNOWN, CRITICAL,
                        "No care-tag or fibre text was extracted.")]
    named = [(k, v) for k, v in _TECH_ERA.items() if k in squashed]
    if not named:
        return [_result("M-D1", "Technology name era", UNKNOWN, CRITICAL,
                        "No dated technology name found — the check does not apply.")]
    if y is None:
        return [_result("M-D1", "Technology name era", UNKNOWN, CRITICAL,
                        f"{named[0][1][2]} is named but no manufacture date was read, so "
                        f"the era cannot be checked.")]

    impossible = [(v[2], v[0]) for _k, v in named if y < v[0]]
    if impossible:
        name, launched = impossible[0]
        return [_result("M-D1", "Technology name era", FAIL, CRITICAL,
                        f"The tag names {name}, which was first used in {launched}, on a "
                        f"garment dated {y}.")]

    # The soft half. A late HyVent is a signal, not a conviction: the mark was
    # renewed in 2017 and old stock shipped for years.
    if "HYVENT" in squashed and y >= _HYVENT_LATE_FROM and y > _MEMBRANE_TRANSITION[1]:
        return [_result("M-D1", "Technology name era", FAIL, SUPPORTING,
                        f"HyVent is named on a garment dated {y}, after DryVent replaced "
                        f"it. Suggestive only — the HyVent mark was renewed in 2017 and "
                        f"regional stock shipped later.")]
    return [_result("M-D1", "Technology name era", PASS, CRITICAL,
                    f"Every named technology existed by {y}.")]


# ---------------------------------------------------------------------------
# M-D8 — RDS certification era and number
# ---------------------------------------------------------------------------
def check_rds_era_and_number(fields, year=None):
    """Responsible Down Standard claims: the standard, and TNF's use of it, both
    have start dates, and real certificates carry a resolvable number."""
    text = _text_of(fields)
    spaced, squashed = _spaced(text), _squashed(text)
    if not squashed:
        return [_result("M-D8", "RDS certification", UNKNOWN, CRITICAL,
                        "No care-tag or fibre text was extracted.")]
    if not re.search(r"\bRDS\b|RESPONSIBLE\s+DOWN\s+STANDARD", spaced):
        return [_result("M-D8", "RDS certification", UNKNOWN, CRITICAL,
                        "No RDS claim found — the check does not apply.")]

    y = _year_of(fields, year)
    if y is not None and y < _RDS_STANDARD_FROM:
        return [_result("M-D8", "RDS certification", FAIL, CRITICAL,
                        f"An RDS claim appears on a garment dated {y}. The standard did "
                        f"not exist until {_RDS_STANDARD_FROM}.")]
    if y is not None and y < _RDS_TNF_FROM:
        return [_result("M-D8", "RDS certification", FAIL, STRONG,
                        f"An RDS claim appears on a garment dated {y}; TNF's first RDS "
                        f"product shipped in {_RDS_TNF_FROM}.")]

    cert = re.search(r"\b(?:CU|CERT(?:IFICATE)?(?:\s*(?:NO|#))?)\s*[:\s]?\s*(\d{5,8})\b",
                     spaced)
    if not cert:
        return [_result("M-D8", "RDS certification", UNKNOWN, CRITICAL,
                        "An RDS claim is present but no certificate number was read.")]
    return [_result("M-D8", "RDS certification", PASS, CRITICAL,
                    f"RDS claim carries a well-formed certificate number ({cert.group(1)}).")]


# ---------------------------------------------------------------------------
# M-D6 — fill power against the catalogue
# ---------------------------------------------------------------------------
# No TNF product is 900 fill power. This half needs no registry and no style
# code: it is a statement about the whole catalogue, so it works today.
_MAX_TNF_FILL_POWER = 850


def check_fill_power_catalogue(fields, year=None):
    """A declared fill power that no TNF product has, or that contradicts the
    registry row for this style.

    The registry half reports UNKNOWN until sku_registry.json is populated. That
    is the correct empty behaviour — a spec we do not have is not a spec we can
    contradict."""
    spaced = _spaced(_text_of(fields))
    if not spaced:
        return [_result("M-D6", "Fill power matches the catalogue", UNKNOWN, CRITICAL,
                        "No care-tag or fibre text was extracted.")]
    m = _FILL_POWER.search(spaced) or _FILL_POWER_ALT.search(spaced)
    if not m or _COMPARABILITY.search(spaced):
        return [_result("M-D6", "Fill power matches the catalogue", UNKNOWN, CRITICAL,
                        "No fill power is declared — the check does not apply.")]
    declared = int(m.group(1))

    if declared > _MAX_TNF_FILL_POWER:
        return [_result("M-D6", "Fill power matches the catalogue", FAIL, CRITICAL,
                        f"The tag declares {declared} fill power. No TNF product is rated "
                        f"above {_MAX_TNF_FILL_POWER}.")]

    style = (fields if isinstance(fields, dict) else {}).get("style_number") or ""
    expected = sku_registry.field(style, "fill_power", _year_of(fields, year))
    if expected is None:
        return [_result("M-D6", "Fill power matches the catalogue", UNKNOWN, CRITICAL,
                        f"{declared} fill power declared, but no registry row for this "
                        f"style — the catalogue comparison could not be run.")]
    if int(expected) != declared:
        return [_result("M-D6", "Fill power matches the catalogue", FAIL, CRITICAL,
                        f"The tag declares {declared} fill power; the catalogue lists "
                        f"{expected} for style {sku_registry.normalise_style(style)}.")]
    return [_result("M-D6", "Fill power matches the catalogue", PASS, CRITICAL,
                    f"{declared} fill power matches the catalogue.")]


# ---------------------------------------------------------------------------
# Roll-up
# ---------------------------------------------------------------------------


# Sub-critical fails -> the deviation they justify on the Material dimension.
# CRITICAL fails never reach this: they hard-fail the run at rung 3b/3c, above
# every coverage gate, so the only fails left to score are the ones that were
# deliberately graded too noisy to convict on their own.
_INJECT_SCORE = {CRITICAL: 85, STRONG: 70, SUPPORTING: 50}


def dimension_injection(result):
    """What a deterministic FAIL justifies on the Material dimension, or None.

    Only FAILS inject. A PASS returns None on purpose, and the asymmetry is
    the whole design:

      * a contradiction in the printed text is a finding ABOUT THE ITEM, so
        it scores and it counts as evidence gathered;
      * a clean care tag says nothing whatsoever about the FABRIC. Material
        is weave, structure and surface; reading a tag does not examine any
        of them. Letting a passing text check buy coverage on this dimension
        would hand a counterfeiter with a working printer a route to
        clearance, which is the exact escape the ladder exists to close.

    So: facts convict, and a clean tag still clears nothing.
    """
    fails = [c for c in (result or {}).get("checks", []) if c["status"] == FAIL]
    if not fails:
        return None
    score = max(_INJECT_SCORE.get(c["severity"], 0) for c in fails)
    worst = max(fails, key=lambda c: _INJECT_SCORE.get(c["severity"], 0))
    return {"score": score,
            # High, but stated rather than modelled: a deterministic result
            # carries no vision uncertainty. Above DISPOSITIVE_CONFIDENCE so a
            # critical injection could fire rung 4 if one ever reached here.
            "confidence": 0.85,
            "internal_coverage": result.get("internal_coverage", 0.0),
            "finding": f"Deterministic: {worst['evidence']}",
            "check_id": worst["id"]}


# ---------------------------------------------------------------------------
# Roll-up
# ---------------------------------------------------------------------------
# Which class of hard fail each check arms. A check absent from this map can
# never hard-fail whatever its severity — the default is "contributes, does not
# convict", which is the safe direction for anything added later.
_FAIL_CLASS = {
    "M-D2": SPEC, "M-D3": SPEC, "M-D5": SPEC, "M-D6": SPEC, "M-D7": SPEC,
    "M-D10": SPEC,
    "M-D1": PROVENANCE, "M-D8": PROVENANCE,
}


def validate(fields, year=None):
    """Run every deterministic material check over OCR'd label fields.

    Returns {checks, spec_hard_fail, provenance_hard_fail, counts,
    internal_coverage, summary}. A hard fail of either class is raised ONLY when
    a CRITICAL check in that class actually FAILED — never because something was
    UNKNOWN, and never from a STRONG or SUPPORTING fail.
    """
    # Coerce rather than trust: a malformed or legacy record can carry a
    # non-dict here, and aggregate_node calls this unguarded — an
    # AttributeError would take the whole run down with it.
    fields = fields if isinstance(fields, dict) else {}
    checks = []
    checks += check_membrane_exclusivity(fields)
    checks += check_goretex_tm_usage(fields)
    checks += check_down_ftc_compliance(fields)
    checks += check_synthetic_no_fillpower(fields)
    checks += check_brand_collision(fields)
    checks += check_fill_power_catalogue(fields, year)
    checks += check_tech_name_era(fields, year)
    checks += check_rds_era_and_number(fields, year)

    counts = {PASS: 0, FAIL: 0, UNKNOWN: 0}
    for c in checks:
        counts[c["status"]] += 1

    def _hard(cls):
        return [c for c in checks
                if c["status"] == FAIL and c["severity"] == CRITICAL
                and _FAIL_CLASS.get(c["id"].rstrip("b")) == cls]

    spec, prov = _hard(SPEC), _hard(PROVENANCE)

    # Share of the check weight that actually resolved. Same instrument as the
    # Label dimension's internal coverage, so the two are comparable and a
    # single resolved check never reads as a fully examined dimension.
    applicable = sum(_WEIGHT[c["severity"]] for c in checks)
    scored = sum(_WEIGHT[c["severity"]] for c in checks if c["status"] in (PASS, FAIL))
    internal = round(scored / applicable, 2) if applicable else 0.0

    fails = [c for c in checks if c["status"] == FAIL]
    # Spec first, because the ladder tries rung 3b before 3c — the summary must
    # name the verdict the run will actually carry.
    if spec:
        summary = f"Specification contradiction: {spec[0]['evidence']}"
    elif prov:
        summary = f"Impossible product: {prov[0]['evidence']}"
    elif fails:
        summary = f"{len(fails)} deterministic material check(s) failed: {fails[0]['evidence']}"
    elif counts[PASS]:
        summary = (f"{counts[PASS]} deterministic material check(s) passed, "
                   f"{counts[UNKNOWN]} could not be run.")
    else:
        summary = "No deterministic material check could be run — no fields were readable."

    return {"checks": checks,
            "spec_hard_fail": bool(spec), "provenance_hard_fail": bool(prov),
            "spec_reason": spec[0]["evidence"] if spec else "",
            "provenance_reason": prov[0]["evidence"] if prov else "",
            "counts": counts, "internal_coverage": internal,
            "failed": [c["id"] for c in fails], "summary": summary}
