"""Case store (SOW Casemates) + enterprise adapter invariants."""
import base64
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest                                                    # noqa: E402
from PIL import Image                                            # noqa: E402

import enterprise                                                # noqa: E402
import intake                                                    # noqa: E402


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(intake, "INTAKE_PATH", str(tmp_path / "cases.jsonl"))
    monkeypatch.setattr(intake, "CASE_FILES_DIR", str(tmp_path / "files"))


def _img(size=(1200, 900)):
    buf = io.BytesIO()
    Image.new("RGB", size, (30, 40, 60)).save(buf, "JPEG")
    return base64.b64encode(buf.getvalue()).decode()


def test_create_full_generates_case_id_and_hashes():
    rec = intake.create_full({"brand": "TNF", "source_channel": "Customs Seizure",
                              "images": [{"name": "front.jpg", "b64": _img()}]})
    assert rec["case_id"].startswith("VF-")
    assert rec["status"] == "New" and rec["stage"] == 1
    img = rec["images"][0]
    assert len(img["sha256"]) == 64
    assert img["low_res"] is False
    assert intake.image_path(rec["case_id"], img["file"])


def test_low_res_flagged_below_1000px():
    rec = intake.create_full({"images": [{"name": "s.jpg", "b64": _img((640, 480))}]})
    assert rec["images"][0]["low_res"] is True


def test_oversize_image_rejected():
    big = base64.b64encode(b"x" * (26 * 1024 * 1024)).decode()
    with pytest.raises(ValueError):
        intake.create_full({"images": [{"name": "big.bin", "b64": big}]})


def test_patch_status_stage_and_override():
    cid = intake.create_full({})["case_id"]
    rec = intake.patch(cid, {"status": "In Review", "stage": 4, "score": 62,
                             "override": {"tab": "upc", "decision": "Confirmed Match",
                                          "notes": "verified on the physical tag"}})
    assert rec["status"] == "In Review" and rec["stage"] == 4 and rec["score"] == 62
    assert rec["analyzed_at"]
    assert rec["overrides"]["upc"]["decision"] == "Confirmed Match"


def test_patch_rejects_bad_status():
    cid = intake.create_full({})["case_id"]
    with pytest.raises(ValueError):
        intake.patch(cid, {"status": "Vibing"})


def test_close_stamps_closed_at_and_notes_attach():
    cid = intake.create_full({})["case_id"]
    intake.add_note(cid, "A. Morgan", "origin factory code partially legible")
    rec = intake.patch(cid, {"status": "Closed"})
    assert rec["closed_at"]
    assert rec["notes"][0]["author"] == "A. Morgan"


def test_traversal_blocked_on_image_path():
    assert intake.image_path("..", "x.jpg") is None
    assert intake.image_path("VF-1", "../secret") is None


def test_back_compat_create_and_mark_analyzed():
    intake.create("VF-2026-9001", brand="TNF", note="bot intake")
    intake.mark_analyzed("VF-2026-9001")
    rec = intake.get("VF-2026-9001")
    assert rec["status"] == "In Review" and rec["stage"] == 5 and rec["analyzed_at"]
    assert intake.list_cases("queued") == []


# ---- enterprise adapters ----------------------------------------------------
def test_pim_by_upc_and_style():
    hit = enterprise.pim_lookup(upc="193393578024")
    assert hit["matched"] and hit["brand"] == "TNF" and hit["msrp"] == 320.0
    via_style = enterprise.pim_lookup(style="NF0A3C8D")
    assert via_style["matched"] and via_style["upc"] == "193393578024"
    assert enterprise.pim_lookup(upc="000000000000")["matched"] is False


def test_supplier_registry_statuses():
    ok = enterprise.suppliers_query(factory="VN-HCM-014")
    assert ok["status"] == "authorized"
    ghost = enterprise.suppliers_query(factory="CN-DGG-207")
    assert ghost["status"] == "ghost_shift"
    miss = enterprise.suppliers_query(factory="ZZ-XXX-999")
    assert miss["status"] == "not_authorized"
    country_only = enterprise.suppliers_query(country="Vietnam")
    assert country_only["status"] == "authorized" and len(country_only["matches"]) == 2


def test_tms_lane_match():
    hit = enterprise.tms_lanes(origin="Vietnam", dest="United States")
    assert hit["match"] and hit["lanes"]
    miss = enterprise.tms_lanes(origin="Moldova", dest="Peru")
    assert miss["match"] is False


def test_extract_mock_path(monkeypatch):
    monkeypatch.setattr(enterprise, "OPENAI_API_KEY", "")
    monkeypatch.setattr(enterprise, "ALLOW_MOCK", True)
    out = enterprise.extract([_img()])
    assert out["mode"] == "mock" and out["brand"]["value"] == "TNF"
    with pytest.raises(ValueError):
        enterprise.extract([])
