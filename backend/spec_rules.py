"""Per-style specification checks — the item against what TNF publishes for it.

The fourth rules module. `label_rules`, `material_rules` and `logo_rules` all
read TEXT alone. This one is different: it compares what the VISION AGENTS
DETECTED against the specification for the style code the garment itself claims.

That is the [DET-OWN] tier of the check spec, and it only became possible once
sku_registry.json was populated. "Does this logo look right" is an opinion.
"The registry says this style is screen printed and the agent classified the
application as embroidery" is a contradiction between two facts.

WHAT IT READS
  * the OCR'd label fields (style number, fibre content, care text)
  * the operator's product name and the pairing agent's item description
  * each dimension's DETECTED METHOD CLASS — the rubrics already resolve
    application_method, structure_type and component_type, and those enums were
    written to be comparable to a published spec

WHAT IT PRODUCES
  * spec / provenance hard fails, exactly like the other rules modules
  * PER-DIMENSION injections, because a decoration-method contradiction belongs
    on Logo and a fibre contradiction belongs on Material. `driver` then names
    the dimension that actually found something.

THE INVARIANT, UNCHANGED. Only FAILS inject. A garment matching its published
spec has told us nothing about authenticity — matching the spec is what a
competent counterfeiter copies first, straight off the product page. Facts
convict; nothing here clears.

NO REGISTRY ROW -> every check reports `unknown`. A style we have no spec for is
not a style that failed.
"""
import re

import sku_registry
from label_rules import CRITICAL, STRONG, SUPPORTING, PASS, FAIL, UNKNOWN, _result
from material_rules import PROVENANCE, SPEC, _spaced, _squashed

_WEIGHT = {CRITICAL: 3, STRONG: 2, SUPPORTING: 1}

# Which dimension each check's finding belongs to.
_DIMENSION = {
    "SP-1": "Label", "SP-2": "Logo", "SP-3": "Material", "SP-4": "Material",
    "SP-5": "Material", "SP-6": "Material", "SP-7": "Hardware", "SP-8": "Hardware",
}

_FAIL_CLASS = {"SP-1": SPEC, "SP-5": SPEC, "SP-6": SPEC, "SP-8": SPEC}

_INJECT_SCORE = {CRITICAL: 85, STRONG: 80, SUPPORTING: 60}

# Distinctive model tokens. Used only to decide whether the product the operator
# named and the style code the tag carries are the SAME GARMENT. Deliberately
# short: "jacket", "mens" and colour words carry no identity.
_MODEL_TOKENS = ("NUPTSE", "DENALI", "THERMOBALL", "MOUNTAIN", "HALFDOME",
                 "BOREALIS", "RESOLVE", "VENTURE", "ANTORA", "APEX", "STEEPTECH")

# Counterfeit slider brands. "YING" is the documented lookalike stamp; a genuine
# TNF pull reads YKK or is a moulded VISLON puller.
_FAKE_SLIDERS = ("YING", "YKX", "YKH", "KKY")

_FIBRE_WORDS = {"NYLON": "nylon", "POLYESTER": "polyester", "COTTON": "cotton",
                "WOOL": "wool", "ELASTANE": "elastane", "SPANDEX": "elastane"}

# A percentage, then up to three qualifier words, then the fibre itself.
_FIBRE_DECL = re.compile(
    r"(\d{1,3})\s*%\s*(?:[A-Z][A-Z-]*\s+){0,3}?(" + "|".join(_FIBRE_WORDS) + r")\b")


def _text_of(fields):
    fields = fields if isinstance(fields, dict) else {}
    parts = [fields.get("care_text"), fields.get("fiber_content"),
             fields.get("mark_text"), fields.get("product_family")]
    return " ".join(str(p) for p in parts if p)


