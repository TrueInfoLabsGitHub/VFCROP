"""Tests for the run deletion tool.

The failure that matters here is deleting the wrong thing, so these pin the
selection logic: the '#' must match the workbook, and a selector must never
reach beyond what was asked for.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import delete_runs as dr                                          # noqa: E402


def run(case_id, engine="gpt-5.5", rid=None):
    return {"id": rid or f"{case_id}-{engine}", "case_id": case_id,
            "engine": engine, "product": f"{case_id} product"}


RUNS = [
    run("A"), run("A", "Gemini 3.1 Pro"),        # case #1, two engines
    run("B"),                                     # case #2
    run("test1"), run("test2"), run("test3"),     # cases #3, #4, #5
]


def test_numbering_matches_the_export_grouping():
    """One row per case, engines collapsed — same as the analyses sheet."""
    g = dr.group_cases(RUNS)
    assert [x["number"] for x in g] == [1, 2, 3, 4, 5]
    assert [x["cid"] for x in g] == ["A", "B", "test1", "test2", "test3"]
    assert len(g[0]["records"]) == 2               # case A ran on two engines


def test_select_by_case_id_is_case_insensitive():
    g = dr.group_cases(RUNS)
    picked = dr.select(g, case_ids=["TEST1", "test3"])
    assert [x["cid"] for x in picked] == ["test1", "test3"]


def test_select_from_number_takes_that_case_and_everything_after():
    g = dr.group_cases(RUNS)
    picked = dr.select(g, from_number=3)
    assert [x["cid"] for x in picked] == ["test1", "test2", "test3"]


def test_select_from_number_does_not_touch_earlier_cases():
    g = dr.group_cases(RUNS)
    picked = dr.select(g, from_number=5)
    assert [x["cid"] for x in picked] == ["test3"]


def test_select_by_explicit_numbers():
    g = dr.group_cases(RUNS)
    assert [x["cid"] for x in dr.select(g, numbers=[2, 4])] == ["B", "test2"]


def test_empty_selection_selects_nothing():
    """No selector must never mean 'everything'."""
    g = dr.group_cases(RUNS)
    assert dr.select(g) == []


def test_selection_carries_every_engine_run_for_a_case():
    """Deleting case A must remove both its engine runs, not just the first."""
    g = dr.group_cases(RUNS)
    picked = dr.select(g, case_ids=["A"])
    ids = [r["id"] for x in picked for r in x["records"]]
    assert len(ids) == 2 and set(ids) == {"A-gpt-5.5", "A-Gemini 3.1 Pro"}


def test_runs_without_a_case_id_stay_separate():
    runs = [run(""), run("")]
    g = dr.group_cases(runs)
    assert len(g) == 2                              # not collapsed together
