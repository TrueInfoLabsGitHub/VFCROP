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

    _build_comparison_sheet(wb, runs)
    _build_scorecard_sheet(wb, runs)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Model-comparison + scorecard sheets. These read the same run records but
# present them for cross-engine comparison instead of a flat per-run log.
# ---------------------------------------------------------------------------
_BAND_LABEL = {"authentic": "Authentic", "caution": "Inconclusive", "counterfeit": "Counterfeit"}
_HEAD_FILL = PatternFill("solid", fgColor="1A2B3C")
_GROUP_FILL = PatternFill("solid", fgColor="2E4A63")
_BEST_FILL = PatternFill("solid", fgColor="E4F3E8")       # soft green for best-in-column
_HEAD_FONT = Font(color="FFFFFF", bold=True, size=10)
_CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _avg(xs, nd=1):
    xs = [x for x in xs if isinstance(x, (int, float))]
    if not xs:
        return None
    return round(sum(xs) / len(xs), nd)


def _engine_key(name):
    """Collapse engine labels that differ only by casing/whitespace (e.g.
    'GPT-5.5' vs 'gpt-5.5') so they aggregate as one engine."""
    return (name or "").strip().lower()


def _build_comparison_sheet(wb, runs):
    """One row per case, engines side by side: verdict, composite score and the
    five forensic-dimension scores per engine, plus a score-spread column that
    flags where the engines disagree. Only cases actually run on 2+ engines
    appear here (single-engine runs stay on the raw log)."""
    ws = wb.create_sheet("Model comparison")

    # group by case; keep the latest record per engine (dedupes double-saves)
    cases = {}
    for idx, rec in enumerate(runs):
        cid = rec.get("case_id") or ""
        if not cid:
            continue
        g = cases.setdefault(cid, {"order": idx, "cid": cid, "product": "", "engines": {}})
        g["engines"][_engine_key(rec.get("engine"))] = rec
        g["product"] = rec.get("product", "") or g["product"]
    compared = sorted((c for c in cases.values() if len(c["engines"]) >= 2),
                      key=lambda c: c["order"])

    if not compared:
        ws.cell(row=1, column=1,
                value="No multi-engine comparisons yet - run Compare (2+ engines) and save.")
        return

    # fixed engine columns: order by first appearance across compared cases
    engines = []                                    # list of (key, display label)
    seen = set()
    for c in compared:
        for k, rec in c["engines"].items():
            if k not in seen:
                seen.add(k)
                engines.append((k, (rec.get("engine") or k)))

    metrics = ["Verdict", "Score"] + DIMS           # 7 columns per engine
    # header row 1: grouped engine bands; header row 2: metric names
    ws.cell(row=1, column=1, value="Case").fill = _HEAD_FILL
    ws.cell(row=1, column=2, value="Product").fill = _HEAD_FILL
    ws.merge_cells(start_row=1, start_column=1, end_row=2, end_column=1)
    ws.merge_cells(start_row=1, start_column=2, end_row=2, end_column=2)
    col = 3
    for _k, label in engines:
        ws.merge_cells(start_row=1, start_column=col, end_row=1, end_column=col + len(metrics) - 1)
        gc = ws.cell(row=1, column=col, value=label)
        gc.fill = _GROUP_FILL
        gc.font = _HEAD_FONT
        gc.alignment = _CENTER
        for j, m in enumerate(metrics):
            mc = ws.cell(row=2, column=col + j, value=m)
            mc.fill = _HEAD_FILL
            mc.font = _HEAD_FONT
            mc.alignment = _CENTER
        col += len(metrics)
    spread_col = col
    for cell in (ws.cell(row=1, column=spread_col, value="Score\nspread"),):
        cell.fill = _HEAD_FILL
        cell.font = _HEAD_FONT
        cell.alignment = _CENTER
    ws.merge_cells(start_row=1, start_column=spread_col, end_row=2, end_column=spread_col)

    for cc in (1, 2):
        ws.cell(row=1, column=cc).font = _HEAD_FONT
        ws.cell(row=1, column=cc).alignment = _CENTER
    ws.column_dimensions["A"].width = 16
    ws.column_dimensions["B"].width = 24
    ws.freeze_panes = "C3"

    top = Alignment(vertical="top", wrap_text=True)
    r = 3
    for c in compared:
        ws.cell(row=r, column=1, value=c["cid"]).alignment = top
        ws.cell(row=r, column=2, value=c["product"]).alignment = top
        col = 3
        scores = []
        for k, _label in engines:
            rec = c["engines"].get(k)
            if rec:
                band = rec.get("band") or ""
                sc = _fnum(rec.get("score"))
                if isinstance(sc, (int, float)):
                    scores.append(sc)
                dims = rec.get("dimensions") or {}
                vals = [rec.get("verdict", ""), sc,
                        *[_fnum((dims.get(d) or {}).get("score")) for d in DIMS]]
                for j, v in enumerate(vals):
                    cell = ws.cell(row=r, column=col + j, value=v)
                    cell.alignment = top
                    if j <= 1 and band in _BAND_FONT:      # colour verdict + score
                        cell.font = Font(color=_BAND_FONT[band], bold=True)
            col += len(metrics)
        if len(scores) >= 2:
            spread = max(scores) - min(scores)
            sc = ws.cell(row=r, column=spread_col, value=int(round(spread)))
            sc.alignment = _CENTER
            if spread >= 25:                                # engines disagree a lot
                sc.font = Font(color="C0392B", bold=True)
        r += 1

    # width for each metric column
    for i in range(len(engines) * len(metrics)):
        ws.column_dimensions[get_column_letter(3 + i)].width = 11
    ws.column_dimensions[get_column_letter(spread_col)].width = 9


