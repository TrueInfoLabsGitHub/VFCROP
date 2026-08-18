"""Per-style specification checks against the five populated products.

The registry rows are real published specs, so these tests double as a check on
the DATA: if a row is edited wrongly, the genuine-item tests here go red.
"""
import pytest

import sku_registry
import spec_rules as S
from label_rules import PASS, FAIL, UNKNOWN, CRITICAL, STRONG


@pytest.fixture(autouse=True)
def _real_registry(monkeypatch):
    """Use the shipped sku_registry.json, not whatever a previous test set."""
    monkeypatch.delenv("SKU_REGISTRY_PATH", raising=False)
    sku_registry.reload()
    yield
    sku_registry.reload()


def _by(checks):
    return {c["id"]: c for c in checks}


def _f(style="", fiber="", care="", mark="", family=""):
    return {"style_number": style, "fiber_content": fiber, "care_text": care,
            "mark_text": mark, "product_family": family}


def _dims(**methods):
    return [{"dimension": d, "method": methods.get(d, "UNKNOWN")}
            for d in ("Logo", "Stitching", "Hardware", "Label", "Material")]


# ---- the registry itself ---------------------------------------------------
def test_all_five_products_are_in_the_registry():
    for style, model in (("NF0A3C8D", "Nuptse"), ("NF0A88XH", "Denali"),
                         ("NF0A8D1P", "ThermoBall"), ("NF0A831M", "Mountain"),
                         ("NF0A7UNL", "Half Dome")):
        row = sku_registry.lookup(style)
        assert row, style
        assert model.lower().replace(" ", "") in row["name"].lower().replace(" ", "")


def test_a_vintage_garment_gets_no_spec_row():
    """The rows are current-season. A 2005 garment must not be judged against
    a 2023 spec sheet."""
    assert sku_registry.lookup("NF0A3C8D", 2005) is None


# ---- a genuine item of each style clears every check -----------------------
def test_a_genuine_nuptse_passes_every_applicable_check():
    r = S.validate(
        _f(style="NF0A3C8D", fiber="SHELL: 100% NYLON", care="700 FILL POWER DOWN"),
        product="1996 Retro Nuptse Jacket",
        dim_results=_dims(Logo="embroidery", Material="woven", Hardware="zip"))
    assert not [c for c in r["checks"] if c["status"] == FAIL], r["summary"]
    assert r["spec_hard_fail"] is False
    assert r["injections"] == {}


def test_a_genuine_half_dome_hoodie_passes():
    r = S.validate(
        _f(style="NF0A7UNL", fiber="BODY: 73% COTTON 27% POLYESTER"),
        product="Half Dome Pullover Hoodie",
        dim_results=_dims(Logo="screen", Material="knit", Hardware="drawcord_aglet"))
    assert not [c for c in r["checks"] if c["status"] == FAIL], r["summary"]
    assert r["injections"] == {}


def test_a_heather_hoodie_passes_on_the_other_dominant_fibre():
    """Solids are cotton-dominant, heathers polyester-dominant. Both genuine."""
    r = S.validate(_f(style="NF0A7UNL", fiber="BODY: 56% POLYESTER 44% COTTON"),
                   product="Half Dome Pullover Hoodie")
    assert _by(r["checks"])["SP-4"]["status"] == PASS


# ---- SP-1: the tag's style code belongs to another model -------------------
def test_a_nuptse_tag_on_a_denali_is_a_spec_hard_fail():
    r = S.validate(_f(style="NF0A3C8D"), product="Retro Denali Jacket")
    assert _by(r["checks"])["SP-1"]["status"] == FAIL
    assert r["spec_hard_fail"] is True


def test_an_unclassifiable_product_name_asserts_nothing():
    r = S.validate(_f(style="NF0A3C8D"), product="black jacket")
    assert _by(r["checks"])["SP-1"]["status"] == UNKNOWN
    assert r["spec_hard_fail"] is False


# ---- SP-2: decoration method (Logo) ----------------------------------------
def test_a_screen_printed_logo_on_an_embroidered_style_injects_into_logo():
    r = S.validate(_f(style="NF0A3C8D"), product="1996 Retro Nuptse",
                   dim_results=_dims(Logo="screen"))
    assert _by(r["checks"])["SP-2"]["status"] == FAIL
    assert r["injections"]["Logo"]["score"] == 85          # dispositive
    assert "Material" not in r["injections"]               # stays on its own dimension


def test_an_embroidered_logo_on_the_screen_printed_hoodie_injects():
    r = S.validate(_f(style="NF0A7UNL"), product="Half Dome Pullover Hoodie",
                   dim_results=_dims(Logo="embroidery"))
    assert _by(r["checks"])["SP-2"]["status"] == FAIL
    assert r["injections"]["Logo"]["score"] == 85


def test_an_unresolved_application_method_asserts_nothing():
    r = S.validate(_f(style="NF0A3C8D"), product="1996 Retro Nuptse",
                   dim_results=_dims())
    assert _by(r["checks"])["SP-2"]["status"] == UNKNOWN
    assert r["injections"] == {}


# ---- SP-3 / SP-4: Material -------------------------------------------------
def test_a_knit_structure_on_a_woven_style_injects_into_material():
    r = S.validate(_f(style="NF0A3C8D"), product="1996 Retro Nuptse",
                   dim_results=_dims(Material="knit"))
    assert _by(r["checks"])["SP-3"]["status"] == FAIL
    assert r["injections"]["Material"]["score"] == 80
    assert r["spec_hard_fail"] is False                     # strong, not critical


def test_the_wrong_dominant_fibre_injects_into_material():
    r = S.validate(_f(style="NF0A3C8D", fiber="SHELL: 100% POLYESTER"),
                   product="1996 Retro Nuptse")
    assert _by(r["checks"])["SP-4"]["status"] == FAIL
    assert "Material" in r["injections"]


