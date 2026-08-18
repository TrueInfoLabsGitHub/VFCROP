"""Deterministic material rules.

Two halves, and the second matters as much as the first:

  * the checks fire on real contradictions;
  * the TRAPS do not fire. Section 7 of the check spec lists claims that are
    asserted by credible-looking authentication services and are wrong or
    over-claimed. Each one is encoded below as a test that something must NOT
    happen, because prose in a design document does not survive contact with a
    future contributor and a test does.
"""
import material_rules as M
from label_rules import PASS, FAIL, UNKNOWN, CRITICAL, STRONG


def _by_id(checks):
    return {c["id"]: c for c in checks}


def _tag(care="", fiber="", family="", date=""):
    return {"care_text": care, "fiber_content": fiber,
            "product_family": family, "date_code": date}


# ---------------------------------------------------------------------------
# M-D2 — membrane exclusivity
# ---------------------------------------------------------------------------
def test_goretex_with_dryvent_is_a_spec_hard_fail():
    r = M.validate(_tag(care="GORE-TEX® PRODUCT. DRYVENT™ 2L SHELL."))
    assert r["spec_hard_fail"] is True
    assert r["provenance_hard_fail"] is False
    assert _by_id(r["checks"])["M-D2"]["status"] == FAIL


def test_goretex_variants_all_match():
    for spelling in ("GORE-TEX", "GORE TEX", "GORETEX"):
        r = M.validate(_tag(care=f"{spelling}® PRODUCT. HYVENT SHELL."))
        assert r["spec_hard_fail"] is True, spelling


def test_goretex_alone_passes():
    r = M.validate(_tag(care="GORE-TEX® PRODUCT. 2L SHELL."))
    assert _by_id(r["checks"])["M-D2"]["status"] == PASS
    assert r["spec_hard_fail"] is False


def test_no_membrane_named_is_unknown_not_pass():
    r = M.validate(_tag(care="MACHINE WASH COLD. TUMBLE DRY LOW."))
    assert _by_id(r["checks"])["M-D2"]["status"] == UNKNOWN
    assert r["spec_hard_fail"] is False


# ---------------------------------------------------------------------------
# M-D3 — trademark usage, and the OCR-safety split
# ---------------------------------------------------------------------------
def test_goretex_as_a_noun_is_critical():
    r = M.validate(_tag(care="MADE OF GORE-TEX®. MACHINE WASH."))
    assert _by_id(r["checks"])["M-D3"]["status"] == FAIL
    assert r["spec_hard_fail"] is True


def test_goretexed_as_a_verb_is_critical():
    r = M.validate(_tag(care="FULLY GORE-TEXED® SHELL."))
    assert _by_id(r["checks"])["M-D3"]["status"] == FAIL


def test_missing_registered_symbol_is_strong_not_a_hard_fail():
    """OCR drops (R) and (TM) routinely. A genuine tag whose superscript did not
    survive transcription must not be convicted for it."""
    r = M.validate(_tag(care="GORE-TEX PRODUCT. MACHINE WASH COLD."))
    checks = _by_id(r["checks"])
    assert checks["M-D3b"]["status"] == FAIL
    assert checks["M-D3b"]["severity"] == STRONG
    assert checks["M-D3"]["status"] == PASS
    assert r["spec_hard_fail"] is False        # the whole point


# ---------------------------------------------------------------------------
# M-D5 — FTC down labelling
# ---------------------------------------------------------------------------
def test_absolute_down_claim_with_feather_declared_fails():
    r = M.validate(_tag(care="100% DOWN FILL", fiber="FILLING: 80% DOWN 20% FEATHER"))
    assert _by_id(r["checks"])["M-D5"]["status"] == FAIL
    assert r["spec_hard_fail"] is True


def test_unqualified_down_below_seventy_percent_fails():
    r = M.validate(_tag(fiber="FILLING: 60% DOWN"))
    assert _by_id(r["checks"])["M-D5"]["status"] == FAIL


def test_ordinary_eighty_twenty_down_passes():
    r = M.validate(_tag(fiber="FILLING: 80% DOWN 20% FEATHER"))
    assert _by_id(r["checks"])["M-D5"]["status"] == PASS
    assert r["spec_hard_fail"] is False


