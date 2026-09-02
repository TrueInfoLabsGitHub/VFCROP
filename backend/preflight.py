"""Stage 0 — pre-flight quality gate.

Rejects obviously unusable submissions BEFORE the engine runs, so a bad batch
of photos costs milliseconds instead of a full eleven-call run, and never
lands in the run store or the Excel history.

Design constraints, in order:

1. PERMISSIVE BY CONSTRUCTION. This gate rejects deterministic garbage only —
   unreadable files, thumbnails, solid black frames, motion blur so heavy no
   rubric could run. A borderline photo goes THROUGH to the engine, which
   measures properly; the gate must never become a second, cruder judge of
   authenticity. Anything here that starts rejecting genuine-but-clumsy
   photography is a bug (see GROUP_FLOOR_FACTOR history in scoring.py for how
   that ends).

2. REJECTIONS ARE LOGGED, NEVER INVISIBLE. A submitter who repeatedly uploads
   unusable photos is itself a signal (deliberate evasion of the label shot is
   how counterfeits try to dodge conviction). One JSONL row per rejection —
   case id, timestamp, reasons; no image bytes — keeps the pattern visible
   without the storage cost this gate exists to save.

3. SAME VOCABULARY AS THE LADDER. The guidance a rejected submitter gets is
   scoring.RECAPTURE_SHOTS — the identical shot list an Insufficient Evidence
   verdict would have produced 40 seconds later.

Tier 2 (a one-call vision triage asking "is a care label visible and legible?")
hangs off `triage()` below, disabled until wired: the deterministic tier ships
first because it is free and cannot hallucinate.
"""
from __future__ import annotations

import base64
import io
import json
import os
import time

from PIL import Image, ImageFilter, ImageStat

from scoring import RECAPTURE_SHOTS

# Every threshold in one place, same convention as SCORING_CONSTANTS: these are
# UNFITTED DEFAULTS chosen to reject only unambiguous garbage. Fit them against
# the rejection log once it has volume; do not tune them by eye.
PREFLIGHT_CONSTANTS = {
    "MIN_PHOTOS": 1,          # the run gate in the UI already requires >=1
    "MIN_SIDE_PX": 320,       # the client downscales to max 1024; below 320 no rubric resolves
    "MAX_BYTES_MB": 12,       # refuse to decode a bomb
    # Variance of a 3x3 Laplacian over the grayscale image, downscaled to 512.
    # Sharp phone photos land in the hundreds-to-thousands; heavy defocus/motion
    # blur collapses under ~20; a solid frame is ~0.
    "BLUR_FLOOR": 22.0,
    # Mean luminance extremes: a frame this dark or this blown has no texture
    # left for any dimension agent to read.
    "LUMA_DARK": 16,
    "LUMA_BRIGHT": 246,
}

ENABLED = os.environ.get("PREFLIGHT", "1").strip().lower() in ("1", "true", "yes", "on")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REJECTION_LOG = os.environ.get(
    "PREFLIGHT_LOG", os.path.join(_ROOT, "data", "preflight_rejections.jsonl"))

_LAPLACIAN = ImageFilter.Kernel((3, 3), (0, 1, 0, 1, -4, 1, 0, 1, 0), scale=1, offset=0)


def _decode(b64: str):
    raw = base64.b64decode(b64, validate=False)
    if len(raw) > PREFLIGHT_CONSTANTS["MAX_BYTES_MB"] * 1024 * 1024:
        raise ValueError("file too large")
    im = Image.open(io.BytesIO(raw))
    im.load()
    return im


def _sharpness(gray: Image.Image) -> float:
    """Laplacian variance on a bounded-size copy. Two implementation facts the
    BLUR_FLOOR is calibrated against: the Pillow kernel clips negative
    responses to zero (halving what a signed Laplacian would report), and it
    leaves a 1px ring of border garbage large enough to dominate the variance
    of a genuinely blurred frame — so the border is cropped before measuring."""
    g = gray.copy()
    g.thumbnail((512, 512))
    f = g.filter(_LAPLACIAN)
    w, h = f.size
    if w > 6 and h > 6:
        f = f.crop((2, 2, w - 2, h - 2))
    return ImageStat.Stat(f).var[0]


def check_image(b64: str) -> str | None:
    """One image -> None if usable, else a human-readable reason."""
    try:
        im = _decode(b64)
    except Exception:
        return "not a readable image file"
    if min(im.size) < PREFLIGHT_CONSTANTS["MIN_SIDE_PX"]:
        return f"resolution too low ({im.size[0]}x{im.size[1]} — min side {PREFLIGHT_CONSTANTS['MIN_SIDE_PX']}px)"
    gray = im.convert("L")
    luma = ImageStat.Stat(gray).mean[0]
    if luma <= PREFLIGHT_CONSTANTS["LUMA_DARK"]:
        return "image is almost entirely dark — no detail is recoverable"
    if luma >= PREFLIGHT_CONSTANTS["LUMA_BRIGHT"]:
        return "image is blown out — no detail is recoverable"
    if _sharpness(gray) < PREFLIGHT_CONSTANTS["BLUR_FLOOR"]:
        return "too blurred for any inspection — retake in focus"
    return None


def check(images_b64: list[str]) -> dict:
    """The gate. Returns {ok, problems:[{image, issue}], checked, guidance}.

    `image` is the 1-based position in the submission, so the message maps to
    what the submitter sees in their upload row.
    """
    images = [b for b in (images_b64 or []) if b]
    problems = []
    if len(images) < PREFLIGHT_CONSTANTS["MIN_PHOTOS"]:
        problems.append({"image": 0, "issue": "no suspect photos were provided"})
    usable = 0
    for i, b64 in enumerate(images, start=1):
        issue = check_image(b64)
        if issue:
            problems.append({"image": i, "issue": issue})
        else:
            usable += 1
    # The gate fails when NOTHING usable survives, or when any file is garbage.
    # A mixed batch (one blurred shot among sharp ones) still fails loudly —
    # silently dropping the bad frame would hide exactly the "label shot is
    # always the blurry one" pattern the rejection log exists to expose.
    ok = usable >= PREFLIGHT_CONSTANTS["MIN_PHOTOS"] and not problems
    return {
        "ok": ok,
        "problems": problems,
        "checked": len(images),
        "usable": usable,
        # the same shots an Insufficient Evidence verdict would request
        "guidance": [f"{k}: {v}" for k, v in RECAPTURE_SHOTS.items()] if not ok else [],
    }


def log_rejection(case_id: str, result: dict) -> None:
    """One small JSONL row per rejection — no image bytes. This is the paper
    trail that keeps deliberate low-quality submission visible (a submitter's
    third rejected batch is a signal, not an accident)."""
    try:
        os.makedirs(os.path.dirname(REJECTION_LOG), exist_ok=True)
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "case_id": case_id or "",
               "checked": result.get("checked", 0),
               "problems": result.get("problems", [])}
        with open(REJECTION_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass                       # the log must never break the request path


def triage(images_b64: list[str]) -> dict | None:
    """Tier 2 (not yet wired): one cheap low-detail vision call answering only
    'which of [label, logo, hardware, stitching, material, UPC] are visibly
    present and legible?'. Gate = label legible + >=2 other dimensions visible,
    mirroring scoring.evidence_gate up front. Returns None while disabled so
    callers can `if (t := triage(imgs)) and not t['ok']:` safely either way."""
    if os.environ.get("PREFLIGHT_TRIAGE", "0").strip().lower() not in ("1", "true", "yes", "on"):
        return None
    return None  # TODO: wire to providers once a triage prompt is calibrated
