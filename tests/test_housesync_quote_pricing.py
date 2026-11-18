"""Independent signed pricing, allowance and client-payload privacy checks."""

import json
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal

import pytest

from housesync.changes import compare_revisions
from housesync.quotes import QuoteUnavailableError, build_variation_quote, diff_allowances
from housesync.schema import default_openings


def pair(before_amounts=None, after_amounts=None, *, pricing=None, before_allowances=None, after_allowances=None):
    def snapshot(revision, amounts):
        rows = [{"code": code, "description": f"PRIVATE_BOQ_DESCRIPTION_{code}", "quantity": 1, "unit": "each",
                 "rate": amount, "amount": amount, "element_ids": [], "rate_source": "PRIVATE_RATE_SOURCE",
                 "library_rate": amount, "rate_origin": "override"} for code, amount in amounts.items()]
        return {"revision": revision, "settings": {"project_name": "Quote arithmetic house", "currency": "AUD",
                                                    "length_mm": 10000, "width_mm": 8000, "paint_colour": "Light blue"},
                "boq": rows, "totals": {"currency": "AUD", "subtotal": float(sum(Decimal(str(row["amount"])) for row in rows))},
                "rate_library": {"sha256": "PRIVATE_LIBRARY_HASH", "rows": {"FLOOR": {"notes": "PRIVATE_LIBRARY_NOTE"}}}}

    before = snapshot("a" * 12, before_amounts or {})
    after = snapshot("b" * 12, after_amounts or {})
    if pricing is not None:
        after["settings"]["builder_pricing"] = pricing
    if before_allowances is not None:
        before["settings"]["allowances"] = before_allowances
    if after_allowances is not None:
        after["settings"]["allowances"] = after_allowances
    old, new = {r["code"]: r for r in before["boq"]}, {r["code"]: r for r in after["boq"]}
    items = [{"code": code, "cost_delta": float(Decimal(str(new.get(code, {}).get("amount", 0))) - Decimal(str(old.get(code, {}).get("amount", 0)))),
              "description": "PRIVATE_COMPARISON_RATE_BREAKDOWN"} for code in dict.fromkeys([*old, *new])
             if old.get(code, {}).get("amount") != new.get(code, {}).get("amount")]
    comparison = {"before_revision": before["revision"], "after_revision": after["revision"], "currency": "AUD",
                  "items": items, "summary": ["PRIVATE_COMPARISON_SUMMARY"],
                  "totals": {"net_change": float(Decimal(str(after["totals"]["subtotal"])) - Decimal(str(before["totals"]["subtotal"])))}}
    return before, after, comparison


def quote(*args, **kwargs):
    return build_variation_quote(*pair(*args, **kwargs), issued_on=date(2026, 11, 18))


def allowance(identity="AL1", kind="provisional_sum", amount=100, **updates):
    return {"id": identity, "kind": kind, "description": "Selected allowance", "amount": amount, **updates}


def test_default_twenty_percent_is_one_cost_markup_and_ten_percent_gst():
    result = quote({"FLOOR": 100}, {"FLOOR": 8410})
    totals = result["public"]["totals"]
    assert totals["at_cost_delta"] == totals["additions_at_cost"] == 8310
    assert totals["credits_at_cost"] == 0
    assert totals["markup_amount"] == 1662
    assert totals["subtotal_ex_gst"] == 9972
    assert totals["gst_rate"] == 10
    assert totals["gst_amount"] == 997.2
    assert totals["total_incl_gst"] == 10969.2
    assert result["public"]["markup_label"] == "Combined overhead and margin (markup on cost)"
    assert result["public"]["issued_on"] == "2026-11-18"
    assert result["public"]["valid_until"] == "2026-12-02"


def test_explicit_admin_fee_is_added_once_without_markup():
    totals = quote({"FLOOR": 100}, {"FLOOR": 8410}, pricing={"admin_fee_aud": 150})["public"]["totals"]
    assert totals["markup_amount"] == 1662
    assert totals["admin_fee"] == 150
    assert totals["subtotal_ex_gst"] == 10122
    assert totals["gst_amount"] == 1012.2
    assert totals["total_incl_gst"] == 11134.2


