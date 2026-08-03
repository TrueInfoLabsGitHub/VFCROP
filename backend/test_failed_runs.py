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


# ---- blocks must never be silently blank -----------------------------------
def test_an_unnamed_engine_block_is_labelled():
    """A record saved with no engine label used to create a nameless block. The
    data was there, off to the right, under a blank header — which reads as
    'nothing was saved'."""
    ws = _wb([base(engine="gpt-5.5"), base(case_id="C2", engine="")])["VERITAS analyses"]
    assert ws.cell(1, 11).value == "GPT-5.5"          # canonicalised for display
    assert ws.cell(1, 11 + 16).value == "(engine not recorded)"


def test_a_case_not_run_on_an_engine_says_so():
    """Distinguishes 'never attempted' from 'ran and vanished'."""
    ws = _wb([base(engine="gpt-5.5"), base(case_id="C2", engine="Kimi K2.6")])["VERITAS analyses"]
    assert ws.cell(3, 11).value == "Inconclusive"          # C1 on engine 1
    assert ws.cell(3, 11 + 16).value == "not run"          # C1 never ran on engine 2
    assert ws.cell(4, 11).value == "not run"               # C2 never ran on engine 1
    assert ws.cell(4, 11 + 16).value == "Inconclusive"


def test_failed_runs_are_not_relabelled_as_not_run():
    ws = _wb([base(engine="gpt-5.5"), FAILED])["VERITAS analyses"]
    assert ws.cell(3, 11 + 16).value.startswith("Run Failed")


# ---- one engine, one block -------------------------------------------------
def test_label_drift_collapses_into_a_single_block():
    """The bug that hid half the sheet: the same engine saved under different
    spellings created several column blocks, so scores existed but sat off to
    the right under a header the user never scrolled to."""
    runs = [base(case_id="C1", engine="gpt-5.5", score=48),
            base(case_id="C2", engine="GPT-5.5", score=17),
            base(case_id="C3", engine="GPT 5.5", score=20),
            base(case_id="C4", engine="openai", score=33)]
    ws = _wb(runs)["VERITAS analyses"]
    assert ws.cell(1, 11).value == "GPT-5.5"
    assert ws.cell(1, 27).value == "Score\nspread"          # no second engine block
    assert [ws.cell(r, 12).value for r in range(3, 7)] == [48, 17, 20, 33]


def test_distinct_engines_still_get_their_own_block():
    runs = [base(case_id="C1", engine="gpt-5.5"), base(case_id="C1", engine="Kimi K2.6")]
    ws = _wb(runs)["VERITAS analyses"]
    assert ws.cell(1, 11).value == "GPT-5.5"
    assert ws.cell(1, 27).value == "Kimi K2.6"


def test_engine_label_is_canonicalised_at_save_time():
    """The provider on the response is authoritative, so a missing or drifted
    client label cannot split the block."""
    assert app._canonical_engine("", {"provider": "openai"}) == "GPT-5.5"
    assert app._canonical_engine("  gpt-5.5 ", {}) == "GPT-5.5"
    assert app._canonical_engine("gpt-5.5", {"provider": "gemini"}) == "Gemini 3.1 Pro"
    assert app._canonical_engine("", {}) == "(engine not recorded)"


# ---- rate limits must not destroy a run ------------------------------------
def test_a_429_is_retried_not_raised():
    """The observed production failure: 'Client error 429 Too Many Requests' on
    one dimension killed the entire run. 429 was not in the retry set."""
    import inspect

    import providers
    src = inspect.getsource(providers._chat)
    assert "429" in src, "429 is not handled in the HTTP retry path"
    assert "retry-after" in src.lower(), "Retry-After is ignored"
    assert providers.RETRY_ATTEMPTS >= 2


def test_one_failed_dimension_does_not_kill_the_run():
    import graph

    def boom(*a, **k):
        raise RuntimeError("openai Material agent failed: 429 Too Many Requests")

    original = graph.run_dimension_agent
    graph.run_dimension_agent = boom
    try:
        out = graph.dimension_node("Material", {
            "pairing": {"status": "ok"}, "brand": "TNF", "case_id": "c",
            "suspect_images": ["x"], "references": {}, "fetched_refs": []})
    finally:
        graph.run_dimension_agent = original
    d = out["dimension_results"][0]
    assert d["status"] == "error"
    assert d["score"] is None
    assert "AGENT FAILED" in d["finding"]


def test_the_other_dimensions_still_produce_a_verdict():
    import graph

    def ok(name, score):
        return {"dimension": name, "score": score, "band": graph._band(score),
                "status": "scored", "confidence": 0.9, "finding": "f"}

    failed = {"dimension": "Material", "score": None, "band": "neutral",
              "status": "error", "confidence": 0.0, "finding": "AGENT FAILED — 429"}
    c = graph.aggregate_node({
        "dimension_results": [ok("Logo", 20), ok("Stitching", 20), ok("Hardware", 20),
                              ok("Label", 20), failed],
        "upc_result": {"status": "not_provided"}, "pairing": {"status": "ok"},
        "label_id": {"validation": {"hard_fail": False}}})["composite"]
    assert c["score"] == 20
    assert c["coverage"]["assessed"] == 4
    assert c["coverage"]["errored"] == ["Material"]


def test_export_marks_a_failed_dimension_distinctly():
    """'failed' must not read as 'n/a' (abstained) or as a blank."""
    r = base(dimensions={"Logo": {"score": 20, "finding": "f", "status": "scored"},
                         "Material": {"score": None, "finding": "AGENT FAILED",
                                      "status": "error"}})
    ws = _wb([r])["VERITAS analyses"]
    assert ws.cell(3, 14).value == 20          # Logo
    assert ws.cell(3, 18).value == "failed"    # Material
