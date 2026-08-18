"""The SKU registry interface, and the empty-registry behaviour that matters most.

The registry ships EMPTY. Every test here that asserts "unknown" is asserting
that an absent spec produces no assertion — because a registry populated with
guessed values would produce confident, terminal, wrong verdicts on genuine
product, which is strictly worse than having no registry at all.
"""
import json

import material_rules
import sku_registry


def _write(tmp_path, monkeypatch, data):
    p = tmp_path / "sku_registry.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("SKU_REGISTRY_PATH", str(p))
    sku_registry.reload()
    return p


# ---- empty / broken --------------------------------------------------------
def test_missing_registry_is_empty_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("SKU_REGISTRY_PATH", str(tmp_path / "nope.json"))
    sku_registry.reload()
    assert sku_registry.available() is False
    assert sku_registry.lookup("NF0A3C8D") is None
    assert sku_registry.field("NF0A3C8D", "fill_power") is None


def test_malformed_registry_is_empty_not_an_error(tmp_path, monkeypatch):
    p = tmp_path / "sku_registry.json"
    p.write_text("{ this is not json", encoding="utf-8")
    monkeypatch.setenv("SKU_REGISTRY_PATH", str(p))
    sku_registry.reload()
    assert sku_registry.available() is False
    assert sku_registry.lookup("NF0A3C8D") is None


# ---- lookup ----------------------------------------------------------------
def test_lookup_returns_the_row_for_the_style(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {"NF0A3C8D": {"2022": {"fill_power": 700}}})
    assert sku_registry.field("NF0A3C8D", "fill_power") == 700


def test_style_code_is_normalised_out_of_free_text(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {"NF0A3C8D": {"2022": {"fill_power": 700}}})
    assert sku_registry.normalise_style("style nf0a3c8d ") == "NF0A3C8D"
    assert sku_registry.field("  nf0a3c8d", "fill_power") == 700
    assert sku_registry.normalise_style("RW1818273") == ""


def test_a_later_row_is_never_used_for_an_earlier_garment(tmp_path, monkeypatch):
    """Comparing a 2019 garment against a 2025 spec sheet is the single largest
    false-positive generator in this design."""
    _write(tmp_path, monkeypatch,
           {"NF0A3C8D": {"2019": {"fill_power": 700}, "2025": {"fill_power": 800}}})
    assert sku_registry.field("NF0A3C8D", "fill_power", 2019) == 700
    assert sku_registry.field("NF0A3C8D", "fill_power", 2022) == 700   # closest <= 2022
    assert sku_registry.field("NF0A3C8D", "fill_power", 2025) == 800


def test_a_garment_older_than_every_row_gets_no_answer(tmp_path, monkeypatch):
    """'We have no spec for this era' and 'the spec is X' are different answers."""
    _write(tmp_path, monkeypatch, {"NF0A3C8D": {"2019": {"fill_power": 700}}})
    assert sku_registry.field("NF0A3C8D", "fill_power", 2005) is None


def test_an_unknown_style_gets_no_answer(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {"NF0A3C8D": {"2022": {"fill_power": 700}}})
    assert sku_registry.lookup("NF0AZZZZZ") is None


# ---- M-D6, the worked [DET-OWN] check --------------------------------------
def _tag(care, style="", date=""):
    return {"care_text": care, "style_number": style, "date_code": date}


def test_fill_power_above_the_catalogue_maximum_fails_without_any_registry(
        tmp_path, monkeypatch):
    """The SKU-free half. 'No TNF product is 900 FP' is a statement about the
    whole catalogue, so it needs no style code and works today."""
    monkeypatch.setenv("SKU_REGISTRY_PATH", str(tmp_path / "nope.json"))
    sku_registry.reload()
    r = material_rules.validate(_tag("900 FILL POWER DOWN"))
    c = {x["id"]: x for x in r["checks"]}["M-D6"]
    assert c["status"] == "fail"
    assert r["spec_hard_fail"] is True


def test_ordinary_fill_power_is_unknown_without_a_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("SKU_REGISTRY_PATH", str(tmp_path / "nope.json"))
    sku_registry.reload()
    r = material_rules.validate(_tag("700 FILL POWER DOWN", style="NF0A3C8D"))
    c = {x["id"]: x for x in r["checks"]}["M-D6"]
    assert c["status"] == "unknown"
    assert r["spec_hard_fail"] is False


def test_fill_power_contradicting_the_registry_fails(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {"NF0A3C8D": {"2022": {"fill_power": 700}}})
    r = material_rules.validate(_tag("800 FILL POWER DOWN", style="NF0A3C8D",
                                     date="2022"))
    c = {x["id"]: x for x in r["checks"]}["M-D6"]
    assert c["status"] == "fail"
    assert "700" in c["evidence"]
    assert r["spec_hard_fail"] is True


def test_fill_power_matching_the_registry_passes(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {"NF0A3C8D": {"2022": {"fill_power": 700}}})
    r = material_rules.validate(_tag("700 FILL POWER DOWN", style="NF0A3C8D",
                                     date="2022"))
    assert {x["id"]: x for x in r["checks"]}["M-D6"]["status"] == "pass"
    assert r["spec_hard_fail"] is False


def test_thermoball_comparability_still_not_read_as_a_declaration(
        tmp_path, monkeypatch):
    """The trap survives the new check: M-D6 must not read a comparability
    claim as a fill-power declaration either."""
    _write(tmp_path, monkeypatch, {"NF0A3C8D": {"2022": {"fill_power": 700}}})
    r = material_rules.validate(
        _tag("THERMOBALL ECO. COMPARABLE TO 600 FILL POWER DOWN.", style="NF0A3C8D"))
    assert {x["id"]: x for x in r["checks"]}["M-D6"]["status"] == "unknown"
    assert r["spec_hard_fail"] is False