@pytest.mark.parametrize(("treatment", "expected"), [
    ("at_cost", {"credits_quote": 100, "markup_amount": 0, "subtotal_ex_gst": -90, "gst_amount": -9, "total_incl_gst": -99}),
    ("including_margin", {"credits_quote": 120, "markup_amount": -20, "subtotal_ex_gst": -110, "gst_amount": -11, "total_incl_gst": -121}),
])
def test_credit_only_uses_selected_credit_policy_and_signed_gst(treatment, expected):
    totals = quote({"WALL": 200}, {"WALL": 100}, pricing={"credit_treatment": treatment, "admin_fee_aud": 10})["public"]["totals"]
    assert totals["additions_at_cost"] == 0
    assert totals["credits_at_cost"] == 100
    assert totals["admin_fee"] == 10
    for key, value in expected.items():
        assert totals[key] == value


def test_fixed_admin_can_reduce_or_exceed_a_credit_when_explicitly_requested():
    totals = quote({"FLOOR": 20}, {"FLOOR": 10}, pricing={"admin_fee_aud": 25})["public"]["totals"]
    assert totals["credits_at_cost"] == 10
    assert totals["subtotal_ex_gst"] == 15
    assert totals["gst_amount"] == 1.5
    assert totals["total_incl_gst"] == 16.5


@pytest.mark.parametrize(("treatment", "markup", "ex_gst", "inc_gst"), [("at_cost", 20, 27.5, 30.25), ("including_margin", 0, 7.5, 8.25)])
def test_equal_additions_and_omissions_keep_separate_bases_and_apply_admin(treatment, markup, ex_gst, inc_gst):
    totals = quote({"FLOOR": 100, "WALL": 100}, {"FLOOR": 200, "WALL": 0},
                   pricing={"credit_treatment": treatment, "admin_fee_aud": 7.5})["public"]["totals"]
    assert totals["additions_at_cost"] == totals["credits_at_cost"] == 100
    assert totals["at_cost_delta"] == 0
    assert totals["markup_amount"] == markup
    assert totals["admin_fee"] == 7.5
    assert totals["subtotal_ex_gst"] == ex_gst
    assert totals["total_incl_gst"] == inc_gst


@pytest.mark.parametrize("editorial", [False, True])
def test_identical_and_editorial_only_changes_never_charge_configured_admin(editorial):
    before, after, comparison = pair({"PAINT": 100}, {"PAINT": 100}, pricing={"admin_fee_aud": 99})
    if editorial:
        after["settings"]["paint_colour"] = "Warm white"
        comparison["items"] = [{"code": "PAINT", "cost_delta": 0, "description": "PRIVATE_COLOUR_COST_NOTE"}]
    result = build_variation_quote(before, after, comparison, "2026-11-18")
    assert result["public"]["totals"]["admin_fee"] == 0
    assert result["public"]["totals"]["total_incl_gst"] == 0
    assert result["public"]["status"] == "draft"
    assert not result["internal"]["admin_fee_applied"]
    if editorial:
        assert "Warm white" in " ".join(result["public"]["scope"])


def test_markup_rounds_once_per_aggregate_base_and_gst_half_up():
    totals = quote({}, {"ONE": .03, "TWO": .03})["public"]["totals"]
    assert totals["additions_at_cost"] == .06
    assert totals["additions_markup"] == .01  # Not .01 on each tiny line.
    assert totals["subtotal_ex_gst"] == .07
    assert totals["gst_amount"] == .01
    assert totals["total_incl_gst"] == .08


@pytest.mark.parametrize("sign", [-1, 1])
def test_signed_gst_half_cent_rounds_away_from_zero(sign):
    before, after = ({"ITEM": .05}, {}) if sign < 0 else ({}, {"ITEM": .05})
    totals = quote(before, after, pricing={"overhead_margin_percent": 0})["public"]["totals"]
    assert totals["subtotal_ex_gst"] == sign * .05
    assert totals["gst_amount"] == sign * .01
    assert totals["total_incl_gst"] == sign * .06


