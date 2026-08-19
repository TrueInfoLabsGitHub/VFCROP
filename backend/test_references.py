"""The stock reference crops are all of ONE puffer jacket. They must not be
handed out for products they cannot stand in for."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import references                                                # noqa: E402


def test_a_jacket_gets_the_jacket_references():
    refs = references.select_references("TNF", "jacket")
    assert refs and set(refs) >= {"Logo", "Stitching", "Hardware", "Label", "Material"}


def test_kin_categories_may_share_them():
    """Same construction family, same hardware and shell vocabulary."""
    for cat in ("vest", "fleece", "hoodie"):
        assert references.select_references("TNF", cat)


def test_a_t_shirt_gets_NO_reference_rather_than_a_jacket():
    """This is the bug. A T-shirt's Hardware was compared against a jacket's zip
    pull, and the resulting deviation entered the composite looking exactly like
    a real measurement."""
    assert references.select_references("TNF", "t-shirt") == {}
    assert references.select_references("TNF", "swimsuit") == {}
    assert references.select_references("TNF", "hat") == {}


def test_an_unknown_category_still_gets_them():
    """normalise_category returns "" when it cannot tell. Declining to compare on
    the strength of a failed string match would lose real analyses; the pairing
    agent is the check that catches a genuine mismatch."""
    assert references.select_references("TNF", "")


def test_an_unknown_brand_gets_nothing():
    """It used to fall back to TNF, so a Nike submission was scored against a
    North Face jacket."""
    assert references.select_references("Nike", "jacket") == {}


def test_a_missing_file_loads_as_None_not_as_an_exception():
    assert references.load_ref_b64("") is None
    assert references.load_ref_b64("does-not-exist.jpg") is None
