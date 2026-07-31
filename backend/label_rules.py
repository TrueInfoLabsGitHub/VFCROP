"""Deterministic label validation — runs OUTSIDE the model.

Every check here either passes or fails on evidence, with no vision confidence
involved: a fibre list that does not sum to 100, a style number in the wrong
format, an RN that resolves to a company other than the brand owner. That makes
them auditable, cheap, explainable to a client, and able to auto-fail a case
before any scoring happens.

The split that matters: a VLM is good at READING a label and terrible at
VALIDATING one. So the model's only job upstream is OCR into structured fields;
everything below is plain Python over those fields.

Three statuses, and the distinction is load-bearing:
  pass     — the check ran and the evidence is consistent
  fail     — the check ran and the evidence is inconsistent (a real tell)
  unknown  — the check could not run (field absent, registry unreachable).
             Never treated as either a pass or a fail.
"""
import difflib
import json
import os
import re
import threading
import time

import httpx

# --- severities -------------------------------------------------------------
CRITICAL = "critical"
STRONG = "strong"
SUPPORTING = "supporting"

PASS, FAIL, UNKNOWN = "pass", "fail", "unknown"


# ---------------------------------------------------------------------------
# Generic fibre names.
#
# The manufactured-fibre names are the generic names established by the FTC
# under the Textile Fiber Products Identification Act (16 CFR 303.7). The
# natural fibres are the names recognised under the Wool Products Labeling Act
# and ordinary trade usage. A name outside both lists is NOT automatically a
# tell — care labels are routinely multilingual, and "BAUMWOLLE" or "PAMUK" are
# perfectly legitimate. Only a near-miss of a known name (edit distance 1-2) is
# treated as a misspelling, which is the actual counterfeit signal.
# ---------------------------------------------------------------------------
_MANUFACTURED = {
    "acetate", "acrylic", "anidex", "aramid", "azlon", "elastoester", "elasterell-p",
    "fluoropolymer", "glass", "lastol", "lastrile", "lyocell", "melamine", "metallic",
    "modacrylic", "novoloid", "nylon", "nytril", "olefin", "pbi", "polyester",
    "polyethylene", "polypropylene", "rayon", "rubber", "saran", "spandex", "sulfar",
    "triacetate", "vinal", "vinyon",
}
_NATURAL = {
    "cotton", "wool", "silk", "linen", "flax", "hemp", "jute", "ramie", "sisal",
    "abaca", "coir", "kapok",
    "cashmere", "mohair", "alpaca", "angora", "camel", "llama", "vicuna", "guanaco",
    "down", "feather", "feathers", "leather", "suede", "bamboo", "viscose", "modal",
    "lycra", "elastane",           # trade names seen in EU-market declarations
}
VALID_FIBRES = _MANUFACTURED | _NATURAL

# Words that legitimately appear inside a fibre declaration and must not be
# mistaken for a fibre name.
_FIBRE_STOPWORDS = {
    "exclusive", "of", "decoration", "shell", "lining", "filling", "body", "hood",
    "trim", "insert", "and", "or", "outer", "inner", "fill", "back", "front",
    "sleeve", "pocket", "waterfowl", "recycled", "organic", "content", "fabric",
    "main", "part", "upper", "lower", "collar", "cuff", "panel", "membrane",
}

# FTC statutory phrases that counterfeiters routinely mangle. Correct use is
# mildly reassuring; a near-miss misspelling is a strong tell.
_STATUTORY_PHRASES = [
    "exclusive of decoration",
    "exclusive of ornamentation",
    "made in",
    "machine wash",
    "tumble dry",
    "do not bleach",
    "dry clean only",
]


# ---------------------------------------------------------------------------
# The North Face style-number formats.
#   old: a letter A or C followed by 3 alphanumerics   e.g. A71V, CH2M
#   new: 'NF0A' followed by 4 alphanumerics            e.g. NF0A3C8D
# RN / CA / RW registration codes are legitimate identifiers and are NEVER
# style numbers — mistaking one for a malformed style number is a false positive
# this codebase has produced before.
# ---------------------------------------------------------------------------
_STYLE_OLD = re.compile(r"^[AC][A-Z0-9]{3}$", re.I)
_STYLE_NEW = re.compile(r"^NF0A[A-Z0-9]{4}$", re.I)
_REGISTRATION_CODE = re.compile(r"^(RN|CA|RW)\s*\d{3,7}$", re.I)


def _result(cid, label, status, severity, evidence):
    return {"id": cid, "label": label, "status": status, "severity": severity,
            "evidence": evidence}


