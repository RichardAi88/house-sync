"""Regrouping is not a purchase; stable item additions and credits stay signed."""

import csv
import json
from copy import deepcopy
from decimal import Decimal

import pytest

from housesync.changes import compare_revisions
from housesync.engine import build_house
from housesync.quotes import build_variation_quote
from housesync.rates import DEFAULT_LIBRARY_PATH, load_rate_library
from housesync.schema import HouseSettings

R1 = "ROOM_000000000001"
R2 = "ROOM_000000000002"


def room(identity, name, floor="Floor finish allowance"):
    return {"id": identity, "name": name, "finishes": {"floor": floor, "ceiling": "Ceiling allowance",
                                                       "skirting": "Skirting allowance", "paint": "Light blue (#ADD8E6)"}}


def row(code, amount, *, group="ROOM1", rate=10, trade="FLOOR_FINISH", source="PRIVATE_SOURCE", date="2026-11-17"):
    return {"code": code, "rate_code": trade, "description": "PRIVATE_BOQ_DESCRIPTION", "unit": "m2", "rate": rate,
            "quantity": float(Decimal(str(amount)) / Decimal(str(rate))), "amount": amount,
            "group_id": group, "group_name": "PRIVATE_GROUP", "rate_source": source,
            "rate_effective_date": date, "rate_origin": "library", "element_ids": []}


def snapshots(old_rows, new_rows, *, old_rooms=None, new_rooms=None, pricing=None):
    def snapshot(revision, rows, rooms):
        settings = HouseSettings().model_dump(mode="json")
        settings["openings"] = []
        if rooms and any(item["id"] != "ROOM1" for item in rooms):
            settings["partitions"] = [{"id": "P1", "start_mm": [4000, 0], "end_mm": [4000, 8000]}]
        return {"revision": revision, "settings": settings, "boq": rows, "rooms": rooms or [room("ROOM1", "Room 1")],
                "totals": {"currency": "AUD", "subtotal": float(sum(Decimal(str(item["amount"])) for item in rows))}}
    before = snapshot("a" * 12, old_rows, old_rooms)
    after = snapshot("b" * 12, new_rows, new_rooms)
    if pricing:
        after["settings"]["builder_pricing"].update(pricing)
    a, b = {item["code"]: item for item in old_rows}, {item["code"]: item for item in new_rows}
    items = [{"code": code, "cost_delta": float(Decimal(str(b.get(code, {}).get("amount", 0)))
                                                 - Decimal(str(a.get(code, {}).get("amount", 0)))),
              "description": "PRIVATE_COMPARISON_TEXT"}
             for code in dict.fromkeys([*a, *b]) if a.get(code) != b.get(code)]
    comparison = {"before_revision": before["revision"], "after_revision": after["revision"], "currency": "AUD", "items": items,
                  "totals": {"net_change": float(Decimal(str(after["totals"]["subtotal"])) - Decimal(str(before["totals"]["subtotal"])))}}
    return before, after, comparison


def quote(values):
    return build_variation_quote(*values, "2026-11-18")


@pytest.mark.parametrize("policy", ["at_cost", "including_margin"])
def test_same_finish_split_does_not_invent_purchases_or_admin(policy):
    values = snapshots([row("FLOOR_FINISH", 100)], [row(R1 + "_FLOOR_FINISH", 40, group=R1), row(R2 + "_FLOOR_FINISH", 60, group=R2)],
                       new_rooms=[room(R1, "Living"), room(R2, "Bedroom")], pricing={"credit_treatment": policy, "admin_fee_aud": 99})
    originals = deepcopy(values)
    result = quote(values)
    totals = result["public"]["totals"]
    assert totals["additions_at_cost"] == totals["credits_at_cost"] == totals["markup_amount"] == totals["admin_fee"] == 0
    assert totals["total_incl_gst"] == 0
    assert result["internal"]["regrouping_reconciliations"][0]["cost_delta"] == 0
    assert len(result["internal"]["boq_changes"]) == 3
    assert values == originals


