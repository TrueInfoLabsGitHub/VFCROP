"""Enterprise system adapters — PIM, DAM, Authorized Supplier Registry, TMS.

Each adapter presents the SOW-shaped interface (Epic 8) backed by what this
deployment actually has today: the product catalog and UPC master for PIM/DAM,
and seed registries under data/ for suppliers and shipping lanes. Swapping a
seed for the real VF endpoint is a change inside ONE function here — the
routes and the frontend never move.

Also home to the intake extraction call (E3-06/07, E4-14): one vision request
that reads brand, UPC, style number, country of origin, visible text and
region bounding boxes out of the uploaded photos.
"""
from __future__ import annotations

import json
import os
import re
import time

import httpx

import supa
from providers import ALLOW_MOCK, OPENAI_API_KEY, OPENAI_MODEL, _UPC_CATALOG

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUPPLIERS_PATH = os.environ.get("SUPPLIERS_PATH", os.path.join(_ROOT, "data", "suppliers.json"))
LANES_PATH = os.environ.get("LANES_PATH", os.path.join(_ROOT, "data", "shipping_lanes.json"))

# ---------------------------------------------------------------------------
# PIM — product metadata by UPC or style number.
# The catalog rows the UPC master already holds, enriched with the style-level
# details the SOW's product card wants. Absence of a row is never evidence.
# ---------------------------------------------------------------------------
_PIM_DETAILS = {
    "193393578024": {"style": "NF0A3C8D", "colorway": "Recycled TNF Black",
                     "season": "FW24", "msrp": 320.0},
    "191167589436": {"style": "VN000D3HY28", "colorway": "Black/White",
                     "season": "Core", "msrp": 70.0},
    "887168539921": {"style": "TB010061713", "colorway": "Wheat Nubuck",
                     "season": "Core", "msrp": 198.0},
}
_STYLE_TO_UPC = {v["style"].lower(): u for u, v in _PIM_DETAILS.items()}


def _catalog_product_for(name: str):
    """Best-effort match of a PIM name to a catalog product (for DAM images).

    An empty or near-empty name matches NOTHING: `"" in pn` is true for every
    product, which used to return the first catalog row as a bogus reference for
    any case that had no PIM match. A real match needs a non-trivial name and a
    shared word, not a bare substring."""
    try:
        name_l = (name or "").strip().lower()
        if len(name_l) < 4 or not supa.available():
            return None
        name_words = {w for w in re.split(r"\W+", name_l) if len(w) > 2}
        for p in supa.list_products():
            pn = (p.get("name") or "").strip().lower()
            if not pn:
                continue
            if pn == name_l or pn in name_l or name_l in pn:
                return p
            pn_words = {w for w in re.split(r"\W+", pn) if len(w) > 2}
            # at least two shared meaningful words → same product family
            if len(name_words & pn_words) >= 2:
                return p
    except Exception:
        pass
    return None


def pim_lookup(upc: str = "", style: str = "") -> dict:
    upc = re.sub(r"\D", "", upc or "")
    if not upc and style:
        upc = _STYLE_TO_UPC.get(style.strip().lower(), "")
    if upc and upc in _UPC_CATALOG:
        brand, name = _UPC_CATALOG[upc]
        det = _PIM_DETAILS.get(upc, {})
        prod = _catalog_product_for(name)
        return {"matched": True, "upc": upc, "brand": brand, "name": name,
                "style": det.get("style", ""), "colorway": det.get("colorway", ""),
                "season": det.get("season", ""), "msrp": det.get("msrp"),
                "catalog_product_id": (prod or {}).get("id", ""),
                "reference_images": (prod or {}).get("images", [])[:3]}
    return {"matched": False, "upc": upc, "style": style or ""}


def dam_images(style: str = "", name: str = "") -> dict:
    """Reference imagery for a style — served from the catalog bucket."""
    if style and not name:
        upc = _STYLE_TO_UPC.get(style.strip().lower(), "")
        if upc:
            name = _UPC_CATALOG[upc][1]
    prod = _catalog_product_for(name)
    if not prod:
        return {"available": False, "images": []}
    pid = prod.get("id", "")
    return {"available": True, "product_id": pid, "name": prod.get("name", ""),
            "images": [f"/api/products/{pid}/img/{fn}" for fn in (prod.get("images") or [])[:6]]}


# ---------------------------------------------------------------------------
# Authorized Supplier Registry + TMS shipping lanes — seed registries.
# ---------------------------------------------------------------------------
def _load_json(path, fallback):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return fallback