def _misspelling_of(token, vocabulary, cutoff=0.86):
    """Closest vocabulary entry if `token` is a near-miss, else None.

    'poleyester' -> 'polyester' (a tell). 'baumwolle' -> None (another
    language, not a tell)."""
    if token in vocabulary:
        return None
    m = difflib.get_close_matches(token, vocabulary, n=1, cutoff=cutoff)
    return m[0] if m else None


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------
_PCT = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")


def check_fiber_content(raw):
    """Fibre declaration: percentages sum to 100, names are real, formatting is
    not run-together. Entirely offline and entirely deterministic."""
    out = []
    text = (raw or "").strip()
    if not text:
        return [_result("F1", "Fibre percentages sum to 100", UNKNOWN, CRITICAL,
                        "No fibre content text was extracted.")]

    # F1 — percentages sum to 100 per declared component. Labels often carry
    # several components ("SHELL: 100% NYLON  FILLING: 90% DOWN 10% FEATHER"),
    # so split on component headers before summing.
    components = re.split(r"(?=(?:SHELL|LINING|FILLING|BODY|HOOD|TRIM|INSERT)\s*:)",
                          text, flags=re.I)
    sums, bad = [], []
    for comp in components:
        vals = [float(v) for v in _PCT.findall(comp)]
        if not vals:
            continue
        total = round(sum(vals), 1)
        sums.append(total)
        if abs(total - 100.0) > 0.5:
            bad.append((comp.strip()[:60], total))
    if not sums:
        out.append(_result("F1", "Fibre percentages sum to 100", UNKNOWN, CRITICAL,
                           "No percentages found in the fibre declaration."))
    elif bad:
        detail = "; ".join(f"'{c}' sums to {t}%" for c, t in bad)
        out.append(_result("F1", "Fibre percentages sum to 100", FAIL, CRITICAL,
                           f"A declared component does not total 100%: {detail}."))
    else:
        out.append(_result("F1", "Fibre percentages sum to 100", PASS, CRITICAL,
                           f"All {len(sums)} declared component(s) total 100%."))

    # F2 — every fibre name is a real generic name, or a foreign-language name.
    # Only a near-miss of a known name counts as a tell.
    tokens = [t.lower() for t in re.findall(r"[A-Za-z][A-Za-z\-]{2,}", text)]
    misspelled, unrecognised = [], []
    for t in tokens:
        if t in _FIBRE_STOPWORDS or t in VALID_FIBRES:
            continue
        near = _misspelling_of(t, VALID_FIBRES)
        if near:
            misspelled.append((t, near))
        else:
            unrecognised.append(t)
    if misspelled:
        detail = ", ".join(f"'{a}' (did you mean '{b}'?)" for a, b in misspelled[:4])
        out.append(_result("F2", "Fibre names are valid generic names", FAIL, CRITICAL,
                           f"Misspelled fibre name(s): {detail}."))
    else:
        note = ("All fibre names are valid generic names."
                if not unrecognised else
                f"All recognised fibre names are valid; {len(unrecognised)} token(s) "
                f"not in the English vocabulary "
                f"({', '.join(sorted(set(unrecognised))[:4])}) — likely another "
                f"language on a multilingual tag, not scored as a tell.")
        out.append(_result("F2", "Fibre names are valid generic names", PASS, CRITICAL, note))

    # F3 — run-together formatting, e.g. '100%POLYESTER' or 'NYLONLAMINATED'.
    runs = re.findall(r"\d\s*%[A-Za-z]", text) + re.findall(r"[a-z]{6,}[A-Z]{3,}", text)
    if runs:
        out.append(_result("F3", "Fibre declaration spacing", FAIL, STRONG,
                           f"Run-together formatting found ({len(runs)} instance(s)), "
                           f"e.g. '{runs[0]}'. Authentic tags space the percentage from "
                           f"the fibre name."))
    else:
        out.append(_result("F3", "Fibre declaration spacing", PASS, STRONG,
                           "Percentages and fibre names are correctly spaced."))
    return out


