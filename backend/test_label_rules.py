"""Tests for the deterministic label validator.

These checks are the ones that must hold without any model in the loop: a fibre
list either sums to 100 or it does not. The tests below therefore assert exact
statuses, and in particular assert that an unavailable check reports UNKNOWN
rather than quietly passing — a silent pass on a check that never ran is the
failure mode that makes a compliance report worthless.

Run:  pytest backend/test_label_rules.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import label_rules as lr                                          # noqa: E402


def by_id(checks, cid):
    return next(c for c in checks if c["id"] == cid)


# ---- fibre content ---------------------------------------------------------
def test_fibre_percentages_must_sum_to_100():
    ok = by_id(lr.check_fiber_content("75% DOWN, 25% WATERFOWL FEATHERS"), "F1")
    assert ok["status"] == lr.PASS
    bad = by_id(lr.check_fiber_content("60% COTTON, 30% POLYESTER"), "F1")
    assert bad["status"] == lr.FAIL and bad["severity"] == lr.CRITICAL


def test_multi_component_declarations_sum_per_component():
    text = "SHELL: 100% NYLON  FILLING: 90% DOWN 10% FEATHER"
    assert by_id(lr.check_fiber_content(text), "F1")["status"] == lr.PASS


def test_misspelled_fibre_name_is_a_hard_tell():
    """'POLEYESTER' is one edit from 'polyester' — the classic printed tell."""
    r = by_id(lr.check_fiber_content("FILLER: 100% POLEYESTER"), "F2")
    assert r["status"] == lr.FAIL
    assert "polyester" in r["evidence"].lower()


def test_foreign_language_fibre_names_are_not_flagged():
    """Multilingual care labels are legitimate. BAUMWOLLE/PAMUK must not be
    scored as misspellings — this was a real false positive in the corpus."""
    r = by_id(lr.check_fiber_content("100% BAUMWOLLE / 100% PAMUK / 100% COTTON"), "F2")
    assert r["status"] == lr.PASS


def test_run_together_formatting_is_flagged():
    r = by_id(lr.check_fiber_content("100%POLYESTER"), "F3")
    assert r["status"] == lr.FAIL


def test_correct_spacing_passes():
    assert by_id(lr.check_fiber_content("100% POLYESTER"), "F3")["status"] == lr.PASS


def test_absent_fibre_text_is_unknown_not_pass():
    r = lr.check_fiber_content("")
    assert len(r) == 1 and r[0]["status"] == lr.UNKNOWN


# ---- style number ----------------------------------------------------------
@pytest.mark.parametrize("style", ["NF0A3C8D", "nf0a3jqc", "A71V", "CH2M"])
def test_valid_style_numbers_pass(style):
    assert by_id(lr.check_style_number(style), "S1")["status"] == lr.PASS


def test_malformed_style_number_fails():
    assert by_id(lr.check_style_number("XZ99-2"), "S1")["status"] == lr.FAIL


def test_registration_code_is_not_a_malformed_style_number():
    """RW1818273 / CA85730 are legitimate identifiers. Reading them as broken
    style numbers is a false positive this pipeline has produced before."""
    for code in ("RW1818273", "CA85730", "RN 61661"):
        assert by_id(lr.check_style_number(code), "S1")["status"] == lr.UNKNOWN


def test_absent_style_number_is_unknown_not_fail():
    assert by_id(lr.check_style_number(""), "S1")["status"] == lr.UNKNOWN


# ---- RN registry -----------------------------------------------------------
def test_rn_resolving_to_the_brand_owner_passes():
    r = by_id(lr.check_rn_registry("61661", "TNF"), "R3")
    assert r["status"] == lr.PASS
    assert "VF OUTDOOR" in r["evidence"]


def test_rn_belonging_to_another_company_is_a_hard_fail():
    fake = lambda rn: {"rn": rn, "name": "ACME SOCKS LLC", "products": "SOCKS",
                       "reachable": True}
    r = by_id(lr.check_rn_registry("99999", "TNF", lookup=fake), "R3")
    assert r["status"] == lr.FAIL and r["severity"] == lr.CRITICAL


def test_unissued_rn_is_a_hard_fail():
    absent = lambda rn: {"rn": rn, "name": "", "products": "", "reachable": True}
    r = by_id(lr.check_rn_registry("12345", "TNF", lookup=absent), "R3")
    assert r["status"] == lr.FAIL
    assert "does not exist" in r["evidence"]


def test_unreachable_registry_is_unknown_never_a_fail():
    """A network outage must not manufacture a counterfeit indicator."""
    down = lambda rn: None
    r = by_id(lr.check_rn_registry("61661", "TNF", lookup=down), "R3")
    assert r["status"] == lr.UNKNOWN


def test_absent_rn_is_unknown():
    assert by_id(lr.check_rn_registry("", "TNF"), "R3")["status"] == lr.UNKNOWN


def test_rn_syntax_bounds():
    assert by_id(lr.check_registration_syntax("61661", ""), "R1")["status"] == lr.PASS
    assert by_id(lr.check_registration_syntax("123456789", ""), "R1")["status"] == lr.FAIL


# ---- statutory phrasing ----------------------------------------------------
def test_correct_statutory_phrasing_passes():
    r = by_id(lr.check_statutory_phrasing("55% COTTON EXCLUSIVE OF DECORATION"), "P1")
    assert r["status"] == lr.PASS


def test_mangled_statutory_phrasing_fails():
    r = by_id(lr.check_statutory_phrasing("EXCLUSIVE OF DECORATON"), "P1")
    assert r["status"] == lr.FAIL


# ---- cross-field and batch -------------------------------------------------
def test_size_mismatch_across_tags_is_reported_as_strong():
    """Was CRITICAL, and therefore terminal on its own. See
    test_a_size_disagreement_is_strong_not_terminal for why it is not."""
    r = by_id(lr.check_cross_field({"size_neck": "M", "size_care": "L"}), "X1")
    assert r["status"] == lr.FAIL and r["severity"] == lr.STRONG


def test_size_agreement_passes():
    r = by_id(lr.check_cross_field({"size_neck": "M", "size_care": "m"}), "X1")
    assert r["status"] == lr.PASS


def test_one_style_across_many_products_is_a_batch_tell():
    prior = [("NF0A3C8D", "Nuptse Jacket"), ("NF0A3C8D", "Redbox T-Shirt"),
             ("NF0A3C8D", "Norm Hat")]
    r = by_id(lr.check_batch_duplicates("NF0A3C8D", prior), "B1")
    assert r["status"] == lr.FAIL and r["severity"] == lr.CRITICAL


def test_style_used_on_one_product_passes():
    prior = [("NF0A3C8D", "Nuptse Jacket"), ("NF0A3C8D", "Nuptse Jacket")]
    assert by_id(lr.check_batch_duplicates("NF0A3C8D", prior), "B1")["status"] == lr.PASS


# ---- roll-up ---------------------------------------------------------------
def test_hard_fail_requires_an_actual_critical_failure():
    """Unknowns must never produce a hard fail — the whole point of the
    three-state design."""
    res = lr.validate({}, brand="TNF", lookup=lambda rn: None)
    assert res["hard_fail"] is False
    assert res["counts"][lr.FAIL] == 0
    assert res["counts"][lr.UNKNOWN] > 0


def test_clean_label_produces_no_hard_fail():
    res = lr.validate({
        "fiber_content": "SHELL: 100% NYLON  FILLING: 90% DOWN 10% FEATHER",
        "style_number": "NF0A3C8D", "rn": "61661",
        "care_text": "MACHINE WASH. DO NOT BLEACH. TUMBLE DRY.",
        "size_neck": "M", "size_care": "M",
    }, brand="TNF")
    assert res["hard_fail"] is False
    assert res["counts"][lr.FAIL] == 0


def test_the_shared_chat_sample_reproduces():
    """The label from the shared conversation: style NF0A3C8D, 75% down /
    25% waterfowl feathers, EXCLUSIVE OF DECORATION, RN not visible.
    Expect no fails, and RN resolution explicitly UNKNOWN — not a pass."""
    res = lr.validate({
        "fiber_content": "FILLING: 75% DOWN, 25% WATERFOWL FEATHERS, "
                         "EXCLUSIVE OF DECORATION",
        "style_number": "NF0A3C8D", "rn": "", "ca": "",
    }, brand="TNF")
    assert res["hard_fail"] is False
    assert by_id(res["checks"], "F1")["status"] == lr.PASS
    assert by_id(res["checks"], "S1")["status"] == lr.PASS
    assert by_id(res["checks"], "R3")["status"] == lr.UNKNOWN


def test_a_counterfeit_label_hard_fails_without_any_model():
    res = lr.validate({
        "fiber_content": "FILER: 100% POLEYESTER",
        "style_number": "XZ99", "rn": "99999",
        "size_neck": "M", "size_care": "XL",
    }, brand="TNF",
        lookup=lambda rn: {"rn": rn, "name": "", "products": "", "reachable": True})
    assert res["hard_fail"] is True
    assert {"F2", "R3", "X1"} <= set(res["failed"])


# ---- registry HTML parsing -------------------------------------------------
# Captured verbatim from https://www.ftc.gov/rn-database/search?search=61661 on
# 2026-07-31. The cell values are wrapped in <a> tags and padded with newlines;
# an earlier parser that tried to match the whole row in one regex silently
# matched nothing against this, which is why the fixture is pinned here.
REAL_ROW_HTML = """
<table><tbody><tr>
  <td headers="view-field-rn-type-table-column" class="views-field views-field-field-rn-type">RN            </td>
  <td headers="view-field-rn-no-table-column" class="views-field views-field-field-rn-no"><a class="use-ajax" data-dialog-type="modal" href="/rn/193266" data-once="ajax">61661</a>            </td>
  <td headers="view-field-legal-business-name-table-column" class="views-field views-field-field-legal-business-name"><a class="use-ajax" data-dialog-type="modal" href="/rn/193266">VF OUTDOOR, INC.</a>            </td>
  <td headers="view-field-rn-product-line-table-column" class="views-field">BACKPACKS AND EQUIPMENT<br> <br>APPAREL<br> <br>FOOTWEAR            </td>