def test_down_word_with_no_percentage_is_unknown():
    r = M.validate(_tag(care="DOWN FILLED. DO NOT DRY CLEAN."))
    assert _by_id(r["checks"])["M-D5"]["status"] == UNKNOWN


# ---------------------------------------------------------------------------
# M-D7 — synthetic insulation
# ---------------------------------------------------------------------------
def test_thermoball_with_a_fill_power_fails():
    r = M.validate(_tag(care="THERMOBALL™ ECO INSULATION. 700 FILL POWER."))
    assert _by_id(r["checks"])["M-D7"]["status"] == FAIL
    assert r["spec_hard_fail"] is True


def test_thermoball_comparability_claim_is_legitimate():
    """TNF's own copy says ThermoBall is COMPARABLE TO 600 fill power. Reading
    that as a fill-power declaration hard-fails every genuine ThermoBall piece."""
    r = M.validate(_tag(care="THERMOBALL™ ECO. WARMTH COMPARABLE TO 600 FILL POWER DOWN."))
    assert _by_id(r["checks"])["M-D7"]["status"] == PASS
    assert r["spec_hard_fail"] is False


def test_synthetic_with_down_content_fails():
    r = M.validate(_tag(care="HEATSEEKER™ ECO", fiber="FILLING: 90% DOWN 10% FEATHER"))
    assert _by_id(r["checks"])["M-D7"]["status"] == FAIL


# ---------------------------------------------------------------------------
# M-D10 — brand collision
# ---------------------------------------------------------------------------
def test_polartec_and_tka_together_fail():
    r = M.validate(_tag(care="POLARTEC® 200 / TKA 100 FLEECE"))
    assert _by_id(r["checks"])["M-D10"]["status"] == FAIL
    assert r["spec_hard_fail"] is True


def test_goretex_on_a_dryvent_line_is_strong_not_critical():
    r = M.validate(_tag(care="GORE-TEX® PRODUCT", family="VENTURE 2 JACKET"))
    c = _by_id(r["checks"])["M-D10b"]
    assert c["status"] == FAIL and c["severity"] == STRONG
    assert r["spec_hard_fail"] is False


def test_goretex_with_no_family_read_is_unknown():
    """Asymmetric rule. A family we could not read asserts nothing."""
    r = M.validate(_tag(care="GORE-TEX® PRODUCT"))
    assert _by_id(r["checks"])["M-D10b"]["status"] == UNKNOWN


# ---------------------------------------------------------------------------
# M-D1 / M-D8 — era checks, which need a date
# ---------------------------------------------------------------------------
def test_era_checks_are_unknown_without_a_date():
    r = M.validate(_tag(care="DRYVENT™ 2L SHELL"))
    assert _by_id(r["checks"])["M-D1"]["status"] == UNKNOWN
    assert r["provenance_hard_fail"] is False


def test_technology_predating_its_launch_is_a_provenance_hard_fail():
    r = M.validate(_tag(care="DRYVENT™ 2L SHELL"), year=2010)
    assert _by_id(r["checks"])["M-D1"]["status"] == FAIL
    assert r["provenance_hard_fail"] is True
    assert r["spec_hard_fail"] is False


def test_futurelight_before_2019_is_impossible():
    r = M.validate(_tag(care="FUTURELIGHT™ SHELL"), year=2015)
    assert r["provenance_hard_fail"] is True


def test_rds_before_the_standard_existed_fails():
    r = M.validate(_tag(care="RDS CERTIFIED DOWN. CU 843098."), year=2011)
    assert _by_id(r["checks"])["M-D8"]["status"] == FAIL
    assert r["provenance_hard_fail"] is True


# ---------------------------------------------------------------------------
# Section 7 — TRAPS. Each of these must NOT fire.
# ---------------------------------------------------------------------------
def test_trap_hyvent_in_the_transition_window_does_not_fire():
    """7.9-adjacent. DryVent and HyVent overlapped in retail through 2015-2016;
    firing inside that window manufactures fakes out of genuine stock."""
    for y in (2015, 2016):
        r = M.validate(_tag(care="HYVENT® 2L SHELL"), year=y)
        assert _by_id(r["checks"])["M-D1"]["status"] == PASS, y
        assert r["provenance_hard_fail"] is False