def check_style_number(style, product_family=""):
    """TNF style-number syntax. Absence is not a tell; a registration code
    mistaken for a style number is not a tell either."""
    s = (style or "").strip()
    if not s:
        return [_result("S1", "Style-number format", UNKNOWN, STRONG,
                        "No style number was visible — absence is not a defect.")]
    if _REGISTRATION_CODE.match(s):
        return [_result("S1", "Style-number format", UNKNOWN, STRONG,
                        f"'{s}' is an RN/CA/RW registration code, not a style number. "
                        f"Legitimate identifier; no style number was read.")]
    compact = re.sub(r"[\s\-]", "", s)
    if _STYLE_NEW.match(compact):
        return [_result("S1", "Style-number format", PASS, STRONG,
                        f"'{s}' matches the current NF0A+4 format.")]
    if _STYLE_OLD.match(compact):
        return [_result("S1", "Style-number format", PASS, STRONG,
                        f"'{s}' matches the legacy A/C+3 format.")]
    return [_result("S1", "Style-number format", FAIL, STRONG,
                    f"'{s}' matches neither the legacy A/C+3 nor the current NF0A+4 "
                    f"format.")]


def check_registration_syntax(rn, ca):
    """RN / CA numeric plausibility. Syntax only — resolution is check_rn_registry."""
    out = []
    r = re.sub(r"\D", "", str(rn or ""))
    if not r:
        out.append(_result("R1", "RN syntax", UNKNOWN, STRONG,
                           "No RN number was visible."))
    elif 2 <= len(r) <= 6:
        out.append(_result("R1", "RN syntax", PASS, STRONG,
                           f"RN {r} is a plausible registered identification number."))
    else:
        out.append(_result("R1", "RN syntax", FAIL, STRONG,
                           f"RN '{r}' has {len(r)} digits; issued RNs are 2-6 digits."))
    c = re.sub(r"\D", "", str(ca or ""))
    if not c:
        out.append(_result("R2", "CA syntax", UNKNOWN, SUPPORTING,
                           "No CA number was visible."))
    elif 2 <= len(c) <= 6:
        out.append(_result("R2", "CA syntax", PASS, SUPPORTING,
                           f"CA {c} is a plausible Canadian identification number."))
    else:
        out.append(_result("R2", "CA syntax", FAIL, SUPPORTING,
                           f"CA '{c}' has {len(c)} digits; issued CA numbers are 2-6."))
    return out


def check_statutory_phrasing(care_text):
    """Statutory wording is a fixed vocabulary; a near-miss is a strong tell."""
    text = (care_text or "").strip().lower()
    if not text:
        return [_result("P1", "Statutory phrasing", UNKNOWN, SUPPORTING,
                        "No care/statutory text was extracted.")]
    found, mangled = [], []
    for phrase in _STATUTORY_PHRASES:
        if phrase in text:
            found.append(phrase)
            continue
        # look for a near-miss of the same length window
        w = len(phrase)
        for i in range(0, max(1, len(text) - w + 1)):
            window = text[i:i + w]
            if window and difflib.SequenceMatcher(None, window, phrase).ratio() >= 0.88:
                mangled.append((window.strip(), phrase))
                break
    if mangled:
        detail = "; ".join(f"'{a}' should read '{b}'" for a, b in mangled[:3])
        return [_result("P1", "Statutory phrasing", FAIL, STRONG,
                        f"Statutory phrasing is misspelled: {detail}.")]
    if found:
        return [_result("P1", "Statutory phrasing", PASS, SUPPORTING,
                        f"Correct statutory phrasing: {', '.join(found[:3])}.")]
    return [_result("P1", "Statutory phrasing", UNKNOWN, SUPPORTING,
                    "No recognised statutory phrase appeared in the care text.")]


def check_cross_field(fields):
    """The cross-field join. Counterfeiters photograph one authentic label and
    reuse it across a run, so the fields stop agreeing with each other."""
    out = []
    sizes = [str(v).strip().upper() for k, v in fields.items()
             if k.startswith("size") and str(v or "").strip()]
    if len(sizes) < 2:
        out.append(_result("X1", "Size agrees across tags", UNKNOWN, STRONG,
                           "Fewer than two size-bearing tags were read."))
    elif len(set(sizes)) == 1:
        out.append(_result("X1", "Size agrees across tags", PASS, STRONG,
                           f"Size '{sizes[0]}' is consistent across {len(sizes)} tags."))
    else:
        out.append(_result("X1", "Size agrees across tags", FAIL, CRITICAL,
                           f"Size differs across tags: {', '.join(sorted(set(sizes)))}."))

    style = (fields.get("style_number") or "").strip()
    family = (fields.get("product_family") or "").strip()
    if not style or not family:
        out.append(_result("X2", "Style number suits the product family", UNKNOWN,
                           SUPPORTING,
                           "Style number or product family not available."))
    else:
        out.append(_result("X2", "Style number suits the product family", UNKNOWN,
                           SUPPORTING,
                           f"Style '{style}' cannot be resolved to a product family "
                           f"without the brand's style master; syntax checked "
                           f"separately."))
    return out