def test_the_denali_is_polyester_fleece_not_nylon():
    r = S.validate(_f(style="NF0A88XH", fiber="BODY: 100% RECYCLED POLYESTER"),
                   product="Retro Denali Jacket",
                   dim_results=_dims(Material="fleece_pile"))
    assert _by(r["checks"])["SP-4"]["status"] == PASS
    assert _by(r["checks"])["SP-3"]["status"] == PASS


# ---- SP-5: insulation ------------------------------------------------------
def test_down_declared_on_a_thermoball_is_a_spec_hard_fail():
    """ThermoBall is synthetic clusters. A down claim on one is the documented
    giveaway."""
    r = S.validate(_f(style="NF0A8D1P", care="THERMOBALL ECO",
                      fiber="FILLING: 80% DOWN 20% FEATHER"),
                   product="ThermoBall Jacket 2.0")
    assert _by(r["checks"])["SP-5"]["status"] == FAIL
    assert r["spec_hard_fail"] is True


def test_down_on_the_nuptse_is_correct():
    r = S.validate(_f(style="NF0A3C8D", fiber="FILLING: 90% DOWN 10% FEATHER"),
                   product="1996 Retro Nuptse")
    assert _by(r["checks"])["SP-5"]["status"] == PASS


# ---- SP-6: membrane --------------------------------------------------------
def test_dryvent_on_the_goretex_mountain_jacket_is_a_spec_hard_fail():
    r = S.validate(_f(style="NF0A831M", care="DRYVENT 2L SHELL"),
                   product="GORE-TEX Mountain Jacket")
    assert _by(r["checks"])["SP-6"]["status"] == FAIL
    assert r["spec_hard_fail"] is True


def test_goretex_on_a_style_with_no_membrane_is_a_spec_hard_fail():
    r = S.validate(_f(style="NF0A3C8D", care="GORE-TEX PRODUCT"),
                   product="1996 Retro Nuptse")
    assert _by(r["checks"])["SP-6"]["status"] == FAIL
    assert r["spec_hard_fail"] is True


def test_a_tag_naming_no_membrane_is_never_a_tell():
    """Silence is ordinary. Treating it as evidence would fail genuine stock."""
    r = S.validate(_f(style="NF0A831M", care="MACHINE WASH WARM"),
                   product="GORE-TEX Mountain Jacket")
    assert _by(r["checks"])["SP-6"]["status"] == UNKNOWN
    assert r["spec_hard_fail"] is False


def test_goretex_named_on_the_goretex_jacket_passes():
    r = S.validate(_f(style="NF0A831M", care="GORE-TEX(R) PRODUCT. 2L."),
                   product="GORE-TEX Mountain Jacket")
    assert _by(r["checks"])["SP-6"]["status"] == PASS


# ---- SP-7 / SP-8: hardware -------------------------------------------------
def test_a_zip_on_the_pullover_hoodie_injects_into_hardware():
    r = S.validate(_f(style="NF0A7UNL"), product="Half Dome Pullover Hoodie",
                   dim_results=_dims(Hardware="zip"))
    assert _by(r["checks"])["SP-7"]["status"] == FAIL
    assert r["injections"]["Hardware"]["score"] == 80


def test_a_zip_on_the_nuptse_is_expected():
    r = S.validate(_f(style="NF0A3C8D"), product="1996 Retro Nuptse",
                   dim_results=_dims(Hardware="zip"))
    assert _by(r["checks"])["SP-7"]["status"] == UNKNOWN     # style has a zip
    assert "Hardware" not in r["injections"]


def test_a_ying_slider_stamp_is_a_spec_hard_fail():
    r = S.validate(_f(style="NF0A3C8D", mark="YING"), product="1996 Retro Nuptse")
    assert _by(r["checks"])["SP-8"]["status"] == FAIL
    assert r["spec_hard_fail"] is True


# ---- contract --------------------------------------------------------------
def test_an_unknown_style_code_asserts_nothing():
    r = S.validate(_f(style="NF0AZZZZZ", fiber="100% NYLON"), product="Nuptse",
                   dim_results=_dims(Logo="screen", Material="knit"))
    assert r["spec_hard_fail"] is False and r["provenance_hard_fail"] is False
    assert r["injections"] == {}


def test_no_style_number_read_asserts_nothing():
    r = S.validate(_f(), product="1996 Retro Nuptse",
                   dim_results=_dims(Logo="screen"))
    assert not [c for c in r["checks"] if c["status"] == FAIL]
    assert r["injections"] == {}


def test_a_pass_never_injects():
    """Matching the published spec is what a counterfeiter copies first, straight
    off the product page. It can never be evidence of authenticity."""
    r = S.validate(_f(style="NF0A3C8D", fiber="SHELL: 100% NYLON"),
                   product="1996 Retro Nuptse",
                   dim_results=_dims(Logo="embroidery", Material="woven"))
    assert r["counts"][PASS] >= 3
    assert r["injections"] == {}


def test_coverage_is_reported_per_dimension():
    r = S.validate(_f(style="NF0A3C8D", fiber="SHELL: 100% NYLON"),
                   product="1996 Retro Nuptse",
                   dim_results=_dims(Logo="embroidery"))
    cov = r["coverage_by_dimension"]
    assert cov["Logo"] > 0 and 0.0 <= cov["Material"] <= 1.0
    assert set(cov) == {"Label", "Logo", "Material", "Hardware"}


def test_non_dict_fields_never_crash():
    for bad in ("junk", ["x"], 42, None, {}):
        r = S.validate(bad, product="Nuptse")
        assert r["spec_hard_fail"] is False