def test_allowance_only_movement_prices_delta_and_keeps_pc_ps_separate():
    result = quote({}, {}, before_allowances=[allowance(amount=500)],
                   after_allowances=[allowance(amount=300), allowance("AL2", "prime_cost", 100, note="Selected appliance allowance")])
    public = result["public"]
    assert public["ps_allowances"][0]["before"] == 500
    assert public["ps_allowances"][0]["after"] == 300
    assert public["ps_allowances"][0]["movement"] == -200
    assert public["pc_allowances"][0]["movement"] == 100
    assert public["totals"]["at_cost_delta"] == -100
    assert public["totals"]["markup_amount"] == 20
    assert public["totals"]["total_incl_gst"] == -88
    assert result["internal"]["totals"]["boq_cost_delta"] == 0
    assert result["internal"]["totals"]["allowance_cost_delta"] == -100


def test_removed_allowance_is_a_credit_with_optional_markup_return():
    result = quote({}, {}, pricing={"credit_treatment": "including_margin"},
                   before_allowances=[allowance(kind="prime_cost", amount=250)], after_allowances=[])
    assert result["public"]["pc_allowances"][0]["movement"] == -250
    assert result["public"]["totals"]["credits_quote"] == 300
    assert result["public"]["totals"]["total_incl_gst"] == -330


def test_allowance_kind_transfer_is_visible_in_both_groups_without_billable_markup_or_fee():
    result = quote({}, {}, pricing={"admin_fee_aud": 99}, before_allowances=[allowance(amount=100)],
                   after_allowances=[allowance(kind="prime_cost", amount=100)])
    assert result["public"]["ps_allowances"][0]["movement"] == -100
    assert result["public"]["pc_allowances"][0]["movement"] == 100
    assert "Reclassified" in result["public"]["pc_allowances"][0]["notes"]
    assert result["public"]["totals"]["additions_at_cost"] == 0
    assert result["public"]["totals"]["credits_at_cost"] == 0
    assert result["public"]["totals"]["admin_fee"] == result["public"]["totals"]["total_incl_gst"] == 0


def test_reclassification_with_value_increase_prices_only_the_net_increase():
    result = quote({}, {}, before_allowances=[allowance(amount=100)], after_allowances=[allowance(kind="prime_cost", amount=150)])
    assert result["public"]["totals"]["additions_at_cost"] == 50
    assert result["public"]["totals"]["additions_markup"] == 10
    assert result["public"]["totals"]["total_incl_gst"] == 66
    assert sum(row["movement"] for key in ("pc_allowances", "ps_allowances") for row in result["public"][key]) == 50


def test_allowance_editorial_changes_and_missing_note_defaults_do_not_create_fees():
    old = allowance(amount=100)
    assert diff_allowances([old], [{**old, "note": ""}]) == []
    new = {**old, "description": "Updated appliance selection", "note": "Selection remains within allowance"}
    result = quote({}, {}, pricing={"admin_fee_aud": 75}, before_allowances=[old], after_allowances=[new])
    assert result["internal"]["allowance_changes"][0]["cost_delta"] == 0
    assert result["public"]["totals"]["total_incl_gst"] == 0
    assert result["public"]["ps_allowances"][0]["notes"] == new["note"]
    assert diff_allowances([old, allowance("AL2")], [allowance("AL2"), old]) == []


def test_only_after_pricing_policy_applies_and_validity_uses_calendar_days():
    before, after, comparison = pair({}, {"ITEM": 100}, pricing={"overhead_margin_percent": 25, "validity_days": 14})
    before["settings"]["builder_pricing"] = {"overhead_margin_percent": 99}
    result = build_variation_quote(before, after, comparison, date(2026, 12, 25))
    assert result["public"]["totals"]["markup_amount"] == 25
    assert result["public"]["valid_until"] == "2027-01-08"


def test_public_projection_uses_client_scope_and_never_copies_private_item_data():
    before, after, comparison = pair({"FLOOR": 100}, {"FLOOR": 200}, pricing={"builder_name": "Example Builder", "client_description": "Extend the living space.\nMatch the agreed finish."})
    original = deepcopy((before, after, comparison))
    result = build_variation_quote(before, after, comparison, "2026-11-18")
    serialized = json.dumps(result["public"], allow_nan=False)
    assert "PRIVATE_" not in serialized
    assert "rate_source" not in serialized
    assert "rate_origin" not in serialized
    assert "boq_changes" not in serialized
    assert result["public"]["scope"] == [after["settings"]["builder_pricing"]["client_description"]]
    assert result["public"]["builder_name"] == "Example Builder"
    assert "PRIVATE_BOQ_DESCRIPTION" in json.dumps(result["internal"])
    assert (before, after, comparison) == original


