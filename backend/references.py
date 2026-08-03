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


def select_references(brand: str) -> dict:
    """Return {dimension: filename} for the brand (falls back to TNF)."""
    return REFERENCES.get(brand, REFERENCES["TNF"])


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
