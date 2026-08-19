"""Find the forensic regions in a submitted photo, then send each dimension
agent a crop of its own region instead of the whole garment.

THE PROBLEM THIS SOLVES. Across 249 archived runs only 18% of dimension cells
were real measurements. 85% of the rest failed for one stated reason —
INSUFFICIENT_CAPTURE, the photo does not show the detail. Per dimension the
measurement rate was Label 43%, Logo 29%, Material 11%, Stitching 4%,
Hardware 3%.

Those numbers are not mostly a photography problem. A 3000px photo of a jacket
contains a perfectly legible zip pull — but the whole frame is downsampled to
roughly 1024px on its longest side before the model ever sees it, and the pull
is then about forty pixels across. The evidence was in the file and got thrown
away in transit.

So: one cheap locator call per case returns a box for each region, and each
dimension agent is handed that box, cropped from the ORIGINAL pixels and
enlarged. The full frame still travels alongside for context, at low detail.

This is the change that moves the measurement rate, and therefore the change
that decides how often the engine can reach a verdict at all. No new
photographs are required from the submitter.
"""
from __future__ import annotations

import base64
import io
import time

try:
    from PIL import Image
    _PIL = True
except Exception:                                          # pragma: no cover
    _PIL = False
    # Say so loudly. Without Pillow every crop silently returns None and each
    # dimension falls back to the full frame — the engine keeps running and
    # quietly stops doing the one thing this module exists for. A measurement
    # rate that drops back to 18% with no error in the log is exactly the kind
    # of regression that took months to notice last time.
    print("[regions] WARNING: Pillow is not installed — region cropping is "
          "DISABLED and every dimension will run on full frames. "
          "pip install pillow")


# Which region each forensic dimension needs resolved. Stitching asks for a
# seam rather than "the stitching" because a named seam is a thing a locator can
# point at, and an unnamed property is not.
REGION_FOR_DIMENSION = {
    "Logo": "logo",
    "Label": "care_label",
    "Hardware": "hardware",
    "Stitching": "seam",
    "Material": "fabric",
}

REGIONS = tuple(REGION_FOR_DIMENSION.values())

# Shortest side a crop is enlarged to before sending. Below roughly this the
# application method — embroidery vs rubberised vs screen print, the single most
# diagnostic thing about a logo — stops being resolvable, and the dimension
# comes back PARTIAL no matter how good the original photograph was.
MIN_CROP_PX = 768

# Fraction of the box added on each side. A crop flush to the logo edge loses
# the surrounding fabric, and edge_thread_creep and offset_from_placket are both
# measured against what is immediately around the mark.
BOX_PAD = 0.12

# Enlarging a 20px box to 768 does not create detail, it creates a blur that
# reads as a soft edge — and a soft edge scores as deviation. Below this the
# crop is discarded and the region reported as not resolvable, which is the
# honest answer and routes to a recapture request.
MIN_SOURCE_PX = 48

