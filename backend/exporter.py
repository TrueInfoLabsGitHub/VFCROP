"""Excel export of analysis runs.

Builds a real .xlsx workbook (openpyxl) from the saved run history, one row per
analysis, with every detail as a column AND the suspect / UPC photos embedded as
thumbnails in the row. Thumbnails are produced with Pillow at save time so the
stored history stays small.
"""
import base64
import io
import math

from openpyxl import Workbook
from openpyxl.drawing.image import Image as XLImage
from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, OneCellAnchor
from openpyxl.drawing.xdr import XDRPositiveSize2D
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.utils.units import pixels_to_EMU

try:
    from PIL import Image as PILImage
    _PIL = True
except Exception:                       # pragma: no cover
    _PIL = False

DIMS = ["Logo", "Stitching", "Hardware", "Label", "Material"]
_THUMB_PX = 150
_CELL = 46                               # px, box each tiled thumbnail fits into
_GAP = 3                                 # px, gap between tiled thumbnails
_PERROW = 3                              # thumbnails per grid row inside a cell

_BAND_FONT = {"authentic": "1E8A4C", "caution": "B07D0A", "counterfeit": "C0392B"}


def thumb(b64, px=_THUMB_PX):
    """Downscale a base64 JPEG to a small thumbnail (returns base64 JPEG)."""
    if not b64:
        return ""
    try:
        raw = base64.b64decode(b64)
        if not _PIL:
            return b64
        im = PILImage.open(io.BytesIO(raw)).convert("RGB")
        im.thumbnail((px, px))
        out = io.BytesIO()
        im.save(out, format="JPEG", quality=80)
        return base64.b64encode(out.getvalue()).decode()
    except Exception:
        return ""


# (header label, column width). Image columns are placed up front for visibility.
# Suspect + References hold multiple tiled thumbnails, so they are wide enough
# for _PERROW thumbnails across.
_IMG_COL_W = round(_PERROW * (_CELL + _GAP) / 7) + 1
_COLUMNS = [
    ("#", 5), ("Timestamp (UTC)", 20), ("Case", 15), ("Brand", 8),
    ("Engine", 15), ("Product", 22), ("Verdict", 21), ("Score", 7),
    ("Suspect", _IMG_COL_W), ("References", _IMG_COL_W), ("UPC image", 16),
    *[(f"{d}\nscore", 9) for d in DIMS],
    ("UPC status", 12), ("UPC read", 16), ("UPC expected", 14),
    ("Verifier", 11), ("Confidence", 11),
    ("Tokens", 10), ("Cost ($)", 11), ("Latency (s)", 11),
    *[(f"{d} finding", 46) for d in DIMS],
]
_SUSPECT_COL = 9
_REF_COL = 10
_UPC_COL = 11


def _fnum(v, nd=0):
    try:
        return round(float(v), nd) if nd else int(round(float(v)))
    except (TypeError, ValueError):
        return ""


def build_workbook(runs):
    """runs: list of record dicts (oldest first). Returns .xlsx bytes."""
    wb = Workbook()
    ws = wb.active
    ws.title = "VERITAS analyses"

    head_fill = PatternFill("solid", fgColor="1A2B3C")
    head_font = Font(color="FFFFFF", bold=True, size=10)
    head_align = Alignment(horizontal="center", vertical="center", wrap_text=True)

    for ci, (label, width) in enumerate(_COLUMNS, start=1):
        c = ws.cell(row=1, column=ci, value=label)
        c.fill = head_fill
        c.font = head_font
        c.alignment = head_align
        ws.column_dimensions[get_column_letter(ci)].width = width
    ws.row_dimensions[1].height = 30
    ws.freeze_panes = "A2"

    top = Alignment(vertical="top", wrap_text=True)
    for i, rec in enumerate(runs, start=1):
        r = i + 1
        dims = rec.get("dimensions") or {}
        upc = rec.get("upc") or {}
        band = rec.get("band") or ""
        vals = [
            i, rec.get("created_at", ""), rec.get("case_id", ""), rec.get("brand", ""),
            rec.get("engine", ""), rec.get("product", ""), rec.get("verdict", ""),
            _fnum(rec.get("score")), "", "", "",
            *[_fnum((dims.get(d) or {}).get("score")) for d in DIMS],
            upc.get("status", ""), upc.get("extracted", ""), upc.get("expected", ""),
            rec.get("verifier", ""), rec.get("confidence", ""),
            _fnum(rec.get("tokens")),
            round(float(rec.get("cost") or 0), 4),
            round(float(rec.get("latency_ms") or 0) / 1000, 1),
            *[(dims.get(d) or {}).get("finding", "") for d in DIMS],
        ]
        for ci, v in enumerate(vals, start=1):
            cell = ws.cell(row=r, column=ci, value=v)
            cell.alignment = top
        # colour the Verdict + Score by band
        if band in _BAND_FONT:
            f = Font(color=_BAND_FONT[band], bold=True)
            ws.cell(row=r, column=7).font = f
            ws.cell(row=r, column=8).font = f

        # image lists (with back-compat for older single-image records)
        sus = rec.get("suspect_thumbs") or (
            [rec["suspect_thumb"]] if rec.get("suspect_thumb") else [])
        refs = rec.get("reference_thumbs") or []
        upc = [rec["upc_thumb"]] if rec.get("upc_thumb") else []
        gr = max(_embed_grid(ws, sus, _SUSPECT_COL, r),
                 _embed_grid(ws, refs, _REF_COL, r),
                 _embed_grid(ws, upc, _UPC_COL, r), 1)
        ws.row_dimensions[r].height = max(76, gr * (_CELL + _GAP) * 0.75)

    if not runs:
        ws.cell(row=2, column=1, value="No analyses saved yet.")

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _embed_grid(ws, thumbs, col, row):
    """Tile every thumbnail in a grid anchored inside a single cell.

    openpyxl anchors one image per cell by default, so multiple images would
    stack on top of each other. We instead give each image an explicit
    OneCellAnchor with a pixel offset, laying them out _PERROW across and
    wrapping down. Returns the number of grid rows used (for row-height sizing).
    """
    n = 0
    for idx, b64 in enumerate([t for t in thumbs if t]):
        try:
            img = XLImage(io.BytesIO(base64.b64decode(b64)))
            w, h = img.width, img.height
            scale = min(_CELL / w, _CELL / h, 1) if w and h else 1
            iw, ih = max(1, int(w * scale)), max(1, int(h * scale))
            gx, gy = idx % _PERROW, idx // _PERROW
            # centre each thumbnail within its box
            xoff = gx * (_CELL + _GAP) + (_CELL - iw) // 2
            yoff = gy * (_CELL + _GAP) + (_CELL - ih) // 2
            marker = AnchorMarker(col=col - 1, colOff=pixels_to_EMU(xoff),
                                  row=row - 1, rowOff=pixels_to_EMU(yoff))
            img.anchor = OneCellAnchor(
                _from=marker,
                ext=XDRPositiveSize2D(pixels_to_EMU(iw), pixels_to_EMU(ih)))
            ws.add_image(img)
            n = idx + 1
        except Exception:
            pass
    return math.ceil(n / _PERROW) if n else 0
