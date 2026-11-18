"""Stable room/partition revision descriptions and grouped-line rate evidence."""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

import pytest
from reportlab import rl_config

from housesync.changes import compare_revisions, export_change_report
from housesync.rates import load_rate_library
from housesync.schema import HouseSettings

ROOM_A = "ROOM_A123456789AB"
ROOM_B = "ROOM_B123456789AB"


def snapshot():
    library = load_rate_library().snapshot()
    return {
        "revision": "a" * 12,
        "settings": {**HouseSettings().model_dump(mode="json"), "openings": []},
        "boq": [{"code": "FLOOR_FINISH", "description": "Floor finish allowance", "quantity": 10, "unit": "m2",
                 "rate": 65, "amount": 650, "element_ids": ["S0"], "rate_origin": "library",
                 "rate_source": "illustrative", "rate_effective_date": "2026-11-17"}],
        "rate_library": library,
        "totals": {"currency": "AUD", "subtotal": 650},
    }


def partition(**edits):
    return {"id": "P1", "start_mm": [4000, 0], "end_mm": [4000, 8000],
            "thickness_mm": 100, "height_mm": 3000, "wall_type": "partition", **edits}


def room(identity=ROOM_A, name="Living", **edits):
    return {"id": identity, "name": name, "area_m2": 31.6, "perimeter_m": 23.9,
            "outline_mm": [[0, 0], [3950, 0], [3950, 8000], [0, 8000]], "holes_mm": [],
            "finishes": {"floor": "Floor finish allowance", "ceiling": "Ceiling allowance",
                         "skirting": "Skirting allowance", "paint": "Light blue (#ADD8E6)"}, **edits}


def changed(before):
    after = deepcopy(before)
    after["revision"] = "b" * 12
    return after


def grouped_snapshot():
    result = snapshot()
    result["settings"]["partitions"] = [partition()]
    result["rooms"] = [room()]
    result["boq"][0].update(code=f"{ROOM_A}_FLOOR_FINISH", rate_code="FLOOR_FINISH", group_id=ROOM_A, group_name="Living")
    return result


def test_partition_addition_and_removal_are_described_by_id_without_mutating_snapshots():
    before = snapshot()
    after = changed(before)
    after["settings"]["partitions"] = [partition()]
    original_a, original_b = deepcopy(before), deepcopy(after)
    result = compare_revisions(before, after)
    change = next(item for item in result["design_changes"] if item["category"] == "partitions")
    assert change["element_id"] == "P1" and change["status"] == "added"
    assert "Added Partition P1" in change["description"]
    assert "(4,000, 0) mm" in change["description"] and "100 mm thick" in change["description"]
    reverse = compare_revisions(after, before)
    assert any("Removed Partition P1" in text for text in reverse["summary"])
    assert result["totals"]["net_change"] == 0
    assert before == original_a and after == original_b


@pytest.mark.parametrize("field,value,text", [
    ("start_mm", [4500, 0], "start"), ("end_mm", [4000, 7000], "end"),
    ("thickness_mm", 125, "thickness"), ("height_mm", 2700, "height"), ("wall_type", "Acoustic partition", "wall type"),
])
def test_partition_move_resize_and_type_have_readable_stable_id_fields(field, value, text):
    before = snapshot()
    before["settings"]["partitions"] = [partition()]
    after = changed(before)
    after["settings"]["partitions"][0][field] = value
    result = compare_revisions(before, after)
    changes = [item for item in result["setting_changes"] if item["category"] == "partitions"]
    assert [item["field"] for item in changes] == [f"partitions.P1.{field}"]
    assert f"Partition P1 {text}:" in changes[0]["description"]
    assert result["totals"]["net_change"] == 0


def test_room_rename_and_refinish_use_resolved_stable_ids_and_group_names():
    before = grouped_snapshot()
    after = changed(before)
    after["settings"]["rooms"] = [{"id": ROOM_A, "name": "Study", "finishes": {"floor": "Timber boards"}}]
    after["rooms"][0].update(name="Study", finishes={**after["rooms"][0]["finishes"], "floor": "Timber boards"})
    after["boq"][0]["group_name"] = "Study"
    result = compare_revisions(before, after)
    assert {item["field"] for item in result["setting_changes"]} == {f"rooms.{ROOM_A}.name", f"rooms.{ROOM_A}.finishes.floor"}
    assert all(item["element_id"] == ROOM_A for item in result["setting_changes"])
    assert any(f"Room {ROOM_A} name: Living to Study" in text for text in result["summary"])
    assert any("Floor finish allowance to Timber boards" in text for text in result["summary"])
    assert result["items"][0]["group_id"] == ROOM_A
    assert result["items"][0]["before_group_name"] == "Living" and result["items"][0]["after_group_name"] == "Study"
    assert result["totals"]["net_change"] == 0


