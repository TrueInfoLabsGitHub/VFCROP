"""The LIKELY_AUTH_REQUIRES_UPC_MATCH lever.

Off (the shipped default), the ordinary clearance is reachable on photographs
alone — unchanged behaviour. On, an item that would have cleared without a
verified barcode goes to REVIEW (R10b), never to a rejection: the lever's
measured cost must be review volume, not false convictions.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import scoring                                                   # noqa: E402
from scoring import Dim, DimState                                # noqa: E402


def _clean_dims():
    """Five MEASURED, confident, fully-covered dimensions, all near zero —
    the canonical Likely Authentic input."""
    return [Dim(n, 5, DimState.MEASURED, 0.9, 1.0, "clean")
            for n in scoring.DIMENSION_NAMES]


def test_default_behaviour_unchanged():
    res = scoring.decide(_clean_dims(), category="jacket", upc_status="not_provided")
    assert res["rule"] in ("R10", "R10a")
    assert res["verdict_label"] == "Likely Authentic"


def test_lever_routes_clean_item_to_review_not_rejection(monkeypatch):
    monkeypatch.setattr(scoring, "LIKELY_AUTH_REQUIRES_UPC_MATCH", True)
    res = scoring.decide(_clean_dims(), category="jacket", upc_status="not_provided")
    assert res["rule"] == "R10b"
    assert res["verdict_label"] == "Inconclusive"
    assert res["lane"] == "REVIEW", "the lever must cost review volume, never a conviction"


def test_lever_still_clears_with_a_verified_upc(monkeypatch):
    monkeypatch.setattr(scoring, "LIKELY_AUTH_REQUIRES_UPC_MATCH", True)
    res = scoring.decide(_clean_dims(), category="jacket", upc_status="match")
    assert res["verdict_label"] in ("Likely Authentic", "Authentic")


def test_lever_never_touches_convictions(monkeypatch):
    monkeypatch.setattr(scoring, "LIKELY_AUTH_REQUIRES_UPC_MATCH", True)
    dims = _clean_dims()
    dims[0] = Dim("Logo", 82, DimState.MEASURED, 0.9, 1.0, "wrong stitch density")
    res = scoring.decide(dims, category="jacket", upc_status="not_provided")
    assert res["lane"] == "REJECTED"


def test_r10b_has_a_rule_sentence():
    assert "R10b" in scoring.RULES
