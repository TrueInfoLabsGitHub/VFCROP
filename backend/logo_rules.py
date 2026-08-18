"""Deterministic logo/mark validation — runs OUTSIDE the model.

The third rules module, after `label_rules` and `material_rules`, and the same
split: the model transcribes marks verbatim into `mark_text`, and every judgement
below is plain Python over that text plus a date.

What makes these deterministic is that BRAND MARKS ARE DATED. A sub-brand, a
tagline or a collaboration has a first-use date in a trademark register or a
published launch, so a mark printed on a garment made before that date is a fact
about the world rather than an impression about this garment. And a mark that
never existed at all cannot be a matter of degree.

NOT implemented here, and not implementable from text:
  * L-D1 arc count, L-D2 lockup structure, L-D3 lockup era. All three need the
    Half Dome SEGMENTED out of the image — arc bands counted, text lines located,
    arcs resolved left-or-right of the wordmark. The pipeline has no detection
    stage, so these stay in the vision rubric and cap at Suspected until one
    exists. Writing them against OCR alone would produce confident nonsense.
  * L-D9/L-D10 decoration method and placement. Both are [DET-OWN]: they need the
    SKU registry populated before they can assert anything. See sku_registry.py.

Fail classes, as in material_rules:
  spec        — the marking contradicts the item's own style code   (rung 3b)
  provenance  — the mark did not exist when the item was made       (rung 3c)
"""
import re

from label_rules import CRITICAL, STRONG, SUPPORTING, PASS, FAIL, UNKNOWN, _result
from material_rules import SPEC, PROVENANCE, _squashed

_WEIGHT = {CRITICAL: 3, STRONG: 2, SUPPORTING: 1}


def _text_of(fields):
    """Every OCR'd string that can carry a brand mark."""
    fields = fields if isinstance(fields, dict) else {}
    parts = [fields.get("mark_text"), fields.get("product_family"),
             fields.get("care_text")]
    return " ".join(str(p) for p in parts if p)


# Season codes as they are printed on a TNF tag. FW19 -> 2019, SS18 -> 2018.
_SEASON = re.compile(r"\b(?:FW|SS|AW|SP)\s?(\d{2}|\d{4})\b")


def _year_of(fields, year=None):
    """Manufacture year from an explicit argument, a season code or a bare year.

    Never guessed. No date -> None -> every era check below reports UNKNOWN,
    which is the only safe default: not knowing when something was made is not
    evidence that it is impossible.
    """
    if year:
        try:
            return int(year)
        except (TypeError, ValueError):
            return None
    fields = fields if isinstance(fields, dict) else {}
    raw = str(fields.get("date_code") or "")
    m = _SEASON.search(raw.upper())
    if m:
        v = int(m.group(1))
        return v if v > 1900 else (2000 + v if v < 80 else 1900 + v)
    m = re.search(r"\b(19|20)\d{2}\b", raw)
    return int(m.group(0)) if m else None


# ---------------------------------------------------------------------------
# L-D4 / L-D5 — dated marks
# ---------------------------------------------------------------------------
# The tagline. USPTO Reg. 2897197, first use January 1997.
_TAGLINE = ("NEVERSTOPEXPLORING", 1997, "NEVER STOP EXPLORING")

# Sub-brands, by first use. A Summit Series mark on a 1998 garment is not a
# close call — the line did not exist.
_SUBBRANDS = {
    "STEEPTECH": (1991, "Steep Tech"),
    "SUMMITSERIES": (2000, "Summit Series"),
    "PURPLELABEL": (2003, "Purple Label"),
    "WHITELABEL": (2014, "White Label"),
    "URBANEXPLORATION": (2016, "Urban Exploration"),
    "BLACKSERIES": (2017, "Black Series"),
    "RMST": (2022, "RMST"),
}