def suppliers_query(factory: str = "", country: str = "", name: str = "") -> dict:
    """authorized / not_authorized / unknown / ghost_shift, plus factory rows.
    Unknown is the honest default — absence of a row convicts nothing."""
    rows = _load_json(SUPPLIERS_PATH, [])
    f, c, n = factory.strip().lower(), country.strip().lower(), name.strip().lower()
    hits = [r for r in rows if
            (f and f in (r.get("factory_code") or "").lower()) or
            (n and n in (r.get("name") or "").lower()) or
            (c and not f and not n and c in (r.get("country") or "").lower())]
    if not hits and not rows:
        return {"status": "unknown", "matches": [],
                "note": "supplier registry not populated"}
    if not hits:
        return {"status": "not_authorized" if f or n else "unknown", "matches": [],
                "note": "no matching factory in the authorized registry"}
    status = "authorized"
    if any(r.get("ghost_shift_flag") for r in hits):
        status = "ghost_shift"
    return {"status": status, "matches": hits[:5]}


def tms_lanes(origin: str = "", dest: str = "") -> dict:
    """Known VF shipping lanes between two countries/ports (E5-06, E8-09)."""
    lanes = _load_json(LANES_PATH, [])
    o, d = origin.strip().lower(), dest.strip().lower()
    hits = [l for l in lanes if
            (not o or o in (l.get("origin") or "").lower()) and
            (not d or d in (l.get("destination") or "").lower())]
    return {"match": bool(hits), "lanes": hits[:6],
            "note": "matches a known VF shipping lane" if hits
            else "no matching VF lane — route unverified"}


# ---------------------------------------------------------------------------
# Intake extraction — one vision call over the uploaded photos (E3-06/07).
# ---------------------------------------------------------------------------
_EXTRACT_PROMPT = """You are an intake analyst for a counterfeit-investigation system.
Look at the product photos and report ONLY what is visible. Never guess from
brand reputation; if something is not readable, use null and confidence 0.

Return JSON only:
{
 "brand": {"value": "TNF|Vans|Timberland|Unknown", "confidence": 0-1},
 "upc":   {"value": "digits or null", "confidence": 0-1, "image_index": 0-based or null},
 "style_number": {"value": "string or null", "confidence": 0-1},
 "country_of_origin": {"value": "string or null", "confidence": 0-1},
 "category": {"value": "e.g. jacket|t-shirt|hoodie|hat|boot|backpack or null", "confidence": 0-1},
 "colorway": {"value": "dominant colour(s), e.g. 'Red/Black' or null", "confidence": 0-1},
 "pocket_config": {"value": "brief, e.g. '2 hand + 1 chest', 'none' or null", "confidence": 0-1},
 "text_detected": ["up to 6 short strings actually readable in the photos"],
 "boxes": [{"region": "logo|label|hardware|stitching", "image_index": 0,
            "x": 0-1, "y": 0-1, "w": 0-1, "h": 0-1}]
}
colorway is the visible colour, always readable. category is the garment type.
pocket_config is only what is visible; use null if you cannot see the pockets.
Boxes are normalized to each image; include one box per clearly visible region,
at most 6 boxes total."""


def _mock_extract(n_images: int) -> dict:
    return {"mode": "mock",
            "brand": {"value": "TNF", "confidence": 0.5},
            "upc": {"value": None, "confidence": 0, "image_index": None},
            "style_number": {"value": None, "confidence": 0},
            "country_of_origin": {"value": None, "confidence": 0},
            "category": {"value": None, "confidence": 0},
            "colorway": {"value": None, "confidence": 0},
            "pocket_config": {"value": None, "confidence": 0},
            "text_detected": [],
            "boxes": [{"region": "logo", "image_index": 0,
                       "x": 0.36, "y": 0.18, "w": 0.28, "h": 0.2}] if n_images else []}


def extract(images_b64: list[str]) -> dict:
    imgs = [b for b in (images_b64 or []) if b][:4]
    if not imgs:
        raise ValueError("at least one image is required")
    if not OPENAI_API_KEY:
        if ALLOW_MOCK:
            return _mock_extract(len(imgs))
        raise RuntimeError("no engine configured — set OPENAI_API_KEY (or ALLOW_MOCK=1)")
    content = [{"type": "text", "text": f"{len(imgs)} photos follow, in order."}]
    for b in imgs:
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b}", "detail": "low"}})
    t0 = time.time()
    r = httpx.post("https://api.openai.com/v1/chat/completions",
                   headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                   json={"model": OPENAI_MODEL,
                         "messages": [{"role": "system", "content": _EXTRACT_PROMPT},
                                      {"role": "user", "content": content}],
                         "response_format": {"type": "json_object"}},
                   timeout=30)                       # SOW E8-10: 30s timeout
    r.raise_for_status()
    out = json.loads(r.json()["choices"][0]["message"]["content"])
    out["mode"] = "live"
    out["latency_ms"] = int((time.time() - t0) * 1000)
    return out