def _build_scorecard_sheet(wb, runs):
    """One row per engine, aggregated across every saved run. Each metric is its
    own column (no blended ranking); lowest avg cost and latency are highlighted
    since 'cheaper/faster' is unambiguous."""
    ws = wb.create_sheet("Engine scorecard")

    agg = {}
    for rec in runs:
        k = _engine_key(rec.get("engine"))
        if not k:
            continue
        a = agg.setdefault(k, {"label": "", "n": 0, "score": [], "conf": [], "cost": [],
                               "lat": [], "band": {"authentic": 0, "caution": 0, "counterfeit": 0},
                               "confirmed": 0, "verifier_n": 0, "dims": {d: [] for d in DIMS},
                               "order": len(agg)})
        a["label"] = (rec.get("engine") or a["label"])
        a["n"] += 1
        a["score"].append(_num(rec.get("score")))
        a["conf"].append(_num(rec.get("confidence")))
        a["cost"].append(_num(rec.get("cost")))
        lat = _num(rec.get("latency_ms"))
        a["lat"].append(lat / 1000 if lat is not None else None)
        band = rec.get("band")
        if band in a["band"]:
            a["band"][band] += 1
        verifier = rec.get("verifier")
        if verifier in ("confirmed", "refuted"):
            a["verifier_n"] += 1
            if verifier == "confirmed":
                a["confirmed"] += 1
        dims = rec.get("dimensions") or {}
        for d in DIMS:
            a["dims"][d].append(_num((dims.get(d) or {}).get("score")))

    engines = sorted(agg.values(), key=lambda a: a["order"])
    cols = ["Engine", "Runs", "Avg score", "% Authentic", "% Inconclusive",
            "% Counterfeit", "Verifier confirmed %", "Avg confidence",
            *[f"Avg {d}" for d in DIMS], "Avg cost ($)", "Avg latency (s)"]
    for ci, label in enumerate(cols, start=1):
        c = ws.cell(row=1, column=ci, value=label)
        c.fill = _HEAD_FILL
        c.font = _HEAD_FONT
        c.alignment = _CENTER
    ws.row_dimensions[1].height = 30
    ws.freeze_panes = "B2"

    if not engines:
        ws.cell(row=2, column=1, value="No analyses saved yet.")
        return

    def pct(part, whole):
        return round(100 * part / whole) if whole else None

    rows = []
    for a in engines:
        n = a["n"]
        rows.append([
            a["label"], n, _avg(a["score"], 1),
            pct(a["band"]["authentic"], n), pct(a["band"]["caution"], n),
            pct(a["band"]["counterfeit"], n),
            pct(a["confirmed"], a["verifier_n"]), _avg(a["conf"], 2),
            *[_avg(a["dims"][d], 1) for d in DIMS],
            _avg(a["cost"], 4), _avg(a["lat"], 1),
        ])
    for ri, row in enumerate(rows, start=2):
        for ci, v in enumerate(row, start=1):
            cell = ws.cell(row=ri, column=ci, value=v)
            cell.alignment = _CENTER if ci > 1 else Alignment(vertical="center")

    # highlight the lowest avg cost and lowest avg latency (unambiguously better)
    for col_idx in (len(cols) - 1, len(cols)):     # Avg cost, Avg latency
        vals = [(ri, ws.cell(row=ri, column=col_idx).value)
                for ri in range(2, 2 + len(rows))]
        vals = [(ri, v) for ri, v in vals if isinstance(v, (int, float))]
        if vals:
            best_ri = min(vals, key=lambda t: t[1])[0]
            ws.cell(row=best_ri, column=col_idx).fill = _BEST_FILL
            ws.cell(row=best_ri, column=col_idx).font = Font(bold=True, color="1E8A4C")

    ws.column_dimensions["A"].width = 16
    for ci in range(2, len(cols) + 1):
        ws.column_dimensions[get_column_letter(ci)].width = 13


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
