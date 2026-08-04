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


# Column positions shift whenever a metric is added or removed, so locate them by
# header instead of by index — a test that hardcodes "column 14" breaks for a
# reason that has nothing to do with what it is testing.
def col_of(ws, header, block=0):
    """1-based column of `header` inside engine block `block` (0 = first)."""
    hits = [c for c in range(1, ws.max_column + 1) if ws.cell(2, c).value == header]
    return hits[block]


def engine_label(ws, block=0):
    """The engine name in the merged header above a block."""
    return ws.cell(1, col_of(ws, "Verdict", block)).value


def verdict_at(ws, row, block=0):
    return ws.cell(row, col_of(ws, "Verdict", block)).value


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
    assert verdict_at(ws, 3, 0) == "Inconclusive"
    assert verdict_at(ws, 3, 1) == "Run Failed — engine exceeded the time budget"


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
    assert engine_label(ws, 0) == "GPT-5.5"          # canonicalised for display
    assert engine_label(ws, 1) == "(engine not recorded)"


def test_a_case_not_run_on_an_engine_says_so():
    """Distinguishes 'never attempted' from 'ran and vanished'."""
    ws = _wb([base(engine="gpt-5.5"), base(case_id="C2", engine="Kimi K2.6")])["VERITAS analyses"]
    assert verdict_at(ws, 3, 0) == "Inconclusive"          # C1 on engine 1
    assert verdict_at(ws, 3, 1) == "not run"               # C1 never ran on engine 2
    assert verdict_at(ws, 4, 0) == "not run"               # C2 never ran on engine 1
    assert verdict_at(ws, 4, 1) == "Inconclusive"


def test_failed_runs_are_not_relabelled_as_not_run():
    ws = _wb([base(engine="gpt-5.5"), FAILED])["VERITAS analyses"]
    assert verdict_at(ws, 3, 1).startswith("Run Failed")


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
    assert engine_label(ws, 0) == "GPT-5.5"
    assert len([c for c in range(1, ws.max_column + 1)
                if ws.cell(2, c).value == "Verdict"]) == 1   # no second engine block
    sc = col_of(ws, "Score", 0)
    assert [ws.cell(r, sc).value for r in range(3, 7)] == [48, 17, 20, 33]


def test_distinct_engines_still_get_their_own_block():
    runs = [base(case_id="C1", engine="gpt-5.5"), base(case_id="C1", engine="Kimi K2.6")]
    ws = _wb(runs)["VERITAS analyses"]
    assert engine_label(ws, 0) == "GPT-5.5"
    assert engine_label(ws, 1) == "Kimi K2.6"


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
    assert ws.cell(3, col_of(ws, "Logo")).value == 20
    assert ws.cell(3, col_of(ws, "Material")).value == "failed"


# ---- a rate limit late in the run must not erase the scores ----------------
def test_verdict_tier_failure_keeps_the_composite():
    """Observed: '#28 Run Failed — openai verdict (synthesize) failed: 429'.
    The composite is already computed when the verdict tier runs, so a failure
    there was discarding five completed dimension scores for the sake of prose."""
    import graph

    def boom(*a, **k):
        raise RuntimeError("openai verdict (synthesize) failed: 429 Too Many Requests")

    original = graph.run_verdict
    graph.run_verdict = boom
    try:
        d = lambda n, s: {"dimension": n, "score": s, "band": graph._band(s),
                          "status": "scored", "confidence": 0.9, "finding": f"{n} finding"}
        out = graph.verdict_node({
            "composite": {"score": 62, "band": "counterfeit",
                          "verdict_label": "Suspected Counterfeit"},
            "dimension_results": [d("Logo", 81), d("Label", 100)],
            "upc_result": {"status": "not_provided"}, "brand": "TNF", "provider": "openai"})
    finally:
        graph.run_verdict = original
    v = out["verdict"]
    assert v["label"] == "Suspected Counterfeit"      # the score survives
    assert v["degraded"] is True
    assert v["verifier_confirmed"] is False
    assert v["verifier_votes"] == "0/0"
    assert v["key_evidence"]                          # falls back to the findings


def test_upc_failure_does_not_kill_the_run():
    import graph

    def boom(*a, **k):
        raise RuntimeError("429 Too Many Requests")

    original = graph.run_upc_tool
    graph.run_upc_tool = boom
    try:
        out = graph.upc_node({"brand": "TNF", "case_id": "c", "upc_image": "x",
                              "provider": "openai"})
    finally:
        graph.run_upc_tool = original
    assert out["upc_result"]["status"] == "unreadable"


