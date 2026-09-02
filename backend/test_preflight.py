"""Pre-flight gate invariants.

The gate exists to reject deterministic garbage BEFORE a run is paid for —
and for nothing else. The tests hold both directions: obvious garbage is
rejected, and anything a rubric could plausibly read passes through to the
engine, which is the only competent judge.
"""
import base64
import io
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image, ImageFilter                               # noqa: E402

import preflight                                                 # noqa: E402


def _b64(im, fmt="JPEG", q=85):
    buf = io.BytesIO()
    im.convert("RGB").save(buf, fmt, quality=q)
    return base64.b64encode(buf.getvalue()).decode()


def _textured(size=(800, 800), seed=7):
    """A sharp, detailed frame — random blocks give plenty of edges."""
    rnd = random.Random(seed)
    im = Image.new("RGB", size)
    px = im.load()
    for y in range(size[1]):
        for x in range(size[0]):
            v = rnd.randrange(256)
            px[x, y] = (v, 255 - v, (v * 3) % 256)
    return im


def test_sharp_photo_passes():
    res = preflight.check([_b64(_textured())])
    assert res["ok"], res
    assert res["usable"] == 1


def test_no_photos_rejected():
    res = preflight.check([])
    assert not res["ok"]
    assert any("no suspect photos" in p["issue"] for p in res["problems"])


def test_garbage_bytes_rejected():
    res = preflight.check([base64.b64encode(b"not an image at all").decode()])
    assert not res["ok"]
    assert "not a readable image" in res["problems"][0]["issue"]


def test_thumbnail_rejected():
    res = preflight.check([_b64(_textured((120, 120)))])
    assert not res["ok"]
    assert "resolution too low" in res["problems"][0]["issue"]


def test_black_frame_rejected():
    res = preflight.check([_b64(Image.new("RGB", (800, 800), (2, 2, 2)))])
    assert not res["ok"]
    assert "dark" in res["problems"][0]["issue"]


def test_blown_frame_rejected():
    res = preflight.check([_b64(Image.new("RGB", (800, 800), (254, 254, 254)))])
    assert not res["ok"]
    assert "blown out" in res["problems"][0]["issue"]


def test_heavy_blur_rejected():
    im = _textured()
    for _ in range(6):
        im = im.filter(ImageFilter.GaussianBlur(12))
    res = preflight.check([_b64(im)])
    assert not res["ok"]
    assert "blurred" in res["problems"][0]["issue"]


def test_one_bad_frame_fails_loudly_not_silently():
    # A mixed batch is REJECTED with the bad frame named — never silently
    # trimmed, because "the label shot is always the blurry one" is exactly
    # the evasion pattern the rejection log exists to expose.
    good = _b64(_textured())
    bad = _b64(Image.new("RGB", (800, 800), (1, 1, 1)))
    res = preflight.check([good, bad])
    assert not res["ok"]
    assert res["usable"] == 1
    assert res["problems"][0]["image"] == 2


def test_rejection_produces_guidance_from_the_ladder():
    res = preflight.check([])
    assert res["guidance"], "a rejection must say what shots would fix it"
    assert any(g.startswith("Label:") for g in res["guidance"])


def test_rejection_log_row_is_small_and_imageless(tmp_path, monkeypatch):
    log = tmp_path / "rej.jsonl"
    monkeypatch.setattr(preflight, "REJECTION_LOG", str(log))
    res = preflight.check([])
    preflight.log_rejection("VF-2026-0001", res)
    row = json.loads(log.read_text(encoding="utf-8").strip())
    assert row["case_id"] == "VF-2026-0001"
    assert "problems" in row
    assert len(log.read_bytes()) < 2048, "a rejection row must stay tiny — no image bytes"