def _row(fields, year=None):
    """The registry row for the style code the GARMENT claims, or None.

    Keyed on the style number read off the tag, never on the product the
    operator typed — the tag is the item's own claim about what it is, and that
    is the claim these checks test."""
    fields = fields if isinstance(fields, dict) else {}
    return sku_registry.lookup(fields.get("style_number") or "", year)


def _method_of(dim_results, name):
    """The method class a rubric detected for one dimension, or ''.

    `_aggregate_rubric` records it as `method`; UNKNOWN means the class was
    never resolved, which is not a contradiction."""
    for d in (dim_results or []):
        if isinstance(d, dict) and d.get("dimension") == name:
            m = str(d.get("method") or "").strip()
            return "" if m.upper() == "UNKNOWN" else m.lower()
    return ""


# ---------------------------------------------------------------------------
# SP-1 — the style code and the product must be the same garment
# ---------------------------------------------------------------------------
def check_style_matches_product(fields, product="", year=None):
    """The tag's style code resolves to a different model than the item is.

    Pure text, no vision, and one of the strongest signals available: a
    counterfeiter reusing one care tag across a product line produces exactly
    this. Only fires when BOTH names carry a distinctive model token and the
    tokens disagree — a product name we cannot classify asserts nothing."""
    row = _row(fields, year)
    if not row:
        return [_result("SP-1", "Style code matches the product", UNKNOWN, CRITICAL,
                        "No registry row for the style code on this tag.")]
    named = _squashed(product) + _squashed((fields or {}).get("product_family"))
    spec_name = _squashed(row.get("name") or "")
    spec_tok = next((t for t in _MODEL_TOKENS if t in spec_name), "")
    item_toks = [t for t in _MODEL_TOKENS if t in named]
    if not spec_tok or not item_toks:
        return [_result("SP-1", "Style code matches the product", UNKNOWN, CRITICAL,
                        "The product could not be classified to a known model.")]
    if spec_tok in item_toks:
        return [_result("SP-1", "Style code matches the product", PASS, CRITICAL,
                        f"Style resolves to {row['name']}, consistent with the item.")]
    return [_result("SP-1", "Style code matches the product", FAIL, CRITICAL,
                    f"The tag's style code belongs to the {row['name']}, but the item "
                    f"is a {item_toks[0].title()}. One care tag reused across a product "
                    f"line produces exactly this.")]


# ---------------------------------------------------------------------------
# SP-2 — decoration method (Logo)
# ---------------------------------------------------------------------------
def check_decoration_method(fields, dim_results=(), year=None):
    """The registry's published branding against the application the Logo agent
    classified.

    The Half Dome Hoodie is a water-based SCREEN PRINT; the puffers and the
    Mountain Jacket are EMBROIDERED. A screen-printed chest logo on a Nuptse is
    not a subtle geometry difference — it is the wrong process."""
    row = _row(fields, year)
    want = (row or {}).get("decoration_method")
    got = _method_of(dim_results, "Logo")
    if not row or not want:
        return [_result("SP-2", "Decoration method matches the spec", UNKNOWN, CRITICAL,
                        "No published decoration method for this style.")]
    if not got:
        return [_result("SP-2", "Decoration method matches the spec", UNKNOWN, CRITICAL,
                        "The Logo agent did not resolve an application method.")]
    if got == str(want).lower():
        return [_result("SP-2", "Decoration method matches the spec", PASS, CRITICAL,
                        f"Logo application reads as {got}, matching the published "
                        f"{want} branding.")]
    return [_result("SP-2", "Decoration method matches the spec", FAIL, CRITICAL,
                    f"The published branding for this style is {want}, but the logo was "
                    f"classified as {got}. That is a different manufacturing process, "
                    f"not a tolerance.")]


