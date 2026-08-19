"""Tests for the crop pipeline — the change that raises the measurement rate."""
import base64
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import regions                                                   # noqa: E402

PIL = pytest.importorskip("PIL")
from PIL import Image                                            # noqa: E402


def _img_b64(w=2000, h=1500, patch=None):
    """A plain frame, optionally with a bright patch to identify a crop by."""
    im = Image.new("RGB", (w, h), (30, 40, 60))
    if patch:
        x0, y0, x1, y1 = patch
        im.paste((240, 20, 20), (int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h)))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=92)
    return base64.b64encode(buf.getvalue()).decode()


def _size(b64):
    return Image.open(io.BytesIO(base64.b64decode(b64))).size


# ---- boxes ----------------------------------------------------------------
def test_a_reversed_box_is_repaired_not_rejected():
    """Models return corners in whatever order they please."""
    assert regions.normalise_box([0.6, 0.2, 0.1, 0.05]) == (0.1, 0.05, 0.6, 0.2)


def test_a_degenerate_box_is_rejected():
    assert regions.normalise_box([0.2, 0.2, 0.2, 0.2]) is None
    assert regions.normalise_box([0, 0, 0, 0]) is None
    assert regions.normalise_box(None) is None
    assert regions.normalise_box([0.1, 0.2]) is None


def test_a_box_outside_the_frame_is_clamped():
    assert regions.normalise_box([-0.5, -1, 1.4, 2]) == (0.0, 0.0, 1.0, 1.0)


# ---- crops ----------------------------------------------------------------
def test_a_small_region_is_enlarged_to_the_legibility_floor():
    """The whole point. A care tag occupying a fifth of the frame still arrives
    at the model small once the full frame is downsampled to ~1024px; cropped
    from the original pixels it arrives at the floor the rubrics need."""
    frame = _img_b64(2000, 1500)
    out, note = regions.crop_b64(frame, [0.40, 0.40, 0.62, 0.62])
    assert out is not None, note
    assert min(_size(out)) >= regions.MIN_CROP_PX


def test_a_tiny_region_is_enlarged_as_far_as_is_honest():
    """A zip pull at 6% of the frame cannot reach the floor without going past
    the 4x cap, so it goes as far as the cap allows and no further — still an
    order of magnitude more pixels than the downsampled full frame would give."""
    frame = _img_b64(2000, 1500)
    out, note = regions.crop_b64(frame, [0.45, 0.45, 0.51, 0.51])
    assert out is not None, note
    source_px = int(0.06 * 1500)                      # ~90px before padding
    assert min(_size(out)) > source_px * 3


