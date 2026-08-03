"""Tests for the run backfill.

The risk with a backfill is silent damage: overwriting values that were already
good, or losing the record of how thin the original evidence was. These pin both.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import backfill_runs as bf                                        # noqa: E402


def rec(**over):
    base = {
        "id": "r1", "case_id": "test3", "brand": "TNF", "engine": "gpt-5.5",
        "score": None, "band": "insufficient", "verdict": "Insufficient Evidence",
        "created_at": "2026-07-31T10:00:00Z", "cost": 0.41, "latency_ms": 68700,
        "suspect_thumbs": ["aGk="],
        "dimensions": {
            "Logo": {"score": None, "finding": "INSUFFICIENT", "status": "abstain"},
            "Stitching": {"score": None, "finding": "INSUFFICIENT", "status": "abstain"},
            "Hardware": {"score": None, "finding": "INSUFFICIENT", "status": "abstain"},
            "Label": {"score": 36, "finding": "measured", "status": "scored"},
            "Material": {"score": None, "finding": "INSUFFICIENT", "status": "abstain"},
        },
    }
    base.update(over)
    return base


def fresh(**scores):
    return {k: {"dimension": k, "score": v, "finding": f"ESTIMATE ({v}/100)",
                "status": "estimated"} for k, v in scores.items()}


def test_finds_only_runs_with_gaps():
    assert bf.null_dims(rec()) == ["Logo", "Stitching", "Hardware", "Material"]
    full = rec(dimensions={d: {"score": 10, "status": "scored"} for d in bf.DIMS})
    assert bf.null_dims(full) == []
    assert bf.needs_backfill(rec()) and not bf.needs_backfill(full)


def test_engine_label_maps_to_provider():
    assert bf.provider_for("gpt-5.5") == "openai"
    assert bf.provider_for("Gemini 3.1 Pro") == "gemini"
    assert bf.provider_for("Kimi K2.6") == "kimi"
    assert bf.provider_for("") == "openai"


def test_existing_scores_are_never_overwritten():
    """Label was measured at 36; a backfill must not touch it."""
    merged, filled = bf.merge(
        rec(), fresh(Logo=55, Stitching=60, Hardware=50, Label=99, Material=58),
        {"score": 54, "band": "caution", "verdict_label": "Inconclusive",
         "coverage": {"assessed": 1}})
    assert merged["dimensions"]["Label"]["score"] == 36     # not 99
    assert merged["dimensions"]["Label"]["status"] == "scored"
    assert set(filled) == {"Logo", "Stitching", "Hardware", "Material"}


def test_filled_cells_are_marked_estimated():
    merged, _ = bf.merge(rec(), fresh(Logo=55, Stitching=60, Hardware=50, Material=58),
                         {"score": 54, "coverage": {"assessed": 1}})
    for d in ("Logo", "Stitching", "Hardware", "Material"):
        assert merged["dimensions"][d]["status"] == "estimated"


def test_coverage_still_reports_the_original_evidence():
    """A fully populated row must not start claiming 5/5 assessed."""
    merged, _ = bf.merge(rec(), fresh(Logo=55, Stitching=60, Hardware=50, Material=58),
                         {"score": 54, "coverage": {"assessed": 1}})
    assert merged["assessed"] == 1


def test_original_run_metadata_is_preserved():
    merged, _ = bf.merge(rec(), fresh(Logo=55, Stitching=60, Hardware=50, Material=58),
                         {"score": 54, "coverage": {"assessed": 1}})
    assert merged["created_at"] == "2026-07-31T10:00:00Z"
    assert merged["cost"] == 0.41 and merged["latency_ms"] == 68700
    assert merged["id"] == "r1"
    assert "backfilled_at" in merged and "backfill_note" in merged


def test_no_fill_leaves_the_record_untouched():
    original = rec()
    merged, filled = bf.merge(original, {}, {"score": 1})
    assert filled == []
    assert merged["dimensions"] == original["dimensions"]
    assert "backfilled_at" not in merged


def test_merge_does_not_mutate_the_input():
    original = rec()
    bf.merge(original, fresh(Logo=55), {"score": 1, "coverage": {"assessed": 1}})
    assert original["dimensions"]["Logo"]["score"] is None


def test_state_reconstruction():
    st = bf.build_state(rec())
    assert st["provider"] == "openai"
    assert st["brand"] == "TNF"
    assert st["suspect_images"] == ["aGk="]
    assert st["ref_source"] == "local"