def check_batch_duplicates(style, prior):
    """Batch-level signal: one style number appearing across several different
    products is a strong indicator of a copied label, and needs no image at all.

    prior: iterable of (style_number, product_name) from earlier runs."""
    s = (style or "").strip().upper()
    if not s:
        return [_result("B1", "Style number not reused across products", UNKNOWN,
                        STRONG, "No style number to check against prior cases.")]
    others = {str(p or "").strip() for st, p in (prior or [])
              if str(st or "").strip().upper() == s and str(p or "").strip()}
    if len(others) > 1:
        return [_result("B1", "Style number not reused across products", FAIL, CRITICAL,
                        f"Style '{s}' appears on {len(others)} different products in "
                        f"this case history: {', '.join(sorted(others)[:3])}. A copied "
                        f"label is the usual explanation.")]
    return [_result("B1", "Style number not reused across products", PASS, STRONG,
                    f"Style '{s}' is not reused across differing products in the "
                    f"current history.")]


# ---------------------------------------------------------------------------
# FTC RN registry.
#
# The database is a plain Drupal GET form — no auth, no CSRF, no session:
#   https://www.ftc.gov/rn-database/search?search=61661
#   -> HTML table: Type | No. | Legal Business Name | Product Line
# So a real-time check is possible. It is cached, seeded, time-bounded, and
# fails soft: an unreachable registry yields UNKNOWN, never a FAIL.
# ---------------------------------------------------------------------------
RN_SEARCH_URL = "https://www.ftc.gov/rn-database/search"
RN_LOOKUP_ENABLED = os.environ.get("RN_LOOKUP", "1").strip().lower() in ("1", "true", "yes", "on")
RN_LOOKUP_TIMEOUT = float(os.environ.get("RN_LOOKUP_TIMEOUT", "8"))
_RN_CACHE_TTL = 60 * 60 * 24 * 30                     # registry changes slowly

# Verified 2026-07-31 against https://www.ftc.gov/rn-database/search?search=VF+Outdoor
# Seeds the cache so the check works offline, in tests, and when the FTC is down.
RN_SEED = {
    "61661": {"name": "VF OUTDOOR, INC.",
              "products": "BACKPACKS AND EQUIPMENT, APPAREL, FOOTWEAR"},
    "52755": {"name": "VF OUTDOOR", "products": "APPAREL"},
    "168178": {"name": "VF Outdoor, INC.", "products": "Unisex Apparel"},
    "116388": {"name": "VF OUTDOOR, LLC", "products": "APPAREL, WOOL, AND ACCESSORIES"},
    "76382": {"name": "VF OUTDOOR, LLC", "products": "WATCHES"},
    "115435": {"name": "NAPAPIJRI, A DIVISION OF VF OUTDOOR",
               "products": "APPAREL AND ACCESSORIES"},
}

# brand -> substrings that a legitimately-resolving RN's company name may contain
BRAND_OWNERS = {
    "TNF": ("VF OUTDOOR", "VF CORPORATION", "THE NORTH FACE", "VF IMAGEWEAR"),
    "Vans": ("VANS", "VF OUTDOOR", "VF CORPORATION"),
    "Timberland": ("TIMBERLAND", "TBL LICENSING", "VF CORPORATION"),
}

_rn_cache = {k: {**v, "ts": float("inf")} for k, v in RN_SEED.items()}   # seeds never expire
_rn_lock = threading.Lock()

# Cell values are wrapped in <a> tags and padded with newlines, so match rows
# and cells separately and strip markup per cell rather than trying to describe
# the whole row in one pattern.
_TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.I | re.S)
_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.I | re.S)


