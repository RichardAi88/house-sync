"""Validated commercial inputs must remain separate from geometry and BOQ rates."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

import pytest
from pydantic import ValidationError

from housesync.schema import AllowanceSettings, BuilderPricing, HouseSettings


def allowance(**updates):
    return {"id": "AL1", "kind": "provisional_sum", "description": "Site preparation allowance",
            "amount": 1200.50, "note": "Scope to be confirmed", **updates}


def test_legacy_design_receives_independent_quote_defaults():
    legacy = {"project_name": "Saved design", "length_mm": 10000, "width_mm": 8000,
              "currency": "SGD", "rates": {"floor_m2": 80, "wall_m3": 300, "roof_m2": 120,
                                           "paint_m2": 12, "opening_each": 500}}
    original = deepcopy(legacy)
    first = HouseSettings.model_validate(legacy)
    second = HouseSettings.model_validate(legacy)
    assert legacy == original
    assert first.currency == "SGD"
    assert first.rate_overrides["FLOOR"] == 80
    assert first.builder_pricing.model_dump(mode="json") == {
        "overhead_margin_percent": 20.0, "admin_fee_aud": 0.0, "gst_percent": 10,
        "credit_treatment": "at_cost", "validity_days": 14, "builder_name": "", "client_description": "",
    }
    assert first.allowances == []
    first.builder_pricing.admin_fee_aud = 25.50
    first.allowances.append(AllowanceSettings.model_validate(allowance()))
    assert second.builder_pricing.admin_fee_aud == 0
    assert second.allowances == []


def test_quote_settings_roundtrip_into_canonical_revision_inputs():
    settings = HouseSettings.model_validate({
        "builder_pricing": {"overhead_margin_percent": 25.5, "admin_fee_aud": "75.25", "gst_percent": 10,
                            "credit_treatment": "including_margin", "validity_days": 30,
                            "builder_name": "  Example Builder  ", "client_description": "Extend living room.\r\nUpdate finishes."},
        "allowances": [allowance(), allowance(id="AL2", kind="prime_cost", description="Tapware", amount="500.00")],
    })
    canonical = settings.model_dump(mode="json")
    assert canonical["builder_pricing"]["builder_name"] == "Example Builder"
    assert canonical["builder_pricing"]["client_description"] == "Extend living room.\nUpdate finishes."
    assert canonical["builder_pricing"]["admin_fee_aud"] == 75.25
    assert canonical["allowances"][1]["amount"] == 500
    assert HouseSettings.model_validate_json(settings.model_dump_json()) == settings
    assert isinstance(canonical["allowances"][0]["amount"], (float, int))


def test_blank_builder_name_and_scope_keep_placeholder_semantics():
    pricing = BuilderPricing(builder_name="  ", client_description="\r\n  ")
    assert pricing.builder_name == pricing.client_description == ""


def test_multiline_allowance_descriptions_and_notes_are_plain_text():
    item = AllowanceSettings.model_validate(allowance(description=" Earthworks\r\nRock excluded ", note=" Pending survey.\nConfirm scope. "))
    assert item.description == "Earthworks\nRock excluded"
    assert item.note == "Pending survey.\nConfirm scope."
    # Markup-looking characters are stored as literal text for exporters to escape.
    assert AllowanceSettings.model_validate(allowance(description="<Client-selected finish>")).description == "<Client-selected finish>"


def test_markup_is_single_percentage_on_cost_and_allows_more_than_100_percent():
    assert BuilderPricing(overhead_margin_percent=150).overhead_margin_percent == 150
    assert BuilderPricing(overhead_margin_percent=0).overhead_margin_percent == 0
    assert BuilderPricing(overhead_margin_percent=1000).overhead_margin_percent == 1000


@pytest.mark.parametrize("updates", [
    {"overhead_margin_percent": -1}, {"overhead_margin_percent": 1000.01},
    {"overhead_margin_percent": float("nan")}, {"overhead_margin_percent": float("inf")},
    {"overhead_margin_percent": True},
    {"admin_fee_aud": -1}, {"admin_fee_aud": .001}, {"admin_fee_aud": "1.999"},
    {"admin_fee_aud": float("nan")}, {"admin_fee_aud": float("inf")}, {"admin_fee_aud": True},
    {"admin_fee_aud": 1_000_000.01},
    {"gst_percent": 0}, {"gst_percent": 9}, {"gst_percent": 11}, {"gst_percent": True},
    {"credit_treatment": "ignore_credits"},
    {"validity_days": 0}, {"validity_days": 366}, {"validity_days": 14.5}, {"validity_days": True},
    {"builder_name": "Builder\nPty Ltd"}, {"builder_name": "Builder\tName"},
    {"builder_name": "Builder\x7f"}, {"builder_name": "B" * 121},
    {"client_description": "Bad\x00scope"}, {"client_description": "Bad\tscope"},
    {"client_description": "s" * 2001}, {"client_description": None},
    {"unrecognised_fee": 50},
])
def test_builder_pricing_rejects_invalid_or_ambiguous_inputs(updates):
    with pytest.raises(ValidationError):
        HouseSettings(builder_pricing=updates)


@pytest.mark.parametrize("updates", [
    {"id": "AL0"}, {"id": "AL01"}, {"id": "AL1000000"}, {"id": "../AL1"}, {"id": "AL1/file"},
    {"kind": "quote"}, {"amount": -1}, {"amount": .001}, {"amount": float("nan")},
    {"amount": float("inf")}, {"amount": True}, {"amount": 10_000_000.01},
    {"description": " "}, {"description": "x" * 241}, {"description": "bad\tlabel"},
    {"note": "x" * 1001}, {"note": "bad\x1bnote"}, {"note": None}, {"supplier_quote": "invented"},
])
def test_allowances_reject_invalid_identifiers_amounts_and_labels(updates):
    with pytest.raises(ValidationError):
        HouseSettings(allowances=[allowance(**updates)])


def test_money_accepts_exact_cents_and_zero_without_silent_rounding():
    settings = HouseSettings(builder_pricing={"admin_fee_aud": Decimal("0.10")},
                             allowances=[allowance(amount=0), allowance(id="AL2", amount=Decimal("999.99"))])
    assert settings.builder_pricing.admin_fee_aud == .1
    assert settings.allowances[0].amount == 0
    assert settings.allowances[1].amount == 999.99
    with pytest.raises(ValidationError, match="whole cents"):
        HouseSettings(allowances=[allowance(amount=Decimal("999.999"))])


def test_allowance_identity_is_unique_and_count_is_bounded():
    with pytest.raises(ValidationError, match="unique"):
        HouseSettings(allowances=[allowance(), allowance()])
    assert len(HouseSettings(allowances=[allowance(id=f"AL{index}") for index in range(1, 101)]).allowances) == 100
    with pytest.raises(ValidationError):
        HouseSettings(allowances=[allowance(id=f"AL{index}") for index in range(1, 102)])


def test_assignment_validation_and_nested_input_copies():
    source = {"builder_pricing": {"admin_fee_aud": 50}, "allowances": [allowance()]}
    settings = HouseSettings.model_validate(source)
    source["allowances"][0]["amount"] = 1
    source["builder_pricing"]["admin_fee_aud"] = 2
    assert settings.allowances[0].amount == 1200.50
    assert settings.builder_pricing.admin_fee_aud == 50
    with pytest.raises(ValidationError):
        settings.builder_pricing.admin_fee_aud = 1.234
    with pytest.raises(ValidationError):
        settings.allowances[0].amount = -1
    with pytest.raises(ValidationError):
        settings.allowances = [allowance(), allowance()]


def test_pricing_and_allowance_edits_change_revision_identity():
    from housesync.engine import _revision

    baseline = HouseSettings()
    assert _revision(baseline) != _revision(HouseSettings(builder_pricing={"admin_fee_aud": 50}))
    assert _revision(baseline) != _revision(HouseSettings(allowances=[allowance()]))
    assert _revision(baseline) == _revision(HouseSettings.model_validate(baseline.model_dump(mode="json")))


def test_omitted_quote_defaults_do_not_invent_historical_changes():
    from housesync.changes import compare_revisions

    explicit = HouseSettings().model_dump(mode="json")
    implicit = deepcopy(explicit)
    implicit.pop("builder_pricing")
    implicit.pop("allowances")
    boq = [{"code": "FLOOR", "description": "Floor slab", "quantity": 80, "unit": "m2", "rate": 95,
            "amount": 7600, "element_ids": ["S0"]}]
    before = {"revision": "a" * 12, "settings": implicit, "boq": deepcopy(boq), "totals": {"currency": "AUD", "subtotal": 7600}}
    after = {"revision": "b" * 12, "settings": explicit, "boq": deepcopy(boq), "totals": {"currency": "AUD", "subtotal": 7600}}
    report = compare_revisions(before, after)
    assert report["setting_changes"] == []
    assert report["items"] == []
    assert not report["has_changes"]
