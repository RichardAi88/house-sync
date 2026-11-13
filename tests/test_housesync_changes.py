"""Independent checks of recorded changes, costing and exported evidence."""
from __future__ import annotations

import csv
import json
from copy import deepcopy
from decimal import ROUND_HALF_UP, Decimal

import pytest
from openpyxl import load_workbook

from housesync.changes import compare_revisions, export_change_report


def money(value):
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def repriced(result, **updates):
    revised = deepcopy(result)
    revised["revision"] = "b" * 12
    revised["settings"]["rates"].update(updates)
    keys = {"FLOOR": "floor_m2", "WALL": "wall_m3", "ROOF": "roof_m2", "PAINT": "paint_m2", "DOOR": "opening_each", "WINDOW": "opening_each"}
    for item in revised["boq"]:
        item["rate"] = revised["settings"]["rates"][keys[item["code"]]]
        item["amount"] = money(Decimal(str(item["quantity"])) * Decimal(str(item["rate"])))
    revised["totals"]["subtotal"] = money(sum(Decimal(str(item["amount"])) for item in revised["boq"]))
    return revised


@pytest.fixture(scope="module")
def built_pair(tmp_path_factory):
    from housesync.engine import build_house
    from housesync.schema import HouseSettings

    root = tmp_path_factory.mktemp("change-geometry")
    return build_house(HouseSettings(), root / "a"), build_house(HouseSettings(length_mm=12000), root / "b")


def test_real_house_length_change_and_independent_cost_reconciliation(built_pair):
    before, after = built_pair
    result = compare_revisions(before, after)
    assert result["totals"] == {
        "before": 28574.8, "after": 33022.8, "additions": 4448.0, "omissions": 0.0,
        "net_change": 4448.0, "quantity_effect": 4448.0, "rate_effect": 0.0, "rounding_effect": 0.0,
    }
    assert {item["code"]: item["cost_delta"] for item in result["items"]} == {"FLOOR": 1280, "WALL": 720, "ROOF": 2160, "PAINT": 288}
    assert result["setting_changes"][0]["field"] == "length_mm"
    assert result["setting_changes"][0]["before"] == 10000
    assert result["setting_changes"][0]["after"] == 12000
    assert "10,000 mm to 12,000 mm" in result["summary"][0]
    assert result["has_changes"] is True
    json.dumps(result, allow_nan=False)


def test_finish_change_is_recorded_with_zero_cost(built_pair):
    before = built_pair[0]
    after = deepcopy(before)
    after["revision"] = "b" * 12
    after["settings"].update(paint_colour="Warm white", paint_hex="#FAF9F6")
    next(item for item in after["boq"] if item["code"] == "PAINT")["description"] = "Paint Warm white #FAF9F6"
    result = compare_revisions(before, after)
    assert result["has_changes"]
    assert result["totals"]["net_change"] == 0
    assert "Warm white" in " ".join(result["summary"])
    assert len(result["items"]) == 1
    assert result["items"][0]["status"] == "description_changed"
    assert result["items"][0]["cost_delta"] == 0


def test_rate_change_and_mixed_change_use_new_quantity_for_rate_effect(built_pair):
    before, longer = built_pair
    price_only = compare_revisions(before, repriced(before, paint_m2=15))
    assert price_only["totals"]["net_change"] == 628.2
    assert price_only["totals"]["quantity_effect"] == 0
    assert price_only["totals"]["rate_effect"] == 628.2
    mixed = compare_revisions(before, repriced(longer, paint_m2=15))
    assert mixed["totals"]["after"] == 33723
    assert mixed["totals"]["net_change"] == 5148.2
    assert mixed["totals"]["quantity_effect"] == 4448
    assert mixed["totals"]["rate_effect"] == 700.2
    assert mixed["totals"]["rounding_effect"] == 0
    reverse = compare_revisions(repriced(longer, paint_m2=15), before)
    assert reverse["totals"]["net_change"] == -5148.2
    assert reverse["totals"]["omissions"] == 5148.2
    assert sum(Decimal(str(reverse["totals"][key])) for key in ("quantity_effect", "rate_effect", "rounding_effect")) == Decimal("-5148.2")


def test_no_change_ignores_snapshot_hash_time_and_boq_order(built_pair):
    before = built_pair[0]
    assert compare_revisions(before, before)["has_changes"] is False
    reordered = deepcopy(before)
    reordered.update(revision="c" * 12, created_at="2026-11-12T12:00:00+00:00")
    reordered["boq"].reverse()
    for item in reordered["boq"]:
        item["element_ids"].reverse()
    result = compare_revisions(before, reordered)
    assert result["has_changes"] is False
    assert result["items"] == []
    assert result["totals"]["net_change"] == 0