def check_tagline_era(fields, year=None):
    """NEVER STOP EXPLORING on a garment predating the tagline."""
    squashed = _squashed(_text_of(fields))
    key, first, name = _TAGLINE
    if not squashed:
        return [_result("L-D4", "Tagline era", UNKNOWN, CRITICAL,
                        "No mark text was extracted.")]
    if key not in squashed:
        return [_result("L-D4", "Tagline era", UNKNOWN, CRITICAL,
                        f"'{name}' is not printed — the check does not apply.")]
    y = _year_of(fields, year)
    if y is None:
        return [_result("L-D4", "Tagline era", UNKNOWN, CRITICAL,
                        f"'{name}' is printed but no date was read.")]
    if y < first:
        return [_result("L-D4", "Tagline era", FAIL, CRITICAL,
                        f"'{name}' appears on a garment dated {y}. The tagline was "
                        f"first used in {first}.")]
    return [_result("L-D4", "Tagline era", PASS, CRITICAL,
                    f"'{name}' is consistent with a {y} garment.")]


def check_subbrand_era(fields, year=None):
    """A sub-brand mark predating its own launch."""
    squashed = _squashed(_text_of(fields))
    if not squashed:
        return [_result("L-D5", "Sub-brand era", UNKNOWN, CRITICAL,
                        "No mark text was extracted.")]
    found = [(first, name) for k, (first, name) in _SUBBRANDS.items() if k in squashed]
    if not found:
        return [_result("L-D5", "Sub-brand era", UNKNOWN, CRITICAL,
                        "No dated sub-brand mark is printed — the check does not apply.")]
    y = _year_of(fields, year)
    if y is None:
        return [_result("L-D5", "Sub-brand era", UNKNOWN, CRITICAL,
                        f"{found[0][1]} is printed but no date was read.")]
    impossible = [(f, n) for f, n in found if y < f]
    if impossible:
        first, name = impossible[0]
        return [_result("L-D5", "Sub-brand era", FAIL, CRITICAL,
                        f"The {name} mark appears on a garment dated {y}. That line "
                        f"was not introduced until {first}.")]
    return [_result("L-D5", "Sub-brand era", PASS, CRITICAL,
                    f"Every sub-brand mark existed by {y}.")]


# ---------------------------------------------------------------------------
# L-D6 — marks that do not exist at all
# ---------------------------------------------------------------------------
# Needs no date: these were never printed by TNF in any year.
#
# GRADING NOTE. '1966 SERIES' is graded STRONG, not critical, against the spec.
# The 1996 Retro Nuptse and 1996 Retro Denali are among the most common genuine
# products in this catalogue, and a single OCR digit transposition turns 1996
# into 1966. A critical grade would convert a transcription slip into a terminal
# counterfeit verdict on ordinary stock. The two-token phrase makes a collision
# unlikely; STRONG makes it survivable.
_NONEXISTENT = [
    ("CHAPTER3", STRONG, "a 'Chapter 3' collaboration marking",
     "the Gucci x TNF collaboration ran to Chapter 2 only"),
    ("SUMMITGOLD", CRITICAL, "a 'Summit Gold' tier", "no such tier exists"),
    ("SUMMITFOCUS", CRITICAL, "a 'Summit Focus' tier", "no such tier exists"),
    ("SUMMITPRO", CRITICAL, "a 'Summit Pro' tier",
     "the line is Summit Series; there is no Pro tier"),
    ("1966SERIES", STRONG, "a '1966 Series' lockup",
     "no such series exists — the retro lines are 1985, 1990, 1992, 1994 and 1996"),
]