def test_fallback_scope_is_plain_geometry_without_comparison_cost_narrative():
    before, after, comparison = pair({"FLOOR": 100}, {"FLOOR": 200})
    after["settings"]["length_mm"] = 12000
    result = build_variation_quote(before, after, comparison, "2026-11-18")
    assert result["public"]["scope"] == ["Change the house internal length from 10 m to 12 m."]
    assert "PRIVATE_" not in json.dumps(result["public"])


def test_fallback_treats_omitted_legacy_opening_and_paint_fields_as_canonical_defaults():
    before, after, comparison = pair({"FLOOR": 100}, {"FLOOR": 200})
    before["settings"].pop("paint_colour")
    after["settings"].update(paint_hex="#ADD8E6", paint_coats=2,
                             openings=[opening.model_dump(mode="json") for opening in default_openings()])
    result = build_variation_quote(before, after, comparison, "2026-11-18")
    scope = " ".join(result["public"]["scope"])
    assert scope == "Adjust the agreed pricing for the selected variation scope."
    assert "door" not in scope.lower() and "window" not in scope.lower() and "paint" not in scope.lower()


def test_known_illustrative_applied_prices_add_only_a_generic_public_qualification():
    before, after, comparison = pair({"FLOOR": 100}, {"FLOOR": 200})
    after["boq"][0]["rate_source"] = "Illustrative PRIVATE_SUPPLIER_REFERENCE"
    public = build_variation_quote(before, after, comparison, "2026-11-18")["public"]
    assert "This draft includes illustrative rates; confirm project pricing before acceptance." in public["notes"]
    assert "PRIVATE_" not in json.dumps(public)
    assert not any("category transfers" in note for note in public["notes"])


def test_unused_illustrative_reference_library_does_not_qualify_real_applied_override():
    before, after, comparison = pair({"FLOOR": 100}, {"FLOOR": 200})
    for snapshot in (before, after):
        snapshot["boq"][0]["rate_source"] = "Project allowance agreed in quotation"
        snapshot["rate_library"]["rows"]["FLOOR"]["source"] = "illustrative"
    public = build_variation_quote(before, after, comparison, "2026-11-18")["public"]
    assert not any("illustrative" in note for note in public["notes"])


def test_non_aud_ordinary_comparison_still_works_but_client_quote_is_refused():
    before, after, _ = pair({"FLOOR": 100}, {"FLOOR": 200})
    for saved in (before, after):
        saved["settings"]["currency"] = saved["totals"]["currency"] = "SGD"
    comparison = compare_revisions(before, after)
    assert comparison["totals"]["net_change"] == 100
    with pytest.raises(QuoteUnavailableError, match="AUD"):
        build_variation_quote(before, after, comparison, "2026-11-18")


@pytest.mark.parametrize("issued", [None, True, datetime(2026, 11, 18), "18/11/2026", "2026-1-2", "2026-02-30"])
def test_issue_date_is_explicit_and_validated(issued):
    with pytest.raises(ValueError):
        build_variation_quote(*pair({}, {"ITEM": 100}), issued_on=issued)


@pytest.mark.parametrize("mutation", ["revision", "item_delta", "total", "missing_item", "saved_subtotal", "duplicate_allowance"])
def test_mismatched_saved_comparison_or_allowances_are_refused(mutation):
    before, after, comparison = pair({"FLOOR": 100}, {"FLOOR": 200})
    if mutation == "revision":
        comparison["before_revision"] = "wrong"
    elif mutation == "item_delta":
        comparison["items"][0]["cost_delta"] = 999
    elif mutation == "total":
        comparison["totals"]["net_change"] = 999
    elif mutation == "missing_item":
        comparison["items"] = []
    elif mutation == "saved_subtotal":
        before["totals"]["subtotal"] = 999
    else:
        after["settings"]["allowances"] = [allowance(), allowance()]
    with pytest.raises(ValueError):
        build_variation_quote(before, after, comparison, "2026-11-18")
