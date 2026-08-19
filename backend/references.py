"""Maps a brand to its authentic reference images in ../data and assigns
each forensic dimension the most diagnostic reference crop.

The frontend loads these by filename from the static server (/data/<file>);
the live providers read the bytes off disk to send to Gemini. Keeping this in
one place means adding a new brand/product is a data edit, not a code change.
"""
import base64
import os

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

# The five forensic vision dimensions (Overall is the aggregated composite,
# not a separate agent — see graph.py).
DIMENSIONS = ["Logo", "Stitching", "Hardware", "Label", "Material"]

# WHICH PRODUCT THESE REFERENCE CROPS ARE OF. Everything below is one item: a
# TNF puffer jacket. That matters, because `select_references` used to return
# these for every submission regardless of what was submitted — so a T-shirt's
# Hardware dimension was compared against a jacket's zip pull, and a swimsuit's
# Material against a quilted shell.
#
# A deviation measured against the wrong garment is not a weak measurement, it
# is a meaningless one, and it entered the composite looking exactly like a
# real one. 112 of the 726 estimates in the August archive carried the note
# "NO_REFERENCE"; the ones that did NOT carry it are the worrying half.
REFERENCE_CATEGORY = "jacket"

# Categories the jacket crops can legitimately stand in for — same construction
# family, same hardware and shell vocabulary. Anything outside this list gets no
# reference rather than the wrong one.
REFERENCE_CATEGORY_KIN = {"jacket", "vest", "fleece", "hoodie"}

# brand -> { dimension: reference filename in ../data }
REFERENCES = {
    "TNF": {
        "Logo":      "71qrrGdyACL._AC_SX679_.jpg",   # embroidered chest logo close-up
        "Stitching": "6148oMGtmcL._AC_SX679_.jpg",   # back panel — baffle stitching
        "Hardware":  "71Dg9BU1aXL._AC_SX679_.jpg",   # zipper / pull close-up
        "Label":     "61wG4ykkKvL._AC_SX679_.jpg",   # interior care/spec label
        "Material":  "61bDpGS29JL._AC_SX679_.jpg",   # front open — shell fabric
        "_hero":     "613d-x0NedL._AC_SX679_.jpg",   # front zipped — overall reference
    },
}


def select_references(brand: str, category: str = "") -> dict:
    """{dimension: filename} for the brand, or {} when the stock references are
    of a different kind of product than the one submitted.

    An empty mapping is the correct, honest outcome: every dimension then runs
    with no reference and reports NOT_ASSESSABLE rather than returning a
    deviation measured against an unrelated garment. It lowers the measurement
    rate on paper and raises the truthfulness of the rate that remains.

    The real fix is a per-SKU reference library — `ref_source="product"` already
    loads one from Supabase and takes precedence over everything here. This
    function is the fallback, and a fallback should decline rather than guess.
    """
    refs = REFERENCES.get(brand)
    if refs is None:
        return {}
    cat = (category or "").strip().lower()
    if cat and cat not in REFERENCE_CATEGORY_KIN:
        return {}
    return refs


def reference_path(filename: str) -> str:
    return os.path.join(DATA_DIR, filename)


def load_ref_b64(filename: str):
    if not filename:                      # no reference mapped for this dimension
        return None
    try:
        with open(reference_path(filename), "rb") as f:
            return base64.b64encode(f.read()).decode()
    except OSError:
        return None