def test_calls_are_throttled_per_provider():
    """Prevent the 429 rather than recover from it."""
    import threading
    import time as _t

    import providers
    peak = cur = 0
    lock = threading.Lock()

    def work():
        nonlocal peak, cur
        with providers._limiter("https://test.example/v1"):
            with lock:
                cur += 1
                peak = max(peak, cur)
            _t.sleep(0.02)
            with lock:
                cur -= 1

    ts = [threading.Thread(target=work) for _ in range(12)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert peak <= providers.MAX_INFLIGHT
    assert providers._limiter("prov-a") is not providers._limiter("prov-b")


# ---- partial export --------------------------------------------------------
def _runs(n):
    return [base(case_id=f"C{i}", score=i) for i in range(1, n + 1)]


def test_export_defaults_to_everything():
    import exporter as e
    assert len(e.select_runs(_runs(6))) == 6


def test_export_range_is_inclusive():
    import exporter as e
    got = [r["case_id"] for r in e.select_runs(_runs(6), first=3, last=5)]
    assert got == ["C3", "C4", "C5"]


def test_export_open_ended_ranges():
    import exporter as e
    assert [r["case_id"] for r in e.select_runs(_runs(6), first=5)] == ["C5", "C6"]
    assert [r["case_id"] for r in e.select_runs(_runs(6), last=2)] == ["C1", "C2"]


def test_export_by_explicit_case_ids():
    import exporter as e
    got = [r["case_id"] for r in e.select_runs(_runs(6), cases=["c2", "C5"])]
    assert got == ["C2", "C5"]


def test_a_multi_engine_case_is_one_number_and_travels_together():
    """Case numbering must match the sheet's '#', where one case is one row
    however many engines ran it — and selecting it must take all its runs."""
    import exporter as e
    runs = [base(case_id="C1", engine="gpt-5.5"), base(case_id="C1", engine="Kimi K2.6"),
            base(case_id="C2", engine="gpt-5.5")]
    groups = e.group_cases(runs)
    assert [(g["number"], g["cid"], len(g["records"])) for g in groups] == [
        (1, "C1", 2), (2, "C2", 1)]
    assert len(e.select_runs(runs, first=1, last=1)) == 2


def test_partial_export_builds_a_valid_workbook():
    import exporter as e
    sel = e.select_runs(_runs(6), first=3, last=4)
    ws = openpyxl.load_workbook(_io.BytesIO(e.build_workbook(sel)))["VERITAS analyses"]
    assert [ws.cell(r, 2).value for r in (3, 4)] == ["C3", "C4"]
    assert ws.cell(5, 2).value in (None, "")        # nothing beyond the range
    assert [ws.cell(r, 1).value for r in (3, 4)] == [1, 2]   # renumbered from 1


def test_export_filename_describes_the_selection():
    import app as a
    assert a._export_name(None, None, []) == "VERITAS_analyses.xlsx"
    assert a._export_name(15, 30, []) == "VERITAS_analyses_15-30.xlsx"
    assert a._export_name(15, None, []) == "VERITAS_analyses_from_15.xlsx"
    assert a._export_name(None, None, ["a", "b"]) == "VERITAS_analyses_2_cases.xlsx"


# ---- the coverage column is gone -------------------------------------------
def test_no_coverage_column_in_any_sheet():
    """Removed on request: Score is the average of all five numbers, so a column
    counting only the measured ones read as contradicting the divisor."""
    wb = _wb([base()])
    an = wb["VERITAS analyses"]
    heads = {an.cell(2, c).value for c in range(1, an.max_column + 1)}
    assert "Assessed" not in heads and "Measured" not in heads
    mc_runs = [base(case_id="C1", engine="gpt-5.5"), base(case_id="C1", engine="Kimi K2.6")]
    mc = _wb(mc_runs)["Model comparison"]
    mheads = {mc.cell(2, c).value for c in range(1, mc.max_column + 1)}
    assert "Assessed" not in mheads and "Measured" not in mheads
    sc = wb["Engine scorecard"]
    sheads = {sc.cell(1, c).value for c in range(1, sc.max_column + 1)}
    assert "Avg assessed" not in sheads and "Avg measured" not in sheads


def test_engine_block_is_fifteen_columns():
    ws = _wb([base(case_id="C1", engine="gpt-5.5"), base(case_id="C1", engine="Kimi K2.6")])["VERITAS analyses"]
    assert col_of(ws, "Verdict", 1) - col_of(ws, "Verdict", 0) == 15


def test_verdict_keeps_its_band_colour():
    """The removal orphaned an amber-font line into the verdict/score branch,
    which turned every verdict cell amber regardless of its band."""
    for band, want in (("counterfeit", "C0392B"), ("authentic", "1E8A4C"), ("caution", "B07D0A")):
        ws = _wb([base(band=band)])["VERITAS analyses"]
        assert ws.cell(3, col_of(ws, "Verdict")).font.color.rgb.endswith(want), band


def test_estimated_cells_still_italicise_after_the_shift():
    r = base(dimensions={d: {"score": 40, "finding": "e", "status": "estimated"}
                         for d in exporter.DIMS})
    ws = _wb([r])["VERITAS analyses"]
    assert all(ws.cell(3, col_of(ws, d)).font.i for d in exporter.DIMS)