LOCATOR_SCHEMA = {
    "type": "object",
    "properties": {
        "regions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "region": {"type": "string", "enum": list(REGIONS)},
                    "photo_index": {"type": "integer"},
                    "box": {"type": "array", "items": {"type": "number"},
                            "minItems": 4, "maxItems": 4},
                    "found": {"type": "boolean"},
                    "legible": {"type": "boolean"},
                    "note": {"type": "string"},
                },
                "required": ["region", "photo_index", "box", "found", "legible", "note"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["regions"],
    "additionalProperties": False,
}

LOCATOR_PROMPT = """You are locating regions in photographs of a garment. You are NOT judging
authenticity — do not comment on whether anything looks genuine.

For each region below, return the ONE photo and the ONE tightest box that shows it best.

  logo         the brand mark on the garment — embroidered, printed or applied
  care_label   the interior care/spec tag: fibre content, size, RN or style number
  hardware     a zip slider, pull, snap, rivet or buckle
  seam         a named construction seam — side seam, armhole, baffle or hem
  fabric       a clear area of the shell or knit surface, away from seams and print

box is [x0, y0, x1, y1] as fractions of that photo's width and height, 0-1,
with x0 < x1 and y0 < y1. photo_index is 0-based over the SUBMITTED photos.

found     = the region is present somewhere in the photos at all.
legible   = it is large and sharp enough that its FINE DETAIL could be examined —
            individual stitches, the text on a tag, the finish on a zip.
            A region can be found and not legible. Say so; that is useful.
            Do not set legible true out of helpfulness.

Return one entry for every region. If a region is absent set found false,
legible false and box [0,0,0,0]. A garment that genuinely has no hardware — a
T-shirt, a beanie — is found false with note "product has no hardware"."""


# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------
def _clamp01(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if v < 0 else (1.0 if v > 1 else v)


def normalise_box(box):
    """-> (x0, y0, x1, y1) inside the unit square, ordered, or None if degenerate."""
    if not box or len(box) != 4:
        return None
    x0, y0, x1, y1 = (_clamp01(v) for v in box)
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    if x1 - x0 <= 0 or y1 - y0 <= 0:
        return None
    return (x0, y0, x1, y1)


def _pad(box, pad=BOX_PAD):
    x0, y0, x1, y1 = box
    dx, dy = (x1 - x0) * pad, (y1 - y0) * pad
    return (_clamp01(x0 - dx), _clamp01(y0 - dy), _clamp01(x1 + dx), _clamp01(y1 + dy))


def crop_b64(image_b64, box, *, min_px=MIN_CROP_PX, pad=BOX_PAD):
    """Crop `box` out of `image_b64` and enlarge it. -> (b64, note) or (None, why).

    The crop comes out of the ORIGINAL pixels, which is the entire point: the
    detail is there until the full frame is downsampled for the API.
    """
    if not _PIL:
        return None, "Pillow not installed"
    nb = normalise_box(box)
    if nb is None:
        return None, "no usable box"
    try:
        im = Image.open(io.BytesIO(base64.b64decode(image_b64)))
        im = im.convert("RGB")
    except Exception as e:
        return None, f"could not read image: {e}"

    w, h = im.size
    x0, y0, x1, y1 = _pad(nb, pad)
    px = (int(x0 * w), int(y0 * h), max(int(x1 * w), int(x0 * w) + 1),
          max(int(y1 * h), int(y0 * h) + 1))
    crop = im.crop(px)
    cw, ch = crop.size
    if min(cw, ch) < MIN_SOURCE_PX:
        return None, (f"region is only {cw}x{ch}px in the original — too small to "
                      f"enlarge without inventing detail")

    if min(cw, ch) < min_px:
        scale = min_px / float(min(cw, ch))
        # Never past 4x. Beyond that the enlargement is smoother than the
        # subject and the softness itself starts scoring as deviation.
        scale = min(scale, 4.0)
        crop = crop.resize((max(1, int(cw * scale)), max(1, int(ch * scale))),
                           Image.LANCZOS)

    buf = io.BytesIO()
    crop.save(buf, format="JPEG", quality=92)
    return base64.b64encode(buf.getvalue()).decode(), f"{cw}x{ch} -> {crop.size[0]}x{crop.size[1]}"


# ---------------------------------------------------------------------------
# the locator call
# ---------------------------------------------------------------------------
def locate(cfg, suspect_b64s, chat=None, timeout=120):
    """One vision call. -> (located, usage).

    `located` is {region: {found, legible, box, photo_index, note}}. Never
    raises: a locator failure degrades to "nothing located", and every dimension
    then falls back to the full frames exactly as before this module existed.
    """
    t0 = time.time()
    empty = {r: {"found": False, "legible": False, "box": None,
                 "photo_index": 0, "note": "locator did not run"} for r in REGIONS}
    imgs = [b for b in (suspect_b64s or []) if b][:6]
    if not cfg or not imgs:
        return empty, None
    if chat is None:
        from providers import _chat as chat                    # lazy: circular import

    content = [{"type": "text", "text": LOCATOR_PROMPT}]
    for i, b in enumerate(imgs):
        content.append({"type": "text", "text": f"SUBMITTED photo {i} (index {i}):"})
        # 'high' here and not 'low'. The locator is cheap in output tokens and
        # its whole job is to see small things; a low-detail locator misses the
        # care tag, and then no amount of cropping downstream can recover it.
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b}", "detail": "high"}})
    try:
        parsed, tin, tout = chat(cfg, content, LOCATOR_SCHEMA, "locate_regions", timeout)
    except Exception as e:
        for r in empty:
            empty[r]["note"] = f"locator failed: {e}"
        return empty, {"agent": "Locate regions", "model": cfg.get("label", "?"),
                       "tokens_in": 0, "tokens_out": 0, "error": str(e)[:160],
                       "latency_ms": int((time.time() - t0) * 1000)}

    out = dict(empty)
    for row in (parsed.get("regions") or []):
        r = row.get("region")
        if r not in REGIONS:
            continue
        idx = row.get("photo_index") or 0
        out[r] = {
            "found": bool(row.get("found")),
            "legible": bool(row.get("legible")),
            "box": normalise_box(row.get("box")),
            "photo_index": idx if 0 <= idx < len(imgs) else 0,
            "note": (row.get("note") or "").strip(),
        }
    usage = {"agent": "Locate regions", "model": cfg.get("label", "?"),
             "tokens_in": tin, "tokens_out": tout,
             "latency_ms": int((time.time() - t0) * 1000)}
    return out, usage


# ---------------------------------------------------------------------------
# what a dimension agent actually receives
# ---------------------------------------------------------------------------
def images_for(dim, located, suspect_b64s, *, context_frames=2):
    """-> (images, meta). The dimension's own region first, then whole frames.

    The crop leads because it carries the evidence; the frames follow so the
    agent can still see where on the garment it is looking. When no crop could
    be made the frames are all there is, which is exactly the behaviour this
    module replaces — so a locator failure costs nothing that was not already
    being lost.
    """
    frames = [b for b in (suspect_b64s or []) if b][:6]
    region = REGION_FOR_DIMENSION.get(dim)
    meta = {"region": region, "cropped": False, "note": ""}
    if not region or not frames:
        return frames, meta

    info = (located or {}).get(region) or {}
    if not info.get("found") or not info.get("box"):
        meta["note"] = info.get("note") or "region not located"
        return frames, meta

    idx = min(info.get("photo_index", 0), len(frames) - 1)
    crop, note = crop_b64(frames[idx], info["box"])
    meta["note"] = note
    if not crop:
        return frames, meta

    meta["cropped"] = True
    meta["legible"] = bool(info.get("legible"))
    return [crop] + frames[:context_frames], meta


# ---------------------------------------------------------------------------
# the intake gate
# ---------------------------------------------------------------------------
# What a submission must show before it is worth spending five vision calls on.
# Deliberately short: these are the two regions where the tells actually live and
# the two whose absence cannot be worked around. Everything else can come back
# NOT_ASSESSABLE without wasting the run.
REQUIRED_REGIONS = ("care_label", "logo")


def capture_gate(located, *, required=REQUIRED_REGIONS):
    """(ok, missing) — is this submission worth analysing at all?

    Called BEFORE the dimension fan-out. A submission with no legible care tag
    and no legible logo cannot produce a clearance under the evidence gate and
    cannot produce a conviction anyone would act on, so running five agents over
    it buys nothing but cost and a row of estimates. Fail here, name the missing
    shots, and let the submitter fix it.
    """
    missing = []
    for r in required:
        info = (located or {}).get(r) or {}
        if not info.get("found"):
            missing.append((r, info.get("note") or "not present in the photos"))
        elif not info.get("legible"):
            missing.append((r, info.get("note") or "present but not sharp enough to examine"))
    return (not missing), missing


def worth_running(dim, located):
    """(run_it, why_not) — is a vision call on this dimension worth making?

    Skip ONLY when the locator says the region is not in the photographs at all.
    A region that is present but soft still runs: it can come back PARTIAL, and
    a partial is real evidence — it contributes to the composite, it just cannot
    convict on its own.

    Note what this deliberately does NOT do: it does not skip the whole run
    because the care tag is illegible. An unreadable tag means the item can
    never be CLEARED, but a visibly wrong logo can still convict it, and
    throwing that away to save one call would be trading a detection for a
    rounding error. Only a submission where NOTHING was located skips entirely.
    """
    region = REGION_FOR_DIMENSION.get(dim)
    if not region:
        return True, ""
    # The locator found nothing anywhere: it failed, it never ran, or it could
    # not read the submission. Its silence is not evidence of absence, so run
    # every dimension — the behaviour that predates this module. Checked before
    # the per-region lookup so a locator outage can never mass-abstain a run.
    if not anything_locatable(located):
        return True, ""
    info = (located or {}).get(region) or {}
    if info.get("found"):
        return True, ""
    return False, (info.get("note")
                   or f"the {region.replace('_', ' ')} is not visible in the submitted photos")


def anything_locatable(located):
    """True if the locator found ANY region. When it found none, the five
    dimension agents have nothing to look at and the run is pure spend."""
    return any((v or {}).get("found") for v in (located or {}).values())