def check_nonexistent_mark(fields):
    """A mark from the denylist. Needs no date and no registry."""
    squashed = _squashed(_text_of(fields))
    if not squashed:
        return [_result("L-D6", "Mark exists", UNKNOWN, CRITICAL,
                        "No mark text was extracted.")]
    # Gucci and 'Chapter 3' need not be adjacent: the mark reads
    # "GUCCI X THE NORTH FACE CHAPTER 3", so match them as a PAIR rather than as
    # one squashed token. Chapter 3 on its own stays STRONG below — some other
    # brand may legitimately number a capsule that way.
    if "GUCCI" in squashed and "CHAPTER3" in squashed:
        return [_result("L-D6", "Mark exists", FAIL, CRITICAL,
                        "The item is marked as a Gucci x The North Face 'Chapter 3' "
                        "— the collaboration ran to Chapter 2 only.")]
    for key, sev, what, why in _NONEXISTENT:
        if key in squashed:
            return [_result("L-D6", "Mark exists", FAIL, sev,
                            f"The item is marked with {what} — {why}.")]
    return [_result("L-D6", "Mark exists", PASS, CRITICAL,
                    "No marking from the non-existent-mark list is present.")]


# ---------------------------------------------------------------------------
# L-D7 — collaboration era
# ---------------------------------------------------------------------------
# (first year, last known year, display name). BEFORE the first year is
# unambiguous: the partnership did not exist, so the garment cannot. AFTER the
# last known year is only suggestive — collaborations get re-released, archived
# and re-dated, and a list of drops is never provably complete.
_COLLABS = {
    "SUPREME": (2007, 2023, "Supreme"),
    "GUCCI": (2021, 2022, "Gucci"),
    "KAWS": (2022, 2022, "KAWS"),
    "BRAINDEAD": (2019, 2020, "Brain Dead"),
}


def check_collab_era(fields, year=None):
    """A collaboration mark outside the window the partnership existed in."""
    squashed = _squashed(_text_of(fields))
    if not squashed:
        return [_result("L-D7", "Collaboration era", UNKNOWN, CRITICAL,
                        "No mark text was extracted.")]
    found = [(a, b, n) for k, (a, b, n) in _COLLABS.items() if k in squashed]
    if not found:
        return [_result("L-D7", "Collaboration era", UNKNOWN, CRITICAL,
                        "No collaboration mark is printed — the check does not apply.")]
    y = _year_of(fields, year)
    if y is None:
        return [_result("L-D7", "Collaboration era", UNKNOWN, CRITICAL,
                        f"A {found[0][2]} mark is printed but no date was read.")]
    early = [(a, n) for a, _b, n in found if y < a]
    if early:
        first, name = early[0]
        return [_result("L-D7", "Collaboration era", FAIL, CRITICAL,
                        f"A {name} x The North Face marking appears on a garment dated "
                        f"{y}. That collaboration did not begin until {first}.")]
    late = [(b, n) for _a, b, n in found if y > b]
    if late:
        last, name = late[0]
        return [_result("L-D7", "Collaboration era", FAIL, SUPPORTING,
                        f"A {name} marking appears on a garment dated {y}, after the "
                        f"last drop known to this table ({last}). Suggestive only — the "
                        f"drop list is not provably complete.")]
    return [_result("L-D7", "Collaboration era", PASS, CRITICAL,
                    f"The collaboration marking is consistent with a {y} garment.")]


# ---------------------------------------------------------------------------
# L-D8 — mark against the style-code prefix
# ---------------------------------------------------------------------------
def check_style_prefix_vs_mark(fields):
    """Purple Label is nanamica's line and carries NN style codes, never NF0A.

    A contradiction between the mark and the item's own style number — the item
    disagreeing with itself, which is a spec fail rather than a provenance one.
    """
    squashed = _squashed(_text_of(fields))
    style = _squashed((fields if isinstance(fields, dict) else {}).get("style_number"))
    if "PURPLELABEL" not in squashed:
        return [_result("L-D8", "Mark matches the style prefix", UNKNOWN, CRITICAL,
                        "No Purple Label marking — the check does not apply.")]
    if not style:
        return [_result("L-D8", "Mark matches the style prefix", UNKNOWN, CRITICAL,
                        "A Purple Label marking is present but no style number was read.")]
    if style.startswith("NN"):
        return [_result("L-D8", "Mark matches the style prefix", PASS, CRITICAL,
                        f"Purple Label marking with an NN style code ({style}).")]
    if style.startswith("NF0A"):
        return [_result("L-D8", "Mark matches the style prefix", FAIL, CRITICAL,
                        f"A Purple Label marking appears on style {style}. Purple Label "
                        f"is nanamica's line and carries NN codes, not NF0A.")]
    return [_result("L-D8", "Mark matches the style prefix", UNKNOWN, CRITICAL,
                    f"Style code {style} is in neither the NN nor the NF0A namespace.")]