# ---------------------------------------------------------------------------
# SP-3 / SP-4 — fabric structure and fibre (Material)
# ---------------------------------------------------------------------------
def check_structure_type(fields, dim_results=(), year=None):
    """Published fabric architecture against what the Material agent classified."""
    row = _row(fields, year)
    want = (row or {}).get("structure_type")
    got = _method_of(dim_results, "Material")
    if not row or not want:
        return [_result("SP-3", "Fabric structure matches the spec", UNKNOWN, STRONG,
                        "No published structure type for this style.")]
    if not got:
        return [_result("SP-3", "Fabric structure matches the spec", UNKNOWN, STRONG,
                        "The Material agent did not resolve a structure type.")]
    if got == str(want).lower():
        return [_result("SP-3", "Fabric structure matches the spec", PASS, STRONG,
                        f"Structure reads as {got}, matching the published {want}.")]
    return [_result("SP-3", "Fabric structure matches the spec", FAIL, STRONG,
                    f"This style is published as {want}; the fabric was classified as "
                    f"{got}.")]


def check_primary_fibre(fields, year=None):
    """The declared dominant fibre against the published one.

    Compares the DOMINANT fibre only. Solids and heathers differ in their exact
    split (73/27 cotton-poly against 56/44 poly-cotton on the Half Dome Hoodie),
    so the registry lists every acceptable dominant fibre and this checks
    membership rather than a percentage."""
    row = _row(fields, year)
    want = (row or {}).get("primary_fibres")
    if not row or not want:
        return [_result("SP-4", "Primary fibre matches the spec", UNKNOWN, STRONG,
                        "No published fibre for this style.")]
    decl = str((fields or {}).get("fiber_content") or "")
    if not decl.strip():
        return [_result("SP-4", "Primary fibre matches the spec", UNKNOWN, STRONG,
                        "No fibre declaration was read.")]

    # The highest-percentage fibre named in the FIRST declared component.
    first = re.split(r"(?=\b[A-Z][A-Z0-9 /&+-]{2,}\s*:)", _spaced(decl))
    block = next((b for b in first if re.search(r"\d\s*%", b)), _spaced(decl))
    # Qualifiers sit between the percentage and the fibre on almost every modern
    # TNF tag — "100% RECYCLED POLYESTER", "100% POST-CONSUMER RECYCLED NYLON".
    # Matching only the next word finds "RECYCLED", which is not a fibre, and
    # silently turns this check off on genuine stock.
    pairs = [(float(p), _FIBRE_WORDS[w])
             for p, w in re.findall(_FIBRE_DECL, block)
             if w in _FIBRE_WORDS]
    if not pairs:
        return [_result("SP-4", "Primary fibre matches the spec", UNKNOWN, STRONG,
                        "No recognised fibre with a percentage in the declaration.")]
    dominant = max(pairs)[1]
    if dominant in [str(w).lower() for w in want]:
        return [_result("SP-4", "Primary fibre matches the spec", PASS, STRONG,
                        f"Dominant fibre is {dominant}, which this style is built from.")]
    return [_result("SP-4", "Primary fibre matches the spec", FAIL, STRONG,
                    f"The tag declares {dominant} as the dominant fibre; this style is "
                    f"published as {' or '.join(str(w) for w in want)}.")]


# ---------------------------------------------------------------------------
# SP-5 / SP-6 — insulation and membrane, both pure text contradictions
# ---------------------------------------------------------------------------
def check_insulation_kind(fields, year=None):
    """A down claim on a synthetic style, or a synthetic claim on a down style."""
    row = _row(fields, year)
    kind = (row or {}).get("insulation_kind")
    if not row or not kind:
        return [_result("SP-5", "Insulation type matches the spec", UNKNOWN, CRITICAL,
                        "This style has no published insulation.")]
    spaced = _spaced(_text_of(fields))
    squashed = _squashed(spaced)
    says_down = bool(re.search(r"\d\s*%\s*DOWN\b|\bDOWN\s+FILL", spaced))
    says_synth = any(k in squashed for k in ("THERMOBALL", "HEATSEEKER"))

    if kind == "synthetic" and says_down:
        return [_result("SP-5", "Insulation type matches the spec", FAIL, CRITICAL,
                        f"{row['name']} is insulated with {row.get('fill_type')}, a "
                        f"synthetic, but the tag declares down content.")]
    if kind == "down" and says_synth and not says_down:
        return [_result("SP-5", "Insulation type matches the spec", FAIL, CRITICAL,
                        f"{row['name']} is a down product, but the tag names a synthetic "
                        f"insulation instead.")]
    if says_down or says_synth:
        return [_result("SP-5", "Insulation type matches the spec", PASS, CRITICAL,
                        f"Declared insulation is consistent with a {kind} product.")]
    return [_result("SP-5", "Insulation type matches the spec", UNKNOWN, CRITICAL,
                    "No insulation claim was read from the tag.")]