</tr></tbody></table>
"""


def test_parses_the_real_ftc_results_html():
    rows = lr.parse_rn_rows(REAL_ROW_HTML)
    assert len(rows) == 1
    assert rows[0]["no"] == "61661"
    assert rows[0]["name"] == "VF OUTDOOR, INC."
    assert "BACKPACKS AND EQUIPMENT" in rows[0]["products"]


def test_parser_ignores_header_and_unrelated_rows():
    html = ("<tr><th>Type</th><th>No.</th></tr>"
            "<tr><td>not a type</td><td>abc</td><td>x</td><td>y</td></tr>")
    assert lr.parse_rn_rows(html) == []


# ---- live registry (network) ----------------------------------------------
@pytest.mark.network
def test_live_ftc_registry_lookup():
    """Hits the real FTC database. Skipped when offline — the point of the
    seeded cache is that everything else still works."""
    lr._rn_cache.pop("168178", None)
    rec = lr.lookup_rn("168178")
    if rec is None:
        pytest.skip("FTC RN registry unreachable")
    assert "VF" in (rec["name"] or "").upper()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))


# ---- multilingual care labels ----------------------------------------------
# Caught in a live batch: a European TNF tag repeats the same declaration in
# several languages, the sum across all of them came to 800%, and F1 is CRITICAL
# — so an ordinary label produced a terminal "Counterfeit — Label Validation
# Failed". A deterministic check that fires wrongly is worse than one that never
# fires: it carries no model uncertainty, so nothing downstream can moderate it.
MULTILINGUAL = ("GB: 68% COTTON 32% POLYESTER FR: 68% COTON 32% POLYESTER "
                "ES: 68% ALGODON 32% POLIESTER")


def test_multilingual_declaration_is_not_a_false_hard_fail():
    f1 = [c for c in lr.check_fiber_content(MULTILINGUAL) if c["id"] == "F1"][0]
    assert f1["status"] == lr.PASS, f1["evidence"]
    assert not lr.validate({"fiber_content": MULTILINGUAL})["hard_fail"]


def test_a_wrong_sum_still_fails_in_every_language():
    """The fix must not blind the check — one language block is validated, and a
    declaration that is wrong in that block is still wrong."""
    bad = "GB: 60% COTTON 30% POLYESTER FR: 60% COTON 30% POLYESTER"
    f1 = [c for c in lr.check_fiber_content(bad) if c["id"] == "F1"][0]
    assert f1["status"] == lr.FAIL


def test_a_single_language_label_takes_the_same_path_as_before():
    for text, want in (("100% NYLON", lr.PASS),
                       ("SHELL: 100% NYLON FILLING: 90% DOWN 10% FEATHER", lr.PASS),
                       ("60% COTTON 30% POLYESTER", lr.FAIL)):
        f1 = [c for c in lr.check_fiber_content(text) if c["id"] == "F1"][0]
        assert f1["status"] == want, text


def test_language_blocks_only_split_when_there_are_markers():
    assert lr._language_blocks("100% NYLON") == ["100% NYLON"]
    assert len(lr._language_blocks(MULTILINGUAL)) == 3


def test_foreign_fibre_names_are_not_misspellings():
    """F2's near-miss detector read the French 'coton' and the Spanish
    'poliester' as misspellings of the English words — they are ~0.9 similar —
    and F2 is CRITICAL, so a correct multilingual tag hard-failed. They are real
    fibre names; the vocabulary was wrong, not the label."""
    for text in ("GB: 68% COTTON 32% POLYESTER FR: 68% COTON 32% POLYESTER",
                 "DE: 100% BAUMWOLLE IT: 100% COTONE",
                 "GB: 90% DOWN 10% FEATHER FR: 90% DUVET 10% PLUME"):
        v = lr.validate({"fiber_content": text})
        assert not v["hard_fail"], f"{text} -> {v['summary']}"


def test_a_genuine_misspelling_is_still_a_hard_fail():
    """The vocabulary fix must not blind the check that catches the real tell."""
    for text in ("100% COTTONN", "100% NYLONN LAMINATED", "100% POLYESTERR"):
        v = lr.validate({"fiber_content": text})
        assert v["hard_fail"], text
        assert "F2" in v["failed"]


# ---- component headers -----------------------------------------------------
def test_an_unlisted_component_header_does_not_merge_two_components():
    """Found on a live jacket: 'LINING: 100% POLYESTER  INSULATION: 100%
    POLYESTER'. INSULATION was not in the fixed header list, so the two merged
    into one component, summed 200%, and hard-failed as a counterfeit. The list
    will always be missing the next word; the shape will not."""
    for text in ("LINING: 100% POLYESTER\nINSULATION: 100% POLYESTER",
                 "SHELL: 100% NYLON POCKETING: 100% POLYESTER RIB: 95% COTTON 5% ELASTANE",
                 "FACE: 100% POLYESTER BACKING: 100% POLYURETHANE",
                 "SHELL: 100% NYLON FILLING: 90% DOWN 10% FEATHER"):
        f1 = [c for c in lr.check_fiber_content(text) if c["id"] == "F1"][0]
        assert f1["status"] == lr.PASS, f"{text} -> {f1['evidence']}"


def test_a_component_that_really_is_short_still_fails():
    f1 = [c for c in lr.check_fiber_content("SHELL: 60% NYLON") if c["id"] == "F1"][0]
    assert f1["status"] == lr.FAIL


def test_a_single_component_declaration_needs_no_header():
    f1 = [c for c in lr.check_fiber_content("100% NYLON") if c["id"] == "F1"][0]
    assert f1["status"] == lr.PASS


# ---- bilingual sizes -------------------------------------------------------
def test_a_bilingual_size_is_not_a_mismatch():
    """A TNF tag sold in Canada or the EU prints the size in two languages at
    once: S/P is Small/Petit, L/G is Large/Grand, XL/TG is Extra Large/Tres
    Grand. A neck tag reading 'M' and a care tag reading 'M/M' describe the same
    garment; comparing the raw strings called it a counterfeit."""
    for neck, care in (("M", "M/M"), ("S", "S/P"), ("L", "L/G"), ("XL", "TG"),
                       ("Medium", "M"), ("LARGE", "L/G")):
        v = lr.validate({"size_neck": neck, "size_care": care})
        x1 = [c for c in v["checks"] if c["id"] == "X1"][0]
        assert x1["status"] == lr.PASS, f"{neck} vs {care}: {x1['evidence']}"


def test_a_genuine_size_disagreement_is_still_reported():
    for neck, care in (("M", "S/P"), ("L", "XS"), ("S", "XXL")):
        x1 = [c for c in lr.validate({"size_neck": neck, "size_care": care})["checks"]
              if c["id"] == "X1"][0]
        assert x1["status"] == lr.FAIL, f"{neck} vs {care}"


def test_a_size_disagreement_is_strong_not_terminal():
    """This check declared STRONG when it passed and CRITICAL when it failed — a
    severity that depended on the outcome. One disagreement between two small
    pieces of OCR'd text ended the analysis at 'Counterfeit — Label Validation
    Failed', with no model uncertainty anywhere in the chain to moderate it. The
    other CRITICAL checks are arithmetic and vocabulary; this one rests on
    reading two tags correctly."""
    v = lr.validate({"size_neck": "M", "size_care": "S/P"})
    assert "X1" in v["failed"]          # still reported
    assert not v["hard_fail"]           # no longer terminal on its own
    x1 = [c for c in v["checks"] if c["id"] == "X1"][0]
    assert x1["severity"] == lr.STRONG