def test_added_removed_scope_and_uncomparable_units(built_pair):
    before = built_pair[0]
    after = deepcopy(before)
    after["revision"] = "b" * 12
    after["boq"] = [item for item in after["boq"] if item["code"] != "WINDOW"]
    after["boq"].append({"code": "FENCE", "description": "New fence", "quantity": 2, "unit": "m", "rate": 50, "amount": 100, "element_ids": ["F1"]})
    after["totals"]["subtotal"] = 27674.8
    result = compare_revisions(before, after)
    assert result["totals"]["additions"] == 100
    assert result["totals"]["omissions"] == 1000
    assert result["totals"]["net_change"] == -900
    assert result["totals"]["quantity_effect"] == -900
    assert result["totals"]["rate_effect"] == 0
    assert {item["status"] for item in result["items"]} == {"added", "removed"}
    unit_change = deepcopy(before)
    unit_change["revision"] = "d" * 12
    unit_change["boq"][0]["unit"] = "m3"
    item = compare_revisions(before, unit_change)["items"][0]
    assert item["before_unit"] == "m2" and item["after_unit"] == "m3"
    assert item["status"] == "unit_changed"
    assert item["quantity_delta"] is None
    assert item["quantity_effect"] is None
    assert compare_revisions(before, unit_change)["totals"]["rate_effect"] is None


def test_rounding_adjustment_reconciles_saved_line_amounts():
    before = {"revision": "a" * 12, "settings": {"project_name": "Round test", "currency": "SGD"},
              "boq": [{"code": "X", "description": "Fine rate", "unit": "each", "quantity": 1, "rate": 0.005, "amount": 0.01}],
              "totals": {"currency": "SGD", "subtotal": 0.01}}
    after = deepcopy(before)
    after["revision"] = "b" * 12
    after["boq"][0]["quantity"] = 2
    result = compare_revisions(before, after)
    item = result["items"][0]
    assert item["cost_delta"] == 0
    assert item["quantity_effect"] == 0.01
    assert item["rounding_effect"] == -0.01
    assert result["totals"]["net_change"] == 0


@pytest.mark.parametrize("problem", ["duplicate", "subtotal", "amount", "currency", "currency_internal", "nan", "boolean"])
def test_invalid_or_inconsistent_snapshots_rejected(built_pair, problem):
    before = built_pair[0]
    after = deepcopy(before)
    after["revision"] = "b" * 12
    if problem == "duplicate":
        after["boq"].append(deepcopy(after["boq"][0]))
    elif problem == "subtotal":
        after["totals"]["subtotal"] += 1
    elif problem == "amount":
        after["boq"][0]["amount"] += 1
    elif problem == "currency":
        after["settings"]["currency"] = after["totals"]["currency"] = "MYR"
    elif problem == "currency_internal":
        after["settings"]["currency"] = "MYR"
    else:
        after["boq"][0]["quantity"] = float("nan") if problem == "nan" else True
    with pytest.raises(ValueError):
        compare_revisions(before, after)


def test_all_report_formats_preserve_pair_changes_and_costs(tmp_path, built_pair):
    result = compare_revisions(*built_pair)
    files = export_change_report(result, tmp_path)
    assert set(files) == {"changes_xlsx", "changes_csv", "changes_pdf", "changes_json"}
    assert all((tmp_path / path).stat().st_size > 100 for path in files.values())
    saved = json.loads((tmp_path / files["changes_json"]).read_text(encoding="utf-8"))
    assert saved["totals"]["net_change"] == 4448
    assert saved["before_revision"] == result["before_revision"]
    assert saved["after_revision"] == result["after_revision"]
    with (tmp_path / files["changes_csv"]).open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream))
    assert rows[0][1] == result["before_revision"]
    assert rows[0][3] == result["after_revision"]
    assert rows[1][-1] == "4448.0"
    workbook = load_workbook(tmp_path / files["changes_xlsx"], data_only=False)
    sheet = workbook.active
    assert result["before_revision"] in sheet["A2"].value
    assert result["after_revision"] in sheet["A3"].value
    assert sheet["K7"].value == 4448
    rows = [row for row in sheet if row[0].value == "FLOOR"]
    assert len(rows) == 1
    row = rows[0][0].row
    assert sheet.cell(row, 10).value == f"=ROUND(I{row}-H{row},2)"
    assert (tmp_path / files["changes_pdf"]).read_bytes().startswith(b"%PDF-")


def test_report_treats_arbitrary_text_as_text_and_nochange_is_exportable(tmp_path, built_pair):
    before, after = deepcopy(built_pair[0]), deepcopy(built_pair[1])
    before["boq"][0]["code"] = after["boq"][0]["code"] = "=1+1"
    comparison = compare_revisions(before, after)
    export_change_report(comparison, tmp_path / "changed")
    sheet = load_workbook(tmp_path / "changed" / "changes.xlsx").active
    assert any(cell.value == "'=1+1" and cell.data_type == "s" for cell in sheet["A"])
    export_change_report(compare_revisions(before, before), tmp_path / "unchanged")
    assert (tmp_path / "unchanged" / "changes.pdf").stat().st_size > 100