def check_membrane_matches_spec(fields, year=None):
    """The membrane the tag names against the one this style is built on.

    Asymmetric on purpose. Naming the WRONG membrane is a contradiction. Naming
    NO membrane is not: a care tag that simply does not mention the laminate is
    ordinary, and treating silence as a tell would fail genuine stock."""
    row = _row(fields, year)
    if not row:
        return [_result("SP-6", "Membrane matches the spec", UNKNOWN, CRITICAL,
                        "No registry row for the style code on this tag.")]
    want = row.get("membrane")
    squashed = _squashed(_text_of(fields))
    named = [n for n in ("GORETEX", "DRYVENT", "HYVENT", "FUTURELIGHT", "WINDWALL")
             if n in squashed]
    if not named:
        return [_result("SP-6", "Membrane matches the spec", UNKNOWN, CRITICAL,
                        "No membrane is named on the tag — silence is not a tell.")]
    want_sq = _squashed(want or "")
    if not want_sq:
        return [_result("SP-6", "Membrane matches the spec", FAIL, CRITICAL,
                        f"The tag names {named[0]}, but {row['name']} is published with "
                        f"no membrane.")]
    if want_sq in named:
        return [_result("SP-6", "Membrane matches the spec", PASS, CRITICAL,
                        f"Tag names {want}, matching the published construction.")]
    return [_result("SP-6", "Membrane matches the spec", FAIL, CRITICAL,
                    f"The tag names {named[0]}, but {row['name']} is built on {want}.")]


# ---------------------------------------------------------------------------
# SP-7 / SP-8 — hardware
# ---------------------------------------------------------------------------
def check_zip_presence(fields, dim_results=(), year=None):
    """A zip detected on a garment published without one.

    The Half Dome Pullover Hoodie has no zip at all. A zip on one is not a
    variant — it is a different garment."""
    row = _row(fields, year)
    if not row or row.get("has_zip") is not False:
        return [_result("SP-7", "Zip presence matches the spec", UNKNOWN, STRONG,
                        "This style is published with a zip, or has no published "
                        "hardware — the check does not apply.")]
    got = _method_of(dim_results, "Hardware")
    if not got:
        return [_result("SP-7", "Zip presence matches the spec", UNKNOWN, STRONG,
                        "The Hardware agent did not resolve a component type.")]
    if got == "zip":
        return [_result("SP-7", "Zip presence matches the spec", FAIL, STRONG,
                        f"A zip was detected, but {row['name']} is a pullover published "
                        f"with no zip.")]
    return [_result("SP-7", "Zip presence matches the spec", PASS, STRONG,
                    f"Detected hardware is a {got}, consistent with a pullover.")]


def check_slider_brand(fields):
    """A documented counterfeit slider stamp anywhere in the transcribed text.

    Needs no registry: 'YING' is not a zip brand that exists on genuine product
    in any year, on any style."""
    squashed = _squashed(_text_of(fields))
    if not squashed:
        return [_result("SP-8", "Slider brand", UNKNOWN, CRITICAL,
                        "No text was extracted.")]
    hit = next((f for f in _FAKE_SLIDERS if f in squashed), "")
    if hit:
        return [_result("SP-8", "Slider brand", FAIL, CRITICAL,
                        f"The text carries '{hit}', a documented counterfeit stamp "
                        f"imitating YKK.")]
    return [_result("SP-8", "Slider brand", PASS, CRITICAL,
                    "No counterfeit slider stamp found.")]