def test_trap_late_hyvent_is_suggestive_only():
    """The HyVent mark was renewed in 2017 and never abandoned, and regional
    stock shipped later. Suggestive — never a conviction."""
    r = M.validate(_tag(care="HYVENT® 2L SHELL"), year=2019)
    c = _by_id(r["checks"])["M-D1"]
    assert c["status"] == FAIL
    assert c["severity"] != CRITICAL
    assert r["provenance_hard_fail"] is False


def test_trap_plastic_zipper_is_never_a_material_fail():
    """7.3 — the 1996 Retro Nuptse ships an exposed VISLON (molded plastic) CF
    zip, per TNF's own page. 'Metal real, plastic fake' is false."""
    r = M.validate(_tag(care="VISLON® CENTRE FRONT ZIP. MOLDED PLASTIC TEETH."))
    assert r["spec_hard_fail"] is False and r["provenance_hard_fail"] is False
    assert not [c for c in r["checks"] if c["status"] == FAIL]


def test_trap_absent_polartec_is_not_a_signal():
    """7.5 — current mainline Retro Denali carries no Polartec branding at all.
    Polartec present + era-plausible is confirmatory; absent is nothing."""
    r = M.validate(_tag(care="360 G/M2 RECYCLED POLYESTER FLEECE", family="DENALI"))
    assert _by_id(r["checks"])["M-D10"]["status"] == UNKNOWN
    assert not [c for c in r["checks"] if c["status"] == FAIL]


def test_trap_absent_velcro_branding_is_not_a_signal():
    """7.6 — TNF's own copy says 'hook-and-loop', never VELCRO(R)."""
    r = M.validate(_tag(care="HOOK-AND-LOOP CUFF TABS. MACHINE WASH."))
    assert not [c for c in r["checks"] if c["status"] == FAIL]


def test_trap_unreadable_tag_never_convicts():
    """Absence of evidence is not evidence — the rule the whole system runs on."""
    r = M.validate({})
    assert r["spec_hard_fail"] is False and r["provenance_hard_fail"] is False
    assert all(c["status"] == UNKNOWN for c in r["checks"])
    assert r["internal_coverage"] == 0.0


def test_trap_ordinary_genuine_tag_is_clean():
    r = M.validate(_tag(
        care="GORE-TEX® PRODUCT. MACHINE WASH WARM. TUMBLE DRY MEDIUM.",
        fiber="SHELL: 100% NYLON. LINING: 100% POLYESTER.",
        family="MOUNTAIN JACKET"), year=2022)
    assert r["spec_hard_fail"] is False and r["provenance_hard_fail"] is False
    assert not [c for c in r["checks"] if c["status"] == FAIL]


# ---------------------------------------------------------------------------
# Roll-up contract
# ---------------------------------------------------------------------------
def test_only_critical_fails_can_hard_fail():
    """A STRONG or SUPPORTING fail contributes but must never convict."""
    r = M.validate(_tag(care="GORE-TEX PRODUCT", family="RESOLVE JACKET"))
    assert [c for c in r["checks"] if c["status"] == FAIL]      # something failed
    assert r["spec_hard_fail"] is False                         # but nothing convicted


def test_internal_coverage_is_severity_weighted_and_bounded():
    thin = M.validate(_tag(care="MACHINE WASH COLD."))
    rich = M.validate(_tag(care="GORE-TEX® PRODUCT. POLARTEC® 200.",
                           fiber="FILLING: 80% DOWN 20% FEATHER",
                           family="MOUNTAIN JACKET"), year=2022)
    assert 0.0 <= thin["internal_coverage"] <= 1.0
    assert 0.0 <= rich["internal_coverage"] <= 1.0
    assert rich["internal_coverage"] > thin["internal_coverage"]


def test_unknown_checks_never_count_toward_coverage():
    r = M.validate({})
    assert r["counts"][UNKNOWN] == len(r["checks"])
    assert r["internal_coverage"] == 0.0


def test_summary_names_the_verdict_the_ladder_will_carry():
    """Spec is tried at rung 3b before provenance at 3c, so when both fire the
    summary must name the spec contradiction."""
    r = M.validate(_tag(care="GORE-TEX® AND DRYVENT™ SHELL"), year=2010)
    assert r["spec_hard_fail"] and r["provenance_hard_fail"]
    assert r["summary"].startswith("Specification contradiction")