@pytest.mark.parametrize(("policy", "markup", "total"), [("at_cost", 4, 4.4), ("including_margin", 0, 0)])
def test_stable_rooms_keep_offsetting_individual_movements(policy, markup, total):
    rooms = [room(R1, "Living"), room(R2, "Bedroom")]
    values = snapshots([row(R1 + "_FLOOR_FINISH", 100, group=R1), row(R2 + "_FLOOR_FINISH", 100, group=R2)],
                       [row(R1 + "_FLOOR_FINISH", 120, group=R1), row(R2 + "_FLOOR_FINISH", 80, group=R2)],
                       old_rooms=rooms, new_rooms=deepcopy(rooms), pricing={"credit_treatment": policy})
    result = quote(values)
    assert result["public"]["totals"]["additions_at_cost"] == 20
    assert result["public"]["totals"]["credits_at_cost"] == 20
    assert result["public"]["totals"]["markup_amount"] == markup
    assert result["public"]["totals"]["total_incl_gst"] == total
    assert result["internal"]["regrouping_reconciliations"] == []


@pytest.mark.parametrize(("rate", "expected_additions", "expected_total"), [(10, 0, 0), (12, 20, 26.4)])
def test_split_with_source_date_refresh_or_homogeneous_rate_change_prices_saved_delta(rate, expected_additions, expected_total):
    values = snapshots([row("FLOOR_FINISH", 100)],
                       [row(R1 + "_FLOOR_FINISH", rate * 4, group=R1, rate=rate, source="NEW_PRIVATE_SOURCE", date="2026-11-18"),
                        row(R2 + "_FLOOR_FINISH", rate * 6, group=R2, rate=rate, source="NEW_PRIVATE_SOURCE", date="2026-11-18")],
                       new_rooms=[room(R1, "Living"), room(R2, "Bedroom")])
    result = quote(values)
    totals = result["public"]["totals"]
    assert totals["additions_at_cost"] == expected_additions
    assert totals["credits_at_cost"] == 0
    assert totals["total_incl_gst"] == expected_total
    assert "PRIVATE" not in json.dumps(result["public"])
    evidence = result["internal"]["regrouping_reconciliations"][0]["source_lines"]
    assert {item["rate_source"] for item in evidence} == {"PRIVATE_SOURCE", "NEW_PRIVATE_SOURCE"}


def test_labels_do_not_price_replacements_but_distinct_item_codes_and_heterogeneous_rates_do():
    changed_material = snapshots([row("FLOOR_FINISH", 100)], [row(R1 + "_FLOOR_FINISH", 100, group=R1)],
                                 new_rooms=[room(R1, "Kitchen", floor="Tile")])
    totals = quote(changed_material)["public"]["totals"]
    assert totals["additions_at_cost"] == totals["credits_at_cost"] == totals["markup_amount"] == 0
    actual_replacement = snapshots([row("FLOOR_FINISH", 100)],
                                   [row("SELECTED_TILE_PRODUCT", 100, group=R1, trade="SELECTED_TILE_PRODUCT")],
                                   new_rooms=[room(R1, "Kitchen", floor="Tile")])
    totals = quote(actual_replacement)["public"]["totals"]
    assert totals["additions_at_cost"] == totals["credits_at_cost"] == 100
    assert totals["markup_amount"] == 20
    varied_rates = snapshots([row("FLOOR_FINISH", 100)],
                            [row(R1 + "_FLOOR_FINISH", 40, group=R1), row(R2 + "_FLOOR_FINISH", 60, group=R2, rate=20)],
                            new_rooms=[room(R1, "Living"), room(R2, "Bedroom")])
    totals = quote(varied_rates)["public"]["totals"]
    assert totals["additions_at_cost"] == totals["credits_at_cost"] == 60
    assert totals["markup_amount"] == 12


def test_stable_room_finish_description_only_change_keeps_zero_cost():
    values = snapshots([row(R1 + "_FLOOR_FINISH", 100, group=R1)], [row(R1 + "_FLOOR_FINISH", 100, group=R1)],
                       old_rooms=[room(R1, "Living")], new_rooms=[room(R1, "Family", floor="Oak")], pricing={"admin_fee_aud": 25})
    result = quote(values)
    assert result["public"]["totals"]["total_incl_gst"] == 0
    scope = " ".join(result["public"]["scope"])
    assert "Family" in scope and "Oak" in scope and "Rename" in scope
    assert R1 not in json.dumps(result["public"])


@pytest.fixture(scope="module")
def compiled_pair(tmp_path_factory):
    settings = HouseSettings()
    split = settings.model_dump(mode="json")
    split["partitions"] = [{"id": "P1", "start_mm": [4000, 0], "end_mm": [4000, 8000], "thickness_mm": 100,
                            "height_mm": 3000, "wall_type": "partition"}]
    split["openings"].append({"id": "D2", "kind": "door", "host": "P1", "offset_mm": 1500,
                              "width_mm": 1000, "height_mm": 2100, "sill_mm": 0})
    before = build_house(settings, tmp_path_factory.mktemp("quote-unsplit"))
    after = build_house(HouseSettings.model_validate(split), tmp_path_factory.mktemp("quote-split"))
    return before, after


