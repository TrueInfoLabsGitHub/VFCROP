"""A failed run must leave a record.

Dropping failures makes "this engine failed" indistinguishable from "this engine
was never run" — the row simply disappears from the export with no trace, which
is what sent us hunting for missing rows.
"""
import io as _io
import os
import sys

import openpyxl
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app                                                       # noqa: E402
import exporter                                                  # noqa: E402


class Req:
    """Minimal stand-in for ExportSaveReq."""
    def __init__(self, data=None, error="", case_id="C1", brand="TNF",
                 engine="Kimi K2.6"):
        self.data = data or {}
        self.error = error
        self.case_id = case_id
        self.brand = brand
        self.engine = engine
        self.product = "Nuptse"
        self.product_id = ""
        self.suspect_image = None
        self.suspect_images = []
        self.reference_images = []
        self.upc_image = None


def test_compare_failure_is_recorded_not_dropped():
    rec = app._build_record(Req(data={"ok": False, "error": "exceeded the time budget",
                                      "case_id": "C1", "brand": "TNF"}))
    assert rec["band"] == "error"
    assert rec["verdict"] == "Run Failed"
    assert rec["error"] == "exceeded the time budget"
    assert rec["score"] is None


def test_single_engine_failure_is_recorded():
    """The single-engine path throws, so the payload carries only an error."""
    rec = app._build_record(Req(data={}, error="job expired — please retry"))
    assert rec["band"] == "error"
    assert rec["error"] == "job expired — please retry"


def test_failed_record_keeps_its_case_id():
    """Without this the row is orphaned into its own line instead of sitting
    beside the engines that succeeded on the same case."""
    rec = app._build_record(Req(data={"ok": False, "error": "boom"}, case_id="2026VFC1"))
    assert rec["case_id"] == "2026VFC1"
    assert rec["brand"] == "TNF"


def test_successful_record_is_untouched():
    rec = app._build_record(Req(data={
        "case_id": "C1", "brand": "TNF",
        "composite": {"score": 20, "band": "authentic", "verdict_label": "Likely Authentic",
                      "coverage": {"assessed": 4}},
        "dimensions": [{"dimension": "Logo", "score": 20, "finding": "f", "status": "scored"}],
        "upc": {"status": "not_provided"}, "verdict": {"verifier_confirmed": True},
        "report": {"totals": {}, "evals": {}}}))
    assert rec["band"] == "authentic"
    assert rec["verdict"] == "Likely Authentic"
    assert rec["error"] == ""


# ---- how it surfaces in the workbook ---------------------------------------
def _wb(runs):
    return openpyxl.load_workbook(_io.BytesIO(exporter.build_workbook(runs)))


def base(**over):
    r = {"case_id": "C1", "brand": "TNF", "engine": "gpt-5.5", "product": "Nuptse",
         "verdict": "Inconclusive", "score": 48, "band": "caution", "assessed": 1,
         "error": "", "dimensions": {d: {"score": 40, "finding": "f", "status": "estimated"}
                                     for d in exporter.DIMS},
         "upc": {"status": "not_provided", "extracted": "", "expected": ""},
         "verifier": "refuted", "confidence": 0.3, "cost": 0.7, "latency_ms": 90000,
         "suspect_thumbs": [], "reference_thumbs": [], "upc_thumb": ""}
    r.update(over)
    return r


FAILED = base(engine="Kimi K2.6", verdict="Run Failed", score=None, band="error",
              assessed=None, error="engine exceeded the time budget", dimensions={},
              confidence=None, cost=None, latency_ms=None)


def test_export_shows_the_failure_and_its_reason():
    ws = _wb([base(), FAILED])["VERITAS analyses"]
    assert ws.cell(3, 11).value == "Inconclusive"                    # engine 1
    assert ws.cell(3, 11 + 16).value == "Run Failed — engine exceeded the time budget"


def test_failed_engine_shares_the_row_with_the_successful_one():
    """Same case id -> one row, both engine blocks populated."""
    ws = _wb([base(), FAILED])["VERITAS analyses"]
    assert ws.cell(3, 2).value == "C1"
    assert ws.cell(4, 2).value in (None, "")       # no orphan second row


def test_scorecard_counts_failures_separately():
    """A failure must not be folded into any verdict rate."""
    sc = _wb([base(), FAILED])["Engine scorecard"]
    cols = [sc.cell(1, c).value for c in range(1, 13)]
    assert "% Failed" in cols
    i = cols.index("% Failed") + 1
    rows = {sc.cell(r, 1).value: r for r in (2, 3)}
    assert sc.cell(rows["Kimi K2.6"], i).value == 100
    assert sc.cell(rows["gpt-5.5"], i).value == 0
    # and the failure contributes to no verdict bucket
    for name in ("% Authentic", "% Inconclusive", "% Counterfeit"):
        j = cols.index(name) + 1
        assert sc.cell(rows["Kimi K2.6"], j).value == 0


def test_failed_run_is_excluded_from_score_averages():
    sc = _wb([base(), FAILED])["Engine scorecard"]
    rows = {sc.cell(r, 1).value: r for r in (2, 3)}
    assert sc.cell(rows["Kimi K2.6"], 3).value is None      # Avg score
    assert sc.cell(rows["gpt-5.5"], 3).value == 48


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
