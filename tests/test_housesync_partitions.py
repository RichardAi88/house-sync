"""Independent take-off, topology and IFC checks for partitioned houses."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

import ifcopenshell
import ifcopenshell.geom
import pytest
from ifcopenshell.util.shape import get_volume
from pydantic import ValidationError

from housesync.changes import compare_revisions
from housesync.engine import build_house
from housesync.layout import derive_layout
from housesync.schema import HouseSettings


def layout_inputs(room_count=2):
    values = HouseSettings().model_dump(mode="json")
    values["partitions"] = [
        {"id": "P1", "start_mm": [4000, 0], "end_mm": [4000, 8000],
         "thickness_mm": 100, "height_mm": 3000, "wall_type": "100 mm masonry partition"},
    ]
    values["openings"].append({"id": "D2", "kind": "door", "host": "P1", "offset_mm": 1500,
                               "width_mm": 1000, "height_mm": 2100, "sill_mm": 0})
    if room_count == 3:
        values["partitions"].append(
            {"id": "P2", "start_mm": [4000, 3000], "end_mm": [10000, 3000],
             "thickness_mm": 100, "height_mm": 3000, "wall_type": "100 mm masonry partition"})
        values["openings"].append({"id": "D3", "kind": "door", "host": "P2", "offset_mm": 1500,
                                   "width_mm": 1000, "height_mm": 2100, "sill_mm": 0})
    return values


def _room_settings(values):
    layout = derive_layout(values)
    assert layout["checks"]["errors"] == []
    metadata = []
    for room in layout["rooms"]:
        x, y = room["label_point_mm"]
        name = "Living" if x < 4000 else "Bedroom" if len(layout["rooms"]) == 2 or y > 3000 else "Kitchen"
        metadata.append({"id": room["id"], "name": name, "finishes": {
            "floor": "Selected oak floor", "ceiling": "Plasterboard lining",
            "skirting": "100 mm primed timber", "paint": "Selected warm white",
        }})
    return {**values, "rooms": metadata}


@pytest.fixture(scope="module", params=[2, 3], ids=["two-rooms", "three-rooms-T-junction"])
def compiled_partitioned_house(request, tmp_path_factory):
    directory = tmp_path_factory.mktemp(f"partitions-{request.param}")
    settings = HouseSettings.model_validate(_room_settings(layout_inputs(request.param)))
    return request.param, settings, build_house(settings, directory), directory


def test_partitioned_takeoff_matches_hand_calculated_clear_areas_and_final_faces(compiled_partitioned_house):
    count, _, result, _ = compiled_partitioned_house
    # Shell opening voids: 1x2.1 + two 1.5x1.2 windows = 5.7 m2.
    shell_volume = ((10.4 * 8.4) - 10 * 8) * 3 - 5.7 * .2
    external_faces = 2 * (10.4 + 8.4) * 3 - 5.7
    # P1 footprint=.1x8. P2 starts at the east face of P1 for its
    # 5.95m exposed run; the buried T overlap is not counted twice.
    partition_footprint = .1 * 8 + (.1 * 5.95 if count == 3 else 0)
    partition_volume = partition_footprint * 3 - (count - 1) * 1 * 2.1 * .1
    clear_area = 80 - partition_footprint
    # Sum of room perimeters, accounting for buried partition end contacts.
    room_perimeters = 51.8 if count == 2 else 63.5
    net_internal_faces = room_perimeters * 3 - 5.7 - (count - 1) * 2 * 2.1
    net_skirting = room_perimeters - 1 - (count - 1) * 2
    quantities = result["quantities"]
    assert quantities["floor_area_m2"] == 80  # Slab remains under partitions.
    assert quantities["floor_finish_area_m2"] == pytest.approx(clear_area, abs=1e-6)
    assert quantities["ceiling_area_m2"] == pytest.approx(clear_area, abs=1e-6)
    assert quantities["partition_wall_volume_m3"] == pytest.approx(partition_volume, abs=1e-6)
    assert quantities["shell_wall_volume_m3"] == pytest.approx(shell_volume, abs=1e-6)
    assert quantities["wall_volume_m3"] == pytest.approx(shell_volume + partition_volume, abs=1e-6)
    assert quantities["internal_plaster_area_m2"] == pytest.approx(net_internal_faces, abs=1e-6)
    assert quantities["external_render_area_m2"] == pytest.approx(external_faces, abs=1e-6)
    assert quantities["paint_area_m2"] == pytest.approx(net_internal_faces + external_faces, abs=1e-6)
    assert quantities["skirting_length_m"] == pytest.approx(net_skirting, abs=1e-6)
    assert quantities["roof_area_m2"] == 99
    assert quantities["door_count"] == count
    assert quantities["window_count"] == 2
    assert sum(room["area_m2"] for room in result["rooms"]) == pytest.approx(clear_area)
    assert sum(room["internal_plaster_area_m2"] for room in result["rooms"]) == pytest.approx(net_internal_faces)
    assert sum(room["skirting_length_m"] for room in result["rooms"]) == pytest.approx(net_skirting)


def test_each_room_has_independent_net_measurements_and_selected_metadata(compiled_partitioned_house):
    count, settings, result, _ = compiled_partitioned_house
    # Each record is area, perimeter, plaster/paint area and net skirting.
    expected = ({"Living": (31.6, 23.9, 69.6, 22.9), "Bedroom": (47.6, 27.9, 75.9, 25.9)} if count == 2 else {
        "Living": (31.6, 23.9, 69.6, 22.9), "Kitchen": (17.5525, 17.8, 47.1, 14.8),
        "Bedroom": (29.4525, 21.8, 59.7, 20.8),
    })
    assert len(result["rooms"]) == count
    metadata = {room.id: room for room in settings.rooms}
    for room in result["rooms"]:
        area, perimeter, plaster, skirting = expected[room["name"]]
        assert room["area_m2"] == pytest.approx(area)
        assert room["ceiling_area_m2"] == pytest.approx(area)
        assert room["perimeter_m"] == pytest.approx(perimeter)
        assert room["internal_plaster_area_m2"] == pytest.approx(plaster)
        assert room["paint_area_m2"] == pytest.approx(plaster)
        assert room["skirting_length_m"] == pytest.approx(skirting)
        assert room["reachable"] is True
        assert room["finishes"] == metadata[room["id"]].finishes.model_dump()


def test_grouped_boq_prices_each_room_and_partition_once(compiled_partitioned_house):
    count, _, result, _ = compiled_partitioned_house
    assert result["totals"] == {"currency": "AUD", "subtotal": {2: 57038.34, 3: 59872.90}[count]}
    rows = result["boq"]
    # Seven shared lines, one construction line per partition, five room trades.
    assert len(rows) == 7 + (count - 1) + count * 5
    assert len({row["code"] for row in rows}) == len(rows)
    assert all(row["rate_code"] in {"FLOOR", "WALL", "ROOF", "DOOR", "WINDOW", "EXTERNAL_RENDER",
                                        "PAINT", "FLOOR_FINISH", "CEILING", "SKIRTING", "INTERNAL_PLASTER"} for row in rows)
    for rate_code, quantity_key in (("WALL", "wall_volume_m3"), ("FLOOR_FINISH", "floor_finish_area_m2"),
                                    ("CEILING", "ceiling_area_m2"), ("SKIRTING", "skirting_length_m"),
                                    ("INTERNAL_PLASTER", "internal_plaster_area_m2"), ("PAINT", "paint_area_m2")):
        assert sum(row["measured_quantity"] for row in rows if row["rate_code"] == rate_code) == pytest.approx(result["quantities"][quantity_key])
    room_ids = {room["id"] for room in result["rooms"]}
    for identity in room_ids:
        assert {row["rate_code"] for row in rows if row["group_id"] == identity} == {
            "FLOOR_FINISH", "CEILING", "SKIRTING", "INTERNAL_PLASTER", "PAINT"}
    assert sum(Decimal(str(row["amount"])) for row in rows) == Decimal(str(result["totals"]["subtotal"]))
    assert sum(Decimal(str(group["subtotal"])) for group in result["boq_groups"]) == Decimal(str(result["totals"]["subtotal"]))


def test_ifc_has_individual_partition_openings_clear_spaces_and_no_duplicate_wall_volume(compiled_partitioned_house):
    from ifcopenshell.util.element import get_psets

    count, _, result, directory = compiled_partitioned_house
    model = ifcopenshell.open(str(directory / result["files"]["ifc"]))
    assert len(model.by_type("IfcWall")) == 4 + count - 1
    assert {wall.Name for wall in model.by_type("IfcWall")} >= {f"P{index}" for index in range(1, count)}
    assert len(model.by_type("IfcDoor")) == count
    assert len(model.by_type("IfcWindow")) == 2
    assert len(model.by_type("IfcOpeningElement")) == count + 2
    spaces = model.by_type("IfcSpace")
    assert {space.Name for space in spaces} == {room["id"] for room in result["rooms"]}
    assert {space.LongName for space in spaces} == {room["name"] for room in result["rooms"]}
    for space in spaces:
        assert get_psets(space)["HouseSpec"]["floor_finish"] == "Selected oak floor"
    for partition in (wall for wall in model.by_type("IfcWall") if wall.Name.startswith("P")):
        assert get_psets(partition)["HouseSpec"]["wall_type"] == "100 mm masonry partition"
    geometry_settings = ifcopenshell.geom.settings()
    exported_volume = 0
    for wall in model.by_type("IfcWall"):
        shape = ifcopenshell.geom.create_shape(geometry_settings, wall)
        exported_volume += get_volume(shape.geometry)
    assert exported_volume == pytest.approx(result["quantities"]["wall_volume_m3"], abs=1e-6)
    assert result["checks"]["wall_clashes"] == []


def test_room_ids_are_stable_under_partition_reordering_and_topology_preserving_moves():
    before = layout_inputs(3)
    after = deepcopy(before)
    after["partitions"].reverse()
    for partition in after["partitions"]:
        if partition["id"] == "P1":
            partition["start_mm"][0] = partition["end_mm"][0] = 3800
        else:
            partition["start_mm"][0] = 3800
    old, new = derive_layout(before, validate=True), derive_layout(after, validate=True)
    assert {room["id"] for room in old["rooms"]} == {room["id"] for room in new["rooms"]}
    assert sorted(room["area_m2"] for room in old["rooms"]) != sorted(room["area_m2"] for room in new["rooms"])


def _invalid_inputs(case):
    values = layout_inputs(3)
    if case == "unreachable":
        values["openings"] = [row for row in values["openings"] if row["id"] != "D3"]
    elif case == "narrow":
        values["partitions"][0]["start_mm"][0] = values["partitions"][0]["end_mm"][0] = 500
        values["partitions"][1]["start_mm"][0] = 500
    elif case == "overlap":
        values["partitions"].append({"id": "P3", "start_mm": [4000, 1000], "end_mm": [4000, 7000]})
    elif case == "physical_overlap":
        values["partitions"].append({"id": "P3", "start_mm": [4050, 0], "end_mm": [4050, 8000]})
    elif case == "floating":
        values["partitions"].append({"id": "P3", "start_mm": [1000, 1000], "end_mm": [2000, 1000]})
    elif case == "diagonal":
        values["partitions"].append({"id": "P3", "start_mm": [1000, 1000], "end_mm": [2000, 2000]})
    elif case == "junction":
        next(row for row in values["openings"] if row["id"] == "D2")["offset_mm"] = 2600
    elif case == "opening_overlap":
        values["openings"].append({"id": "D4", "kind": "door", "host": "P1", "offset_mm": 1600,
                                   "width_mm": 1000, "height_mm": 2100, "sill_mm": 0})
    elif case == "opening_end_clearance":
        next(row for row in values["openings"] if row["id"] == "D2")["offset_mm"] = 99
    elif case == "opening_head_clearance":
        next(row for row in values["openings"] if row["id"] == "D2")["height_mm"] = 2950
    elif case == "unknown_host":
        next(row for row in values["openings"] if row["id"] == "D3")["host"] = "P99"
    else:
        raise AssertionError(case)
    return values


@pytest.mark.parametrize("case,message", [
    ("unreachable", "unreachable"), ("narrow", "clear width"), ("overlap", "overlaps"),
    ("physical_overlap", "physically overlaps"), ("floating", "floating endpoint"),
    ("diagonal", "axis-aligned"), ("junction", "junction footprint"),
    ("opening_overlap", "100 mm apart"), ("opening_end_clearance", "100 mm clear at each inner end"),
    ("opening_head_clearance", "100 mm below"), ("unknown_host", "unknown host"),
])
def test_invalid_partition_and_door_drafts_are_reported_and_blocked_before_cad(case, message):
    values = _invalid_inputs(case)
    layout = derive_layout(values)
    assert any(message in error for error in layout["checks"]["errors"])
    with pytest.raises(ValidationError):
        HouseSettings.model_validate(values)


def test_exact_room_width_and_opening_clearance_boundaries_are_accepted():
    values = layout_inputs(3)
    values["partitions"][0]["start_mm"][0] = values["partitions"][0]["end_mm"][0] = 950
    values["partitions"][1]["start_mm"][0] = 950
    next(row for row in values["openings"] if row["id"] == "D2")["offset_mm"] = 100
    values["openings"].append({"id": "D4", "kind": "door", "host": "P1", "offset_mm": 1200,
                               "width_mm": 1000, "height_mm": 2900, "sill_mm": 0})
    settings = HouseSettings.model_validate(values)
    layout = derive_layout(settings, validate=True)
    assert min(room["min_width_mm"] for room in layout["rooms"]) == pytest.approx(900)


def test_legacy_settings_without_layout_fields_preserve_single_room_quantities_and_comparison(tmp_path):
    values = HouseSettings().model_dump(mode="json")
    for key in ("partitions", "rooms", "min_room_width_mm"):
        values.pop(key)
    settings = HouseSettings.model_validate(values)
    assert settings.partitions == settings.rooms == []
    result = build_house(settings, tmp_path)
    assert len(result["rooms"]) == 1 and result["rooms"][0]["id"] == "ROOM1"
    assert result["quantities"]["floor_area_m2"] == result["quantities"]["floor_finish_area_m2"] == 80
    assert result["quantities"]["wall_volume_m3"] == pytest.approx(20.94)
    assert result["quantities"]["paint_area_m2"] == pytest.approx(209.4)
    assert result["quantities"]["skirting_length_m"] == 35
    assert len(result["boq"]) == 11
    assert result["totals"] == {"currency": "AUD", "subtotal": 53330.22}
    historical = deepcopy(result)
    for key in ("partitions", "rooms", "min_room_width_mm"):
        historical["settings"].pop(key, None)
    assert not compare_revisions(historical, result)["has_changes"]


def test_closed_partition_ring_preserves_the_surrounding_room_hole_and_exposed_face_quantities(tmp_path):
    values = HouseSettings().model_dump(mode="json")
    points = [[3000, 2000], [7000, 2000], [7000, 6000], [3000, 6000]]
    values["partitions"] = [
        {"id": f"P{index + 1}", "start_mm": point, "end_mm": points[(index + 1) % 4],
         "thickness_mm": 100, "height_mm": 3000, "wall_type": "Ring partition"}
        for index, point in enumerate(points)
    ]
    values["openings"].append({"id": "D2", "kind": "door", "host": "P1", "offset_mm": 1500,
                               "width_mm": 1000, "height_mm": 2100, "sill_mm": 0})
    result = build_house(HouseSettings.model_validate(values), tmp_path)
    rooms = sorted(result["rooms"], key=lambda room: room["area_m2"])
    assert len(rooms) == 2
    assert rooms[0]["area_m2"] == pytest.approx(3.9 * 3.9)
    assert rooms[0]["holes_mm"] == []
    assert rooms[1]["area_m2"] == pytest.approx(80 - 4.1 * 4.1)
    assert len(rooms[1]["holes_mm"]) == 1
    assert all(room["reachable"] for room in rooms)
    quantities = result["quantities"]
    footprint = 4.1 * 4.1 - 3.9 * 3.9
    assert quantities["floor_finish_area_m2"] == pytest.approx(80 - footprint)
    assert quantities["ceiling_area_m2"] == pytest.approx(80 - footprint)
    assert quantities["partition_wall_volume_m3"] == pytest.approx(footprint * 3 - 1 * 2.1 * .1)
    assert quantities["internal_plaster_area_m2"] == pytest.approx((36 + 4 * 4.1 + 4 * 3.9) * 3 - 5.7 - 2 * 2.1)
    assert quantities["external_render_area_m2"] == pytest.approx(107.1)
    assert quantities["paint_area_m2"] == pytest.approx(301.2)
    assert quantities["skirting_length_m"] == pytest.approx(36 + 4 * 4.1 + 4 * 3.9 - 1 - 2 * 1)
    assert result["checks"]["wall_clashes"] == []


def test_lower_t_partition_restores_exposed_contact_bands_above_its_head(tmp_path):
    values = layout_inputs(3)
    values["partitions"][1]["height_mm"] = 2400
    result = build_house(HouseSettings.model_validate(_room_settings(values)), tmp_path)
    # P1 remains 3m high. P2's clear run is 5.95m, 2.4m high, and each
    # door cuts .1x1x2.1m3. Hidden P2 end contacts are only 2.4m high.
    partition_volume = (8 * .1 * 3 - .1 * 1 * 2.1) + (5.95 * .1 * 2.4 - .1 * 1 * 2.1)
    shell_interior = 36 * 3 - 5.7 - 2 * .1 * 3 - .1 * 2.4
    p1_faces = 2 * 8 * 3 - 2 * 1 * 2.1 - .1 * 2.4
    p2_faces = 2 * 5.95 * 2.4 - 2 * 1 * 2.1
    quantities = result["quantities"]
    assert quantities["partition_wall_volume_m3"] == pytest.approx(partition_volume)
    assert quantities["wall_volume_m3"] == pytest.approx(20.94 + partition_volume)
    assert quantities["internal_plaster_area_m2"] == pytest.approx(shell_interior + p1_faces + p2_faces)
    assert quantities["paint_area_m2"] == pytest.approx(shell_interior + p1_faces + p2_faces + 107.1)
    assert quantities["floor_finish_area_m2"] == pytest.approx(78.605)
    rooms = {room["name"]: room for room in result["rooms"]}
    assert rooms["Living"]["internal_plaster_area_m2"] == pytest.approx(69.6)
    assert rooms["Kitchen"]["internal_plaster_area_m2"] == pytest.approx(43.59)
    assert rooms["Bedroom"]["internal_plaster_area_m2"] == pytest.approx(56.19)
    assert sum(room["paint_area_m2"] for room in rooms.values()) == pytest.approx(quantities["internal_plaster_area_m2"])