@pytest.mark.parametrize(("policy", "expected"), [("at_cost", 4915.61), ("including_margin", 4894.71)])
def test_compiled_split_including_combined_paint_uses_only_actual_movements(compiled_pair, policy, expected):
    before, after = deepcopy(compiled_pair)
    after["settings"]["builder_pricing"]["credit_treatment"] = policy
    comparison = compare_revisions(before, after)
    result = build_variation_quote(before, after, comparison, "2026-11-18")
    totals = result["public"]["totals"]
    assert totals["at_cost_delta"] == 3708.12
    assert totals["additions_at_cost"] == 3803.12
    assert totals["credits_at_cost"] == 95
    assert totals["total_incl_gst"] == expected
    paint = next(item for item in result["internal"]["regrouping_reconciliations"] if item["rate_code"] == "PAINT")
    assert paint["cost_delta"] == 777.6
    assert "PAINT" in paint["source_codes"] and len(paint["source_codes"]) == 3
    assert len(result["internal"]["boq_changes"]) == len(comparison["items"])
    assert "internal partition" in " ".join(result["public"]["scope"])
    assert "ROOM_" not in json.dumps(result["public"])


@pytest.mark.parametrize(("policy", "expected"), [("at_cost", -4058.03), ("including_margin", -4894.71)])
def test_compiled_merge_preserves_signed_credit_policy(compiled_pair, policy, expected):
    after, before = deepcopy(compiled_pair)
    after["settings"]["builder_pricing"]["credit_treatment"] = policy
    result = build_variation_quote(before, after, compare_revisions(before, after), "2026-11-18")
    totals = result["public"]["totals"]
    assert totals["additions_at_cost"] == 95
    assert totals["credits_at_cost"] == 3803.12
    assert totals["at_cost_delta"] == -3708.12
    assert totals["total_incl_gst"] == expected


@pytest.mark.parametrize(("rate", "additions", "credits", "total"), [(65, 3803.12, 95, 4915.61), (70, 4181.52, 37.8, 5478.02)])
def test_compiled_split_and_csv_refresh_does_not_repurchase_unchanged_scope(compiled_pair, tmp_path, rate, additions, credits, total):
    before, split = compiled_pair
    with DEFAULT_LIBRARY_PATH.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        headers, rows = reader.fieldnames, list(reader)
    selected = next(row for row in rows if row["code"] == "FLOOR_FINISH")
    selected.update(rate=str(rate), source="PRIVATE_REFRESHED_SOURCE", effective_date="2026-11-18")
    path = tmp_path / "updated.csv"
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)
    after = build_house(HouseSettings.model_validate(split["settings"]), tmp_path / "rebuilt", rate_library=load_rate_library(path))
    result = build_variation_quote(before, after, compare_revisions(before, after), "2026-11-18")
    totals = result["public"]["totals"]
    assert totals["additions_at_cost"] == additions
    assert totals["credits_at_cost"] == credits
    assert totals["total_incl_gst"] == total
    assert "PRIVATE_REFRESHED_SOURCE" not in json.dumps(result["public"])
    assert "PRIVATE_REFRESHED_SOURCE" in json.dumps(result["internal"])


@pytest.mark.parametrize(("field", "selection"), [("floor", "Selected oak"), ("paint", "Warm white")])
def test_split_with_descriptive_finish_change_does_not_invent_a_price(compiled_pair, tmp_path, field, selection):
    before, split = compiled_pair
    settings = deepcopy(split["settings"])
    selected = split["rooms"][0]
    settings["rooms"] = [{"id": selected["id"], "name": "Study", "finishes": {field: selection}}]
    after = build_house(HouseSettings.model_validate(settings), tmp_path / field)
    result = build_variation_quote(before, after, compare_revisions(before, after), "2026-11-18")
    assert result["public"]["totals"]["additions_at_cost"] == 3803.12
    assert result["public"]["totals"]["credits_at_cost"] == 95
    assert result["public"]["totals"]["total_incl_gst"] == 4915.61
    assert selection in " ".join(result["public"]["scope"])
    assert "Study" in " ".join(result["public"]["scope"])
    assert "ROOM_" not in json.dumps(result["public"])