# ---------------------------------------------------------------------------
# Roll-up
# ---------------------------------------------------------------------------
_FAIL_CLASS = {"L-D4": PROVENANCE, "L-D5": PROVENANCE, "L-D6": PROVENANCE,
               "L-D7": PROVENANCE, "L-D8": SPEC}

_INJECT_SCORE = {CRITICAL: 85, STRONG: 70, SUPPORTING: 50}


def dimension_injection(result):
    """What a deterministic FAIL justifies on the Logo dimension, or None.

    Only fails inject, for the same reason as material_rules: a mark that is
    correctly spelled and correctly dated is the easiest thing in the world for
    a counterfeiter to copy, so a PASS here says nothing about the ARTWORK and
    must never buy coverage on the dimension that judges it."""
    fails = [c for c in (result or {}).get("checks", []) if c["status"] == FAIL]
    if not fails:
        return None
    worst = max(fails, key=lambda c: _INJECT_SCORE.get(c["severity"], 0))
    return {"score": _INJECT_SCORE.get(worst["severity"], 0),
            "confidence": 0.85,
            "internal_coverage": result.get("internal_coverage", 0.0),
            "finding": f"Deterministic: {worst['evidence']}",
            "check_id": worst["id"]}


def validate(fields, year=None):
    """Run every deterministic logo/mark check over the OCR'd fields.

    Same contract as label_rules.validate and material_rules.validate. A hard
    fail of either class is raised ONLY when a CRITICAL check in that class
    actually FAILED — never from UNKNOWN, never from STRONG or SUPPORTING.
    """
    # Coerce rather than trust: a malformed or legacy record can carry a
    # non-dict here, and aggregate_node calls this unguarded — an
    # AttributeError would take the whole run down with it.
    fields = fields if isinstance(fields, dict) else {}
    checks = []
    checks += check_tagline_era(fields, year)
    checks += check_subbrand_era(fields, year)
    checks += check_nonexistent_mark(fields)
    checks += check_collab_era(fields, year)
    checks += check_style_prefix_vs_mark(fields)

    counts = {PASS: 0, FAIL: 0, UNKNOWN: 0}
    for c in checks:
        counts[c["status"]] += 1

    def _hard(cls):
        return [c for c in checks
                if c["status"] == FAIL and c["severity"] == CRITICAL
                and _FAIL_CLASS.get(c["id"]) == cls]

    spec, prov = _hard(SPEC), _hard(PROVENANCE)

    applicable = sum(_WEIGHT[c["severity"]] for c in checks)
    scored = sum(_WEIGHT[c["severity"]] for c in checks if c["status"] in (PASS, FAIL))
    internal = round(scored / applicable, 2) if applicable else 0.0

    fails = [c for c in checks if c["status"] == FAIL]
    if spec:
        summary = f"Specification contradiction: {spec[0]['evidence']}"
    elif prov:
        summary = f"Impossible product: {prov[0]['evidence']}"
    elif fails:
        summary = f"{len(fails)} deterministic logo check(s) failed: {fails[0]['evidence']}"
    elif counts[PASS]:
        summary = (f"{counts[PASS]} deterministic logo check(s) passed, "
                   f"{counts[UNKNOWN]} could not be run.")
    else:
        summary = "No deterministic logo check could be run — no marks were readable."

    return {"checks": checks,
            "spec_hard_fail": bool(spec), "provenance_hard_fail": bool(prov),
            "spec_reason": spec[0]["evidence"] if spec else "",
            "provenance_reason": prov[0]["evidence"] if prov else "",
            "counts": counts, "internal_coverage": internal,
            "failed": [c["id"] for c in fails], "summary": summary}
