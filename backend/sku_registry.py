"""Per-style specification registry — the anchor every [DET-OWN] check needs.

WHAT THIS IS. A lookup from a TNF style code to what TNF itself publishes about
that garment: decoration method, zipper line and gauge, hood panel count, layer
count and seam-sealing status, shell/lining denier, fill power, pocket count.
With it, "does this logo look right" becomes "does this logo's application
method match the one the manufacturer published for this style" — a fact.

WHAT THIS IS NOT. It ships EMPTY, and an empty registry is not a degraded mode:
every lookup returns None, every check built on it reports `unknown`, and nothing
asserts anything. That is deliberate. A registry populated with guessed spec
values would produce confident, terminal, wrong verdicts on genuine product —
strictly worse than having no registry at all, because a deterministic fail
carries no model uncertainty for anything downstream to moderate.

Populating it is a DATA task, not a code task. Sources, per the check spec:
  * thenorthface.com PDPs   — branding string, materials block, feature list
  * REI spec tables         — zipper line AND gauge per position (TNF publishes
                              the line but not the gauge)
  * evo                     — `Seam Sealing: Fully Taped` as a structured field

VERSION EVERY ROW BY YEAR. TNF's own Nuptse cuff spec and REI's disagree because
the cuff genuinely changed between seasons, and comparing a 2019 garment against
a 2025 row is the single largest false-positive generator in this design. A
lookup for a year older than every row returns None rather than the closest row,
because "we have no spec for this era" and "the spec is X" are different answers.

File format — style code -> year -> spec:

    {
      "NF0A3C8D": {
        "2022": {"decoration_method": "embroidered",
                 "logo_placements": ["left_chest", "back_right_shoulder"],
                 "zipper_line": "VISLON", "hood_panel_count": 3,
                 "layer_count": "2L", "seam_sealing": "fully_taped",
                 "fill_power": 700, "pocket_count": 4},
        "2019": {"...": "..."}
      }
    }
"""
import json
import os
import re
import threading

_DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "sku_registry.json")

_lock = threading.Lock()
_cache = None
_cache_path = None

# NF0A + 5, the current namespace; nanamica/Purple Label uses NN. Older codes are
# a letter plus three characters (A71V).
_STYLE = re.compile(r"\b(NF0A[A-Z0-9]{4,5}|NN[A-Z0-9]{4,7}|[AC][A-Z0-9]{3})\b", re.I)


def path():
    return os.environ.get("SKU_REGISTRY_PATH") or _DEFAULT_PATH


def _load():
    """Read the registry once. A missing or malformed file is an EMPTY registry,
    never an exception — a scoring run must not die because an optional data
    file is absent, and an empty registry already means 'assert nothing'."""
    global _cache, _cache_path
    p = path()
    with _lock:
        if _cache is not None and _cache_path == p:
            return _cache
        try:
            with open(p, encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                data = {}
        except (OSError, ValueError):
            data = {}
        _cache, _cache_path = data, p
        return _cache


def reload():
    """Drop the cache. For tests and for reloading after a scrape."""
    global _cache, _cache_path
    with _lock:
        _cache, _cache_path = None, None


def available():
    return bool(_load())


def normalise_style(raw):
    """A style code out of free text, upper-cased, or ''.

    Punctuation becomes whitespace first so the word boundary survives — with
    spaces merely deleted, 'style nf0a3c8d' collapses to 'STYLENF0A3C8D' and the
    leading \b never matches. The de-spaced second pass is prefix-anchored on
    NF0A/NN only: the legacy A71V shape is four characters and would match
    inside almost any run of text without a boundary to hold it."""
    text = re.sub(r"[^A-Z0-9]+", " ", str(raw or "").upper())
    m = _STYLE.search(text)
    if m:
        return m.group(0)
    m = re.search(r"(NF0A[A-Z0-9]{4,5}|NN[A-Z0-9]{4,7})", text.replace(" ", ""))
    return m.group(0) if m else ""


def lookup(style, year=None):
    """The spec row for this style at this year, or None.

    None means 'no answer', and every caller must treat it as `unknown` rather
    than as a pass. With no year, the most recent row is returned — that is the
    right default for current product and the wrong one for vintage, which is
    why the era-sensitive checks pass a year explicitly.
    """
    data = _load()
    if not data:
        return None
    key = normalise_style(style)
    rows = data.get(key) or data.get(key.upper())
    if not isinstance(rows, dict) or not rows:
        return None

    years = []
    for k in rows:
        try:
            years.append(int(k))
        except (TypeError, ValueError):
            continue
    if not years:
        return None
    if year is None:
        return rows[str(max(years))]

    # The closest row AT OR BEFORE the garment's year. Never a later row: a spec
    # published after the garment was made describes a different garment.
    eligible = [y for y in years if y <= int(year)]
    if not eligible:
        return None
    return rows[str(max(eligible))]


def field(style, name, year=None):
    """One spec field, or None. The shape most checks want."""
    row = lookup(style, year)
    if not isinstance(row, dict):
        return None
    return row.get(name)
