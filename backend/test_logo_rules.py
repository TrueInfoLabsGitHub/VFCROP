"""Deterministic logo/mark rules, and the traps they must not fall into."""
import logo_rules as L
from label_rules import PASS, FAIL, UNKNOWN, CRITICAL, STRONG


def _by_id(checks):
    return {c["id"]: c for c in checks}


def _tag(mark="", style="", date="", family=""):
    return {"mark_text": mark, "style_number": style,
            "date_code": date, "product_family": family}


# ---- L-D4 / L-D5: dated marks ----------------------------------------------
def test_tagline_before_first_use_is_impossible():
    r = L.validate(_tag(mark="NEVER STOP EXPLORING", date="1995"))
    assert _by_id(r["checks"])["L-D4"]["status"] == FAIL
    assert r["provenance_hard_fail"] is True


def test_tagline_after_first_use_passes():
    r = L.validate(_tag(mark="NEVER STOP EXPLORING", date="2010"))
    assert _by_id(r["checks"])["L-D4"]["status"] == PASS
    assert r["provenance_hard_fail"] is False


def test_summit_series_before_2000_is_impossible():
    r = L.validate(_tag(mark="SUMMIT SERIES", date="1998"))
    assert _by_id(r["checks"])["L-D5"]["status"] == FAIL
    assert r["provenance_hard_fail"] is True


def test_rmst_before_2022_is_impossible():
    r = L.validate(_tag(mark="RMST", date="2020"))
    assert r["provenance_hard_fail"] is True


def test_season_codes_are_understood():
    """FW19 -> 2019. TNF tags print seasons far more often than plain years."""
    assert L._year_of({"date_code": "FW19"}) == 2019
    assert L._year_of({"date_code": "SS 18"}) == 2018
    assert L._year_of({"date_code": "AW21"}) == 2021
    assert L._year_of({"date_code": "2019"}) == 2019
    assert L._year_of({"date_code": ""}) is None


def test_era_checks_are_unknown_without_a_date():
    r = L.validate(_tag(mark="SUMMIT SERIES"))
    assert _by_id(r["checks"])["L-D5"]["status"] == UNKNOWN
    assert r["provenance_hard_fail"] is False


# ---- L-D6: marks that never existed ----------------------------------------
def test_gucci_chapter_three_never_existed():
    r = L.validate(_tag(mark="GUCCI X THE NORTH FACE CHAPTER 3"))
    c = _by_id(r["checks"])["L-D6"]
    assert c["status"] == FAIL and c["severity"] == CRITICAL
    assert r["provenance_hard_fail"] is True


def test_summit_pro_tier_never_existed():
    r = L.validate(_tag(mark="SUMMIT PRO"))
    assert r["provenance_hard_fail"] is True


def test_1966_series_is_strong_not_critical():
    """OCR transposing 1996 -> 1966 on a Retro Nuptse must not be terminal."""
    r = L.validate(_tag(mark="1966 SERIES"))
    c = _by_id(r["checks"])["L-D6"]
    assert c["status"] == FAIL and c["severity"] == STRONG
    assert r["provenance_hard_fail"] is False


def test_trap_1996_retro_is_not_1966_series():
    """The most common genuine product in this catalogue must stay clean."""
    for mark in ("1996 RETRO NUPTSE", "1996 RETRO DENALI", "1996 SERIES"):
        r = L.validate(_tag(mark=mark, date="2022"))
        assert not [c for c in r["checks"] if c["status"] == FAIL], mark


# ---- L-D7: collaboration windows -------------------------------------------
def test_collab_before_the_partnership_began_is_impossible():
    r = L.validate(_tag(mark="GUCCI X THE NORTH FACE", date="2019"))
    c = _by_id(r["checks"])["L-D7"]
    assert c["status"] == FAIL and c["severity"] == CRITICAL
    assert r["provenance_hard_fail"] is True


def test_collab_inside_its_window_passes():
    r = L.validate(_tag(mark="SUPREME", date="FW15"))
    assert _by_id(r["checks"])["L-D7"]["status"] == PASS


def test_trap_collab_after_the_last_known_drop_is_suggestive_only():
    """A drop list is never provably complete, and collaborations get
    re-released. Late is a signal; it is not a conviction."""
    r = L.validate(_tag(mark="KAWS", date="2024"))
    c = _by_id(r["checks"])["L-D7"]
    assert c["status"] == FAIL and c["severity"] != CRITICAL
    assert r["provenance_hard_fail"] is False


# ---- L-D8: mark against the style prefix -----------------------------------
def test_purple_label_on_an_nf0a_code_is_a_spec_contradiction():
    r = L.validate(_tag(mark="PURPLE LABEL", style="NF0A3C8D"))
    assert _by_id(r["checks"])["L-D8"]["status"] == FAIL
    assert r["spec_hard_fail"] is True
    assert r["provenance_hard_fail"] is False


def test_purple_label_on_an_nn_code_passes():
    r = L.validate(_tag(mark="PURPLE LABEL", style="NN02345"))
    assert _by_id(r["checks"])["L-D8"]["status"] == PASS


def test_purple_label_with_no_style_read_is_unknown():
    r = L.validate(_tag(mark="PURPLE LABEL"))
    assert _by_id(r["checks"])["L-D8"]["status"] == UNKNOWN


# ---- contract --------------------------------------------------------------
def test_unreadable_marks_never_convict():
    r = L.validate({})
    assert r["spec_hard_fail"] is False and r["provenance_hard_fail"] is False
    assert all(c["status"] == UNKNOWN for c in r["checks"])
    assert r["internal_coverage"] == 0.0


def test_an_ordinary_genuine_tag_is_clean():
    r = L.validate(_tag(mark="SUMMIT SERIES, NEVER STOP EXPLORING",
                        style="NF0A3C8D", date="FW22"))
    assert not [c for c in r["checks"] if c["status"] == FAIL]
    assert r["spec_hard_fail"] is False and r["provenance_hard_fail"] is False


def test_only_fails_inject_and_a_pass_injects_nothing():
    """A correctly spelled, correctly dated mark is the easiest thing for a
    counterfeiter to copy — it must never buy coverage on the Logo dimension."""
    clean = L.validate(_tag(mark="SUMMIT SERIES", date="2022"))
    assert L.dimension_injection(clean) is None
    bad = L.validate(_tag(mark="SUMMIT SERIES", date="1998"))
    inj = L.dimension_injection(bad)
    assert inj["score"] == 85 and inj["confidence"] >= 0.6


def test_only_critical_fails_can_hard_fail():
    r = L.validate(_tag(mark="1966 SERIES"))
    assert [c for c in r["checks"] if c["status"] == FAIL]
    assert r["spec_hard_fail"] is False and r["provenance_hard_fail"] is False
