"""Excel export of analysis runs.

Builds a real .xlsx workbook (openpyxl) from the saved run history, one row per
analysis, with every detail as a column AND the suspect / UPC photos embedded as
thumbnails in the row. Thumbnails are produced with Pillow at save time so the
stored history stays small.
"""
import base64
import io

from openpyxl import Workbook
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

try:
    from PIL import Image as PILImage
    _PIL = True
except Exception:                       # pragma: no cover
    _PIL = False

DIMS = ["Logo", "Stitching", "Hardware", "Label", "Material"]
_THUMB_PX = 150
_IMG_DISPLAY = 96                        # px, how big the embedded image renders

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
_COLUMNS = [
    ("#", 5), ("Timestamp (UTC)", 20), ("Case", 15), ("Brand", 8),
    ("Engine", 15), ("Product", 22), ("Verdict", 21), ("Score", 7),
    ("Suspect", 16), ("UPC image", 16),
    *[(f"{d}\nscore", 9) for d in DIMS],
    ("UPC status", 12), ("UPC read", 16), ("UPC expected", 14),
    ("Verifier", 11), ("Confidence", 11),
    ("Tokens", 10), ("Cost ($)", 11), ("Latency (s)", 11),
    *[(f"{d} finding", 46) for d in DIMS],
]
_SUSPECT_COL = 9
_UPC_COL = 10


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
            _fnum(rec.get("score")), "", "",
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

        ws.row_dimensions[r].height = 76
        _embed(ws, rec.get("suspect_thumb"), _SUSPECT_COL, r)
        _embed(ws, rec.get("upc_thumb"), _UPC_COL, r)

    if not runs:
        ws.cell(row=2, column=1, value="No analyses saved yet.")

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _embed(ws, b64, col, row):
    if not b64:
        return
    try:
        img = XLImage(io.BytesIO(base64.b64decode(b64)))
        # fit inside the display box while preserving aspect ratio
        w, h = img.width, img.height
        scale = min(_IMG_DISPLAY / w, _IMG_DISPLAY / h, 1) if w and h else 1
        img.width, img.height = int(w * scale), int(h * scale)
        ws.add_image(img, f"{get_column_letter(col)}{row}")
    except Exception:
        pass