def test_room_geometry_changes_and_added_removed_rooms_are_explicit():
    before = grouped_snapshot()
    after = changed(before)
    after["rooms"][0].update(area_m2=35.6, perimeter_m=24.9, outline_mm=[[0, 0], [4450, 0], [4450, 8000], [0, 8000]])
    after["rooms"].append(room(ROOM_B, "Bedroom"))
    result = compare_revisions(before, after)
    fields = {item["field"] for item in result["design_changes"]}
    assert {f"rooms.{ROOM_A}.area_m2", f"rooms.{ROOM_A}.perimeter_m", f"rooms.{ROOM_A}.outline_mm", f"rooms.{ROOM_B}"} <= fields
    assert any(f"Added room {ROOM_B} (Bedroom)" in text for text in result["summary"])
    assert any(f"Removed room {ROOM_B} (Bedroom)" in text for text in compare_revisions(after, before)["summary"])


def test_legacy_single_room_defaults_and_new_presentation_fields_do_not_invent_changes():
    before = snapshot()
    for field in ("partitions", "rooms", "min_room_width_mm"):
        before["settings"].pop(field, None)
    after = changed(before)
    after["settings"].update(partitions=[], rooms=[], min_room_width_mm=900)
    after["rooms"] = [room("ROOM1", "Room 1", area_m2=80)]
    after["boq"][0].update(rate_code="FLOOR_FINISH", group_id="ROOM1", group_name="Room 1")
    result = compare_revisions(before, after)
    assert result["setting_changes"] == result["items"] == []
    assert not result["has_changes"]


def test_legacy_room_rename_is_visible_while_explicit_inherited_defaults_are_not():
    before = snapshot()
    before["settings"].pop("rooms", None)
    after = changed(before)
    after["settings"]["rooms"] = [{"id": "ROOM1", "name": "Room 1", "finishes": room()["finishes"]}]
    assert not compare_revisions(before, after)["has_changes"]
    after["settings"]["rooms"][0]["name"] = "Kitchen"
    result = compare_revisions(before, after)
    assert any("Room ROOM1 name: Room 1 to Kitchen" in text for text in result["summary"])


def test_minimum_room_width_is_geometry_metadata_not_a_rate_change():
    before = snapshot()
    after = changed(before)
    after["settings"]["min_room_width_mm"] = 1200
    result = compare_revisions(before, after)
    assert [item["field"] for item in result["design_changes"]] == ["min_room_width_mm"]
    assert result["pricing_changes"] == [] and result["totals"]["net_change"] == 0


def test_partition_hosted_openings_use_authored_direction_and_record_group_movement():
    before = grouped_snapshot()
    before["settings"]["openings"] = [{"id": "D2", "kind": "door", "host": "P1", "offset_mm": None,
                                       "width_mm": 1000, "height_mm": 2100, "sill_mm": 0}]
    after = changed(before)
    after["settings"]["partitions"][0].update(start_mm=[4500, 0], end_mm=[4500, 8000])
    result = compare_revisions(before, after)
    movement = next(item for item in result["setting_changes"] if item["field"] == "openings.D2.offset_mm")
    assert movement["before_start_mm"] == movement["after_start_mm"] == 3500
    assert "(4,000, 3,500) mm to (4,500, 3,500) mm" in movement["description"]
    after["settings"]["partitions"][0]["end_mm"] = [4500, 7000]
    movement = next(item for item in compare_revisions(before, after)["setting_changes"] if item["field"] == "openings.D2.offset_mm")
    assert movement["after_start_mm"] == 3000
    assert compare_revisions(before, before)["has_changes"] is False


def test_partition_opening_host_add_remove_and_resize_are_validated_and_described():
    before = grouped_snapshot()
    after = changed(before)
    after["settings"]["openings"] = [{"id": "D2", "kind": "door", "host": "P1", "offset_mm": 1000,
                                      "width_mm": 1000, "height_mm": 2100, "sill_mm": 0}]
    result = compare_revisions(before, after)
    assert any("Added Door D2 on P1 (partition, authored start" in text for text in result["summary"])
    assert any("Removed Door D2 on P1" in text for text in compare_revisions(after, before)["summary"])
    after["settings"]["openings"][0]["host"] = "P99"
    with pytest.raises(ValueError, match="invalid kind or host"):
        compare_revisions(before, after)


