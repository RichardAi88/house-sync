"""Topology checks independent of the native CAD compiler."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from housesync.layout import derive_layout
from housesync.schema import HouseSettings


def partition(identity, start, end, **extra):
    return {"id": identity, "start_mm": start, "end_mm": end, "thickness_mm": 100, "height_mm": 3000, **extra}


def door(identity, host, offset=1500, **extra):
    return {"id": identity, "kind": "door", "host": host, "offset_mm": offset,
            "width_mm": 1000, "height_mm": 2100, "sill_mm": 0, **extra}


def two_rooms():
    settings = HouseSettings().model_dump(mode="json")
    settings["partitions"] = [partition("P1", [4000, 0], [4000, 8000])]
    settings["openings"].append(door("D2", "P1"))
    return settings


def test_room_ids_follow_topology_when_a_wall_moves_or_reverses():
    settings = two_rooms()
    before = derive_layout(settings, validate=True)
    moved = deepcopy(settings)
    moved["partitions"][0].update(start_mm=[3500, 0], end_mm=[3500, 8000])
    after = derive_layout(moved, validate=True)
    assert {room["id"] for room in before["rooms"]} == {room["id"] for room in after["rooms"]}
    assert [room["area_m2"] for room in before["rooms"]] != [room["area_m2"] for room in after["rooms"]]
    reversed_wall = deepcopy(settings)
    reversed_wall["partitions"][0].update(start_mm=[4000, 8000], end_mm=[4000, 0])
    assert {room["id"] for room in derive_layout(reversed_wall, validate=True)["rooms"]} == {room["id"] for room in before["rooms"]}


def test_closed_interior_ring_has_a_real_hole_and_door_access():
    settings = HouseSettings().model_dump(mode="json")
    settings["partitions"] = [partition("P1", [3000, 2000], [7000, 2000]),
                              partition("P2", [7000, 2000], [7000, 6000]),
                              partition("P3", [7000, 6000], [3000, 6000]),
                              partition("P4", [3000, 6000], [3000, 2000])]
    settings["openings"].append(door("D2", "P1"))
    layout = derive_layout(settings, validate=True)
    assert sorted(room["area_m2"] for room in layout["rooms"]) == [15.21, 63.19]
    assert sorted(len(room["holes_mm"]) for room in layout["rooms"]) == [0, 1]
    assert all(room["reachable"] for room in layout["rooms"])
    assert layout["clear_area_m2"] == 78.4
    assert layout["partition_area_m2"] == 1.6
    assert HouseSettings.model_validate(settings).partitions[0].id == "P1"
    moved = deepcopy(settings)
    for wall in moved["partitions"]:
        wall["start_mm"][0] += 100
        wall["end_mm"][0] += 100
    shifted = derive_layout(moved, validate=True)
    assert {room["id"]: room["name"] for room in layout["rooms"]} == {room["id"]: room["name"] for room in shifted["rooms"]}


def test_l_junction_returns_fill_the_corner_without_double_counting():
    settings = HouseSettings().model_dump(mode="json")
    settings["partitions"] = [partition("P1", [0, 4000], [4000, 4000]),
                              partition("P2", [4000, 4000], [4000, 8000])]
    layout = derive_layout(settings)
    assert sorted(room["area_m2"] for room in layout["rooms"]) == [15.6025, 63.5975]
    assert layout["partition_area_m2"] == .8
    assert layout["partitions"][0]["physical_end_mm"] == (4050, 4000)
    assert layout["partitions"][1]["physical_start_mm"] == (4000, 3950)
    assert layout["partitions"][1]["cut_against"] == ["P1"]


def test_junction_clearance_measures_from_the_other_wall_face():
    settings = two_rooms()
    settings["partitions"].append(partition("P2", [4000, 3000], [10000, 3000]))
    settings["openings"][-1]["offset_mm"] = 1850  # 100 mm before P2's y=2950 face.
    settings["openings"].append(door("D3", "P2"))
    assert not derive_layout(settings, validate=True)["checks"]["errors"]
    settings["openings"][-2]["offset_mm"] += .001
    with pytest.raises(ValidationError, match="junction footprint"):
        HouseSettings.model_validate(settings)


@pytest.mark.parametrize(("parts", "message"), [
    ([partition("P1", [2000, 2000], [6000, 2000])], "floating endpoint"),
    ([partition("P1", [4000, 0], [4000, 8000]), partition("P2", [4000, 0], [4000, 6000])], "overlaps"),
    ([partition("P1", [4000, 0], [4000, 8000]), partition("P2", [4050, 0], [4050, 8000])], "physically overlaps"),
    ([partition("P1", [0, 1000], [4000, 2000])], "axis-aligned"),
    ([partition("P1", [30, 0], [30, 8000])], "outside the clear shell"),
])
def test_invalid_partition_topology_is_rejected(parts, message):
    settings = two_rooms()
    settings["partitions"] = parts
    with pytest.raises(ValidationError, match=message):
        HouseSettings.model_validate(settings)


def test_narrow_room_unreachable_room_and_short_host_are_rejected():
    narrow = two_rooms()
    narrow["partitions"][0].update(start_mm=[800, 0], end_mm=[800, 8000])
    with pytest.raises(ValidationError, match="below the minimum"):
        HouseSettings.model_validate(narrow)
    disconnected = two_rooms()
    disconnected["openings"].pop()
    preview = derive_layout(disconnected)
    assert len(preview["rooms"]) == 2
    assert any("unreachable" in error for error in preview["checks"]["errors"])
    with pytest.raises(ValidationError, match="unreachable"):
        HouseSettings.model_validate(disconnected)
    short = two_rooms()
    short["partitions"][0]["height_mm"] = 2400
    short["openings"][-1]["height_mm"] = 2301
    with pytest.raises(ValidationError, match="wall head"):
        HouseSettings.model_validate(short)


def test_unsplit_room_preserves_zero_door_legacy_but_honours_minimum_width():
    assert HouseSettings(openings=[]).partitions == []
    with pytest.raises(ValidationError, match="minimum room width"):
        HouseSettings(min_room_width_mm=9000, openings=[])


@pytest.mark.parametrize("draft", [None, [], {"partitions": [None]}, {"openings": [None]},
                                  {"partitions": [{"id": "P1", "start_mm": [0], "end_mm": [1, 2]}]},
                                  {"rooms": [None]}, {"rooms": [{}] * 201}, {"length_mm": 1e300}])
def test_malformed_previews_return_diagnostics(draft):
    assert derive_layout(draft)["checks"]["errors"]


def test_room_names_and_finish_metadata_do_not_change_geometry_or_identity():
    settings = two_rooms()
    baseline = derive_layout(settings, validate=True)
    selected = baseline["rooms"][0]
    settings["rooms"] = [{"id": selected["id"], "name": "Living", "finishes": {"floor": "Timber", "paint": "Warm white"}}]
    resolved = derive_layout(HouseSettings.model_validate(settings), validate=True)
    room = next(room for room in resolved["rooms"] if room["id"] == selected["id"])
    assert room["name"] == "Living"
    assert room["finishes"]["floor"] == "Timber"
    assert room["finishes"]["paint"] == "Warm white"
    assert room["area_m2"] == selected["area_m2"]
    assert room["outline_mm"] == selected["outline_mm"]