def test_a_crop_actually_contains_the_region_asked_for():
    frame = _img_b64(1200, 1200, patch=(0.70, 0.10, 0.85, 0.25))
    out, _ = regions.crop_b64(frame, [0.70, 0.10, 0.85, 0.25])
    im = Image.open(io.BytesIO(base64.b64decode(out))).convert("RGB")
    w, h = im.size
    r, g, b = im.getpixel((w // 2, h // 2))
    assert r > 180 and g < 90, "the crop did not land on the region"


def test_a_region_too_small_to_enlarge_honestly_is_refused():
    """Enlarging 20px to 768 does not create detail, it creates a blur — and a
    soft edge scores as deviation. Better to report it unresolvable."""
    frame = _img_b64(400, 400)
    out, why = regions.crop_b64(frame, [0.50, 0.50, 0.512, 0.512])
    assert out is None
    assert "too small" in why


def test_enlargement_is_capped():
    """Never past 4x, or the enlargement is smoother than the subject."""
    frame = _img_b64(1000, 1000)
    out, _ = regions.crop_b64(frame, [0.5, 0.5, 0.56, 0.56])   # ~60px source
    assert out is not None
    assert min(_size(out)) <= 60 * 4 * 1.35 + 2                # 4x plus the pad


def test_crop_survives_a_corrupt_image_without_raising():
    out, why = regions.crop_b64("not-base64-at-all", [0.1, 0.1, 0.5, 0.5])
    assert out is None and why


# ---- what a dimension receives -------------------------------------------
def _located(**over):
    base = {r: {"found": False, "legible": False, "box": None, "photo_index": 0, "note": ""}
            for r in regions.REGIONS}
    base.update(over)
    return base


def test_the_crop_leads_and_the_frames_follow():
    frames = [_img_b64(), _img_b64(), _img_b64()]
    loc = _located(logo={"found": True, "legible": True, "box": [0.4, 0.4, 0.6, 0.6],
                         "photo_index": 1, "note": ""})
    imgs, meta = regions.images_for("Logo", loc, frames)
    assert meta["cropped"] is True
    assert imgs[0] not in frames               # a crop, not one of the originals
    assert imgs[1:] == frames[:2]              # context frames, unchanged


def test_a_missing_region_falls_back_to_the_whole_frames():
    """A locator failure must cost nothing that was not already being lost."""
    frames = [_img_b64()]
    imgs, meta = regions.images_for("Hardware", _located(), frames)
    assert imgs == frames
    assert meta["cropped"] is False


def test_a_dimension_with_no_region_mapping_is_unaffected():
    frames = [_img_b64()]
    imgs, meta = regions.images_for("Overall", _located(), frames)
    assert imgs == frames and meta["cropped"] is False


def test_an_out_of_range_photo_index_does_not_explode():
    frames = [_img_b64()]
    loc = _located(logo={"found": True, "legible": True, "box": [0.3, 0.3, 0.7, 0.7],
                         "photo_index": 9, "note": ""})
    imgs, meta = regions.images_for("Logo", loc, frames)
    assert meta["cropped"] is True and len(imgs) >= 1


# ---- the intake gate ------------------------------------------------------
def test_the_gate_passes_when_the_tag_and_the_logo_are_both_legible():
    loc = _located(
        care_label={"found": True, "legible": True, "box": [0.1, 0.1, 0.3, 0.3],
                    "photo_index": 0, "note": ""},
        logo={"found": True, "legible": True, "box": [0.4, 0.4, 0.6, 0.6],
              "photo_index": 0, "note": ""})
    ok, missing = regions.capture_gate(loc)
    assert ok is True and missing == []


def test_present_but_blurred_fails_the_gate_the_same_as_absent():
    """'I can see there is a tag' is not 'I can read the tag'. Treating the two
    the same is how a run spent five vision calls to produce five estimates."""
    loc = _located(
        care_label={"found": True, "legible": False, "box": [0.1, 0.1, 0.3, 0.3],
                    "photo_index": 0, "note": "tag is out of focus"},
        logo={"found": True, "legible": True, "box": [0.4, 0.4, 0.6, 0.6],
              "photo_index": 0, "note": ""})
    ok, missing = regions.capture_gate(loc)
    assert ok is False
    assert missing[0][0] == "care_label"
    assert "focus" in missing[0][1]


def test_the_gate_names_every_missing_region_not_just_the_first():
    ok, missing = regions.capture_gate(_located())
    assert ok is False
    assert {m[0] for m in missing} == set(regions.REQUIRED_REGIONS)


def test_the_locator_degrades_to_nothing_located_without_a_config():
    loc, usage = regions.locate(None, [_img_b64()])
    assert usage is None
    assert all(not v["found"] for v in loc.values())


def test_the_locator_survives_a_provider_error():
    def boom(*a, **k):
        raise RuntimeError("429 Too Many Requests")
    loc, usage = regions.locate({"label": "x"}, [_img_b64()], chat=boom)
    assert all(not v["found"] for v in loc.values())
    assert "429" in usage["error"]


def test_the_locator_parses_a_normal_response():
    def fake(cfg, content, schema, name, timeout):
        return {"regions": [
            {"region": "logo", "photo_index": 0, "box": [0.4, 0.4, 0.5, 0.5],
             "found": True, "legible": True, "note": "chest logo"},
            {"region": "hardware", "photo_index": 0, "box": [0, 0, 0, 0],
             "found": False, "legible": False, "note": "product has no hardware"},
        ]}, 10, 5
    loc, usage = regions.locate({"label": "x"}, [_img_b64()], chat=fake)
    assert loc["logo"]["found"] and loc["logo"]["legible"]
    assert loc["logo"]["box"] == (0.4, 0.4, 0.5, 0.5)
    assert loc["hardware"]["found"] is False
    assert loc["care_label"]["found"] is False        # absent from the response
    assert usage["tokens_in"] == 10


# ---- per-dimension skip ---------------------------------------------------
def test_a_dimension_whose_region_is_absent_is_skipped():
    """Not a saving for its own sake: before ALWAYS_SCORE was turned off, this
    call spent a full vision request to invent a number about something that
    was not in the photograph."""
    loc = _located(logo={"found": True, "legible": True, "box": [0.4, 0.4, 0.6, 0.6],
                         "photo_index": 0, "note": ""},
                   hardware={"found": False, "legible": False, "box": None,
                             "photo_index": 0, "note": "product has no hardware"})
    run_logo, _ = regions.worth_running("Logo", loc)
    run_hw, why = regions.worth_running("Hardware", loc)
    assert run_logo is True
    assert run_hw is False and "no hardware" in why


def test_a_region_that_is_soft_but_present_still_runs():
    """A soft region can still come back PARTIAL, and a partial is real
    evidence — it just cannot convict on its own."""
    loc = _located(logo={"found": True, "legible": False, "box": [0.4, 0.4, 0.6, 0.6],
                         "photo_index": 0, "note": "slightly out of focus"})
    assert regions.worth_running("Logo", loc)[0] is True


def test_an_illegible_care_tag_does_not_cancel_the_other_dimensions():
    """An unreadable tag means the item can never be CLEARED. It does not mean
    a visibly wrong logo should go unexamined."""
    loc = _located(care_label={"found": True, "legible": False, "box": [0.1, 0.1, 0.2, 0.2],
                              "photo_index": 0, "note": "blurred"},
                   logo={"found": True, "legible": True, "box": [0.4, 0.4, 0.6, 0.6],
                         "photo_index": 0, "note": ""})
    assert regions.capture_gate(loc)[0] is False       # cannot be cleared
    assert regions.worth_running("Logo", loc)[0] is True   # can still be convicted


def test_nothing_located_at_all_is_detectable():
    assert regions.anything_locatable(_located()) is False
    assert regions.anything_locatable(None) is False
    loc = _located(fabric={"found": True, "legible": True, "box": [0.1, 0.1, 0.4, 0.4],
                           "photo_index": 0, "note": ""})
    assert regions.anything_locatable(loc) is True


def test_a_failed_locator_runs_every_dimension():
    """Degrade to the old behaviour, never to a silent mass-abstention."""
    loc, _ = regions.locate({"label": "x"}, [_img_b64()],
                            chat=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    for dim in regions.REGION_FOR_DIMENSION:
        assert regions.worth_running(dim, loc)[0] is True