@pytest.mark.parametrize("edit,expected", [("library", (50, 50, 0)), ("override", (100, 0, 100)), ("mixed", (100, 50, 50))])
def test_room_prefixed_items_resolve_base_rate_code_for_library_override_bridge(edit, expected):
    before = grouped_snapshot()
    after = changed(before)
    row = after["boq"][0]
    if edit in {"library", "mixed"}:
        after["rate_library"]["sha256"] = "b" * 64
        after["rate_library"]["rows"]["FLOOR_FINISH"].update(rate=70, source="Example library B")
        row.update(rate=70, rate_source="Example library B")
    if edit in {"override", "mixed"}:
        after["settings"]["rate_overrides"] = {"FLOOR_FINISH": 75}
        row.update(rate=75, rate_origin="override", rate_source="Project assumption")
    row["amount"] = row["quantity"] * row["rate"]
    after["totals"]["subtotal"] = row["amount"]
    result = compare_revisions(before, after)
    item = result["items"][0]
    assert (item["rate_effect"], item["library_rate_effect"], item["override_rate_effect"]) == expected
    assert item["rate_code"] == "FLOOR_FINISH" and item["group_id"] == ROOM_A
    assert sum(Decimal(str(item[key])) for key in ("library_rate_effect", "override_rate_effect", "unattributed_rate_effect")) == Decimal(str(item["rate_effect"]))
    assert result["rate_provenance"][0]["rate_code"] == "FLOOR_FINISH"


def test_room_and_partition_row_order_is_irrelevant_and_duplicate_ids_are_rejected():
    before = grouped_snapshot()
    before["settings"]["partitions"].append(partition(id="P2", start_mm=[6000, 0], end_mm=[6000, 8000]))
    before["rooms"].append(room(ROOM_B, "Bedroom"))
    after = changed(before)
    after["settings"]["partitions"].reverse()
    after["rooms"].reverse()
    assert not compare_revisions(before, after)["has_changes"]
    after["settings"]["partitions"].append(deepcopy(after["settings"]["partitions"][0]))
    with pytest.raises(ValueError, match="duplicate element ID"):
        compare_revisions(before, after)


@pytest.mark.parametrize("override", [False, True])
def test_single_room_to_grouped_lines_is_scope_quantity_change_without_fake_repricing(override):
    before = snapshot()
    if override:
        before["settings"]["rate_overrides"] = {"FLOOR_FINISH": 70}
        before["boq"][0].update(rate=70, amount=700, rate_origin="override")
        before["totals"]["subtotal"] = 700
    before["rooms"] = [room("ROOM1", "Room 1", area_m2=80)]
    after = changed(before)
    after["settings"]["partitions"] = [partition()]
    after["rooms"] = [room(), room(ROOM_B, "Bedroom")]
    original = before["boq"][0]
    after["boq"] = [{**original, "code": f"{identity}_FLOOR_FINISH", "rate_code": "FLOOR_FINISH",
                     "group_id": identity, "group_name": name, "quantity": quantity,
                     "amount": quantity * original["rate"]}
                    for identity, name, quantity in ((ROOM_A, "Living", 5), (ROOM_B, "Bedroom", 6))]
    after["totals"]["subtotal"] = sum(row["amount"] for row in after["boq"])
    result = compare_revisions(before, after)
    assert result["totals"]["net_change"] == result["totals"]["quantity_effect"] == original["rate"]
    assert result["totals"]["rate_effect"] == result["totals"]["library_rate_effect"] == result["totals"]["override_rate_effect"] == 0
    assert result["library_changes"] == result["pricing_changes"] == []
    assert sum(Decimal(str(item["cost_delta"])) for item in result["items"]) == Decimal(str(original["rate"]))
    assert any("Removed room ROOM1" in text for text in result["summary"])
    reverse = compare_revisions(after, before)
    assert reverse["totals"]["net_change"] == -original["rate"]
    assert reverse["totals"]["rate_effect"] == 0


def test_variation_pdf_records_changed_partitions_and_room_ids_at_zero_cost(tmp_path, monkeypatch):
    monkeypatch.setattr(rl_config, "pageCompression", 0)
    before = grouped_snapshot()
    after = changed(before)
    after["settings"]["partitions"][0]["thickness_mm"] = 125
    after["settings"]["rooms"] = [{"id": ROOM_A, "name": "Study", "finishes": {}}]
    after["rooms"][0]["name"] = "Study"
    result = compare_revisions(before, after)
    files = export_change_report(result, tmp_path)
    pdf = (tmp_path / files["variation_pdf"]).read_bytes()
    assert b"Partition P1 thickness" in pdf
    assert f"Room {ROOM_A} name".encode() in pdf
    assert b"Living to Study" in pdf
    assert result["totals"]["net_change"] == 0