def _strip_html(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


def parse_rn_rows(html):
    """FTC results HTML -> [{kind, no, name, products}].

    Columns are Type | No. | Legal Business Name | Product Line."""
    rows = []
    for tr in _TR_RE.findall(html or ""):
        cells = [_strip_html(td) for td in _TD_RE.findall(tr)]
        if len(cells) >= 4 and cells[0].upper() in ("RN", "WPL") and cells[1].isdigit():
            rows.append({"kind": cells[0].upper(), "no": cells[1],
                         "name": cells[2], "products": cells[3]})
    return rows


def lookup_rn(rn, *, timeout=None):
    """Resolve an RN against the FTC registry. Returns a dict or None.

    None means 'could not resolve' — either genuinely absent OR unreachable.
    The caller distinguishes those via the `reachable` flag on the result."""
    digits = re.sub(r"\D", "", str(rn or ""))
    if not digits:
        return None
    now = time.time()
    with _rn_lock:
        hit = _rn_cache.get(digits)
        if hit and now - hit["ts"] < _RN_CACHE_TTL:
            return {"rn": digits, "name": hit["name"], "products": hit["products"],
                    "reachable": True, "cached": True}
    if not RN_LOOKUP_ENABLED:
        return None
    try:
        r = httpx.get(RN_SEARCH_URL, params={"search": digits},
                      timeout=timeout or RN_LOOKUP_TIMEOUT,
                      headers={"User-Agent": "VERITAS-brand-protection/1.0"},
                      follow_redirects=True)
        r.raise_for_status()
    except Exception:
        return None                                  # unreachable -> UNKNOWN upstream
    for row in parse_rn_rows(r.text):
        if row["no"] == digits:
            rec = {"name": row["name"], "products": row["products"]}
            with _rn_lock:
                _rn_cache[digits] = {**rec, "ts": now}
            return {"rn": digits, **rec, "reachable": True, "cached": False}
    return {"rn": digits, "name": "", "products": "", "reachable": True, "cached": False}


def check_rn_registry(rn, brand, lookup=None):
    """The strongest deterministic check available: does the RN on the tag
    resolve to the brand owner? A mismatch is a hard fail with zero model
    uncertainty; an unreachable registry is UNKNOWN, never a fail."""
    digits = re.sub(r"\D", "", str(rn or ""))
    if not digits:
        return [_result("R3", "RN resolves to the brand owner", UNKNOWN, CRITICAL,
                        "No RN number was visible on the label.")]
    rec = (lookup or lookup_rn)(digits)
    if rec is None:
        return [_result("R3", "RN resolves to the brand owner", UNKNOWN, CRITICAL,
                        f"The FTC RN registry could not be reached to resolve RN "
                        f"{digits}. Not counted as evidence either way.")]
    name = (rec.get("name") or "").upper()
    if not name:
        return [_result("R3", "RN resolves to the brand owner", FAIL, CRITICAL,
                        f"RN {digits} does not exist in the FTC registry. An "
                        f"unissued RN on a care label is a hard counterfeit "
                        f"indicator.")]
    owners = BRAND_OWNERS.get(brand, ())
    if any(o in name for o in owners):
        return [_result("R3", "RN resolves to the brand owner", PASS, CRITICAL,
                        f"RN {digits} resolves to '{rec['name']}', a {brand} brand "
                        f"owner. Product line: {rec.get('products') or 'n/a'}.")]
    return [_result("R3", "RN resolves to the brand owner", FAIL, CRITICAL,
                    f"RN {digits} resolves to '{rec['name']}', which is not a known "
                    f"{brand} brand owner ({', '.join(owners) or 'none configured'}).")]


# ---------------------------------------------------------------------------
# Roll-up
# ---------------------------------------------------------------------------
def validate(fields, brand="TNF", prior=None, lookup=None):
    """Run every deterministic check over OCR'd label fields.

    Returns {checks, hard_fail, counts, summary}. `hard_fail` is True only when
    a CRITICAL check actually FAILED — never because something was UNKNOWN.
    """
    fields = fields or {}
    checks = []
    checks += check_fiber_content(fields.get("fiber_content"))
    checks += check_style_number(fields.get("style_number"),
                                 fields.get("product_family", ""))
    checks += check_registration_syntax(fields.get("rn"), fields.get("ca"))
    checks += check_rn_registry(fields.get("rn"), brand, lookup=lookup)
    checks += check_statutory_phrasing(fields.get("care_text"))
    checks += check_cross_field(fields)
    checks += check_batch_duplicates(fields.get("style_number"), prior)

    counts = {PASS: 0, FAIL: 0, UNKNOWN: 0}
    for c in checks:
        counts[c["status"]] += 1
    fails = [c for c in checks if c["status"] == FAIL]
    hard = [c for c in fails if c["severity"] == CRITICAL]
    if hard:
        summary = (f"{len(hard)} deterministic hard fail(s): "
                   f"{hard[0]['evidence']}")
    elif fails:
        summary = f"{len(fails)} deterministic check(s) failed: {fails[0]['evidence']}"
    elif counts[PASS]:
        summary = (f"{counts[PASS]} deterministic check(s) passed, "
                   f"{counts[UNKNOWN]} could not be run.")
    else:
        summary = "No deterministic label check could be run — no fields were readable."
    return {"checks": checks, "hard_fail": bool(hard), "counts": counts,
            "failed": [c["id"] for c in fails], "summary": summary}


def dump(result):
    """Compact JSON for the run log / export."""
    return json.dumps(result, separators=(",", ":"))