# ---------------------------------------------------------------------------
# Roll-up
# ---------------------------------------------------------------------------
def validate(fields, product="", dim_results=(), year=None):
    """Every per-style check. Same contract as the other rules modules, plus
    `injections`: {dimension -> {score, confidence, internal_coverage, ...}}."""
    fields = fields if isinstance(fields, dict) else {}
    checks = []
    checks += check_style_matches_product(fields, product, year)
    checks += check_decoration_method(fields, dim_results, year)
    checks += check_structure_type(fields, dim_results, year)
    checks += check_primary_fibre(fields, year)
    checks += check_insulation_kind(fields, year)
    checks += check_membrane_matches_spec(fields, year)
    checks += check_zip_presence(fields, dim_results, year)
    checks += check_slider_brand(fields)

    counts = {PASS: 0, FAIL: 0, UNKNOWN: 0}
    for c in checks:
        counts[c["status"]] += 1

    def _hard(cls):
        return [c for c in checks
                if c["status"] == FAIL and c["severity"] == CRITICAL
                and _FAIL_CLASS.get(c["id"]) == cls]

    spec, prov = _hard(SPEC), _hard(PROVENANCE)

    # Per-dimension coverage and injection. A dimension's coverage counts only
    # the checks that concern IT, so a resolved Logo check never inflates
    # Material.
    injections, per_dim = {}, {}
    for dim in set(_DIMENSION.values()):
        mine = [c for c in checks if _DIMENSION.get(c["id"]) == dim]
        applicable = sum(_WEIGHT[c["severity"]] for c in mine)
        scored = sum(_WEIGHT[c["severity"]] for c in mine
                     if c["status"] in (PASS, FAIL))
        per_dim[dim] = round(scored / applicable, 2) if applicable else 0.0
        fails = [c for c in mine if c["status"] == FAIL]
        if not fails:
            continue                       # a PASS never injects. Facts convict only.
        worst = max(fails, key=lambda c: _INJECT_SCORE.get(c["severity"], 0))
        injections[dim] = {
            "score": _INJECT_SCORE.get(worst["severity"], 0),
            "confidence": 0.85,
            "internal_coverage": per_dim[dim],
            "finding": f"Spec contradiction: {worst['evidence']}",
            "check_id": worst["id"],
        }

    applicable = sum(_WEIGHT[c["severity"]] for c in checks)
    scored = sum(_WEIGHT[c["severity"]] for c in checks if c["status"] in (PASS, FAIL))
    internal = round(scored / applicable, 2) if applicable else 0.0

    fails = [c for c in checks if c["status"] == FAIL]
    if spec:
        summary = f"Specification contradiction: {spec[0]['evidence']}"
    elif prov:
        summary = f"Impossible product: {prov[0]['evidence']}"
    elif fails:
        summary = f"{len(fails)} per-style check(s) failed: {fails[0]['evidence']}"
    elif counts[PASS]:
        summary = (f"{counts[PASS]} per-style check(s) passed against the published "
                   f"specification, {counts[UNKNOWN]} could not be run.")
    else:
        summary = ("No per-style check could be run — no registry row, or nothing "
                   "readable to compare.")

    return {"checks": checks,
            "spec_hard_fail": bool(spec), "provenance_hard_fail": bool(prov),
            "spec_reason": spec[0]["evidence"] if spec else "",
            "provenance_reason": prov[0]["evidence"] if prov else "",
            "counts": counts, "internal_coverage": internal,
            "coverage_by_dimension": per_dim, "injections": injections,
            "failed": [c["id"] for c in fails], "summary": summary}