def test_compiled_shell_split_move_and_legacy_migration_keep_the_saved_cost_bridge(tmp_path):
    from housesync.engine import build_house

    base = HouseSettings()
    before = build_house(base, tmp_path / "single")
    legacy = deepcopy(before)
    legacy.pop("rooms", None)
    for field in ("rooms", "partitions", "min_room_width_mm"):
        legacy["settings"].pop(field, None)
    original = deepcopy(legacy)
    migration = compare_revisions(legacy, before)
    assert migration["has_changes"] is False and migration["totals"]["net_change"] == 0
    assert legacy == original

    settings = base.model_dump(mode="json")
    settings["partitions"] = [partition()]
    settings["openings"].append({"id": "D2", "kind": "door", "host": "P1", "offset_mm": 1500,
                                 "width_mm": 1000, "height_mm": 2100, "sill_mm": 0})
    split = build_house(HouseSettings.model_validate(settings), tmp_path / "split")
    transition = compare_revisions(before, split)
    expected = Decimal(str(split["totals"]["subtotal"])) - Decimal(str(before["totals"]["subtotal"]))
    assert expected != 0
    assert Decimal(str(transition["totals"]["net_change"])) == expected
    assert sum(Decimal(str(item["cost_delta"])) for item in transition["items"]) == expected
    assert transition["totals"]["rate_effect"] == transition["totals"]["library_rate_effect"] == transition["totals"]["override_rate_effect"] == 0
    assert transition["pricing_changes"] == transition["library_changes"] == []
    assert any("Partition P1" in text for text in transition["summary"])
    assert compare_revisions(split, before)["totals"]["net_change"] == -float(expected)

    settings["partitions"] = [partition(start_mm=[3500, 0], end_mm=[3500, 8000])]
    moved = build_house(HouseSettings.model_validate(settings), tmp_path / "moved")
    assert {row["id"] for row in split["rooms"]} == {row["id"] for row in moved["rooms"]}
    movement = compare_revisions(split, moved)
    assert any(item["field"].endswith("area_m2") and item["category"] == "rooms" for item in movement["design_changes"])
    assert movement["library_changes"] == movement["pricing_changes"] == []

    selected = split["rooms"][0]
    settings["partitions"] = [partition()]
    settings["rooms"] = [{"id": selected["id"], "name": "Study", "finishes": {"floor": "Timber boards"}}]
    refinished = build_house(HouseSettings.model_validate(settings), tmp_path / "refinished")
    finish_change = compare_revisions(split, refinished)
    room_changes = [item for item in finish_change["design_changes"] if item["category"] == "rooms"]
    assert {item["field"] for item in room_changes} == {f"rooms.{selected['id']}.name", f"rooms.{selected['id']}.finishes.floor"}
    assert finish_change["totals"]["net_change"] == 0
    assert finish_change["pricing_changes"] == finish_change["library_changes"] == []


def test_default_room_renumbering_does_not_report_authored_renames_or_boq_changes():
    before = grouped_snapshot()
    before["rooms"] = [room(f"ROOM_{index:012X}", f"Room {index}") for index in range(1, 13)]
    template = before["boq"][0]
    before["boq"] = [{**template, "code": f"{row['id']}_FLOOR_FINISH", "group_id": row["id"], "group_name": row["name"]}
                     for row in before["rooms"]]
    before["totals"]["subtotal"] = 12 * 650
    after = changed(before)
    for index, (row, item) in enumerate(zip(after["rooms"], after["boq"], strict=True), 2):
        row["name"] = item["group_name"] = f"Room {index}"
    original = deepcopy(after)
    result = compare_revisions(before, after)
    assert not result["has_changes"]
    assert result["setting_changes"] == result["items"] == []
    assert after == original

    # An actual metadata edit still describes the two effective saved labels.
    selected = after["rooms"][9]
    after["settings"]["rooms"] = [{"id": selected["id"], "name": "Workshop", "finishes": {}}]
    selected["name"] = after["boq"][9]["group_name"] = "Workshop"
    result = compare_revisions(before, after)
    assert [item["field"] for item in result["design_changes"]] == [f"rooms.{selected['id']}.name"]
    assert f"Room {selected['id']} name: Room 10 to Workshop." in result["summary"]
    assert [item["code"] for item in result["items"]] == [f"{selected['id']}_FLOOR_FINISH"]
    assert result["totals"]["net_change"] == 0


def test_finishes_only_metadata_and_reset_do_not_invent_room_rename():
    before = grouped_snapshot()
    after = changed(before)
    after["settings"]["rooms"] = [{"id": ROOM_A, "name": "Living", "finishes": {"floor": "Timber boards"}}]
    after["rooms"][0]["finishes"]["floor"] = "Timber boards"
    comparison = compare_revisions(before, after)
    assert [item["field"] for item in comparison["design_changes"]] == [f"rooms.{ROOM_A}.finishes.floor"]
    reset = changed(after)
    reset["settings"]["rooms"] = [{"id": ROOM_A, "name": "Living", "finishes": {}}]
    reset["rooms"][0]["finishes"]["floor"] = "Floor finish allowance"
    reset_comparison = compare_revisions(after, reset)
    assert [item["field"] for item in reset_comparison["design_changes"]] == [f"rooms.{ROOM_A}.finishes.floor"]
    assert "Timber boards to Floor finish allowance" in reset_comparison["summary"][0]
