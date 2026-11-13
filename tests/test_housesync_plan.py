"""Plan edit contracts: fitting constraints, real voids, and shared documents."""
from __future__ import annotations

from copy import deepcopy

import ifcopenshell
import ifcopenshell.geom
import pytest
from ifcopenshell.util.shape import get_volume
from pydantic import ValidationError

from housesync.engine import build_house
from housesync.schema import HouseSettings, OpeningSettings


def opening(id="D1", kind="door", host="W1", offset_mm=1000, width_mm=1000, height_mm=2100, sill_mm=0):
    return dict(id=id, kind=kind, host=host, offset_mm=offset_mm, width_mm=width_mm, height_mm=height_mm, sill_mm=sill_mm)


def test_legacy_omitted_openings_and_explicit_empty_scope():
    defaults = HouseSettings().model_dump(mode="json")
    assert [item["id"] for item in defaults["openings"]] == ["D1", "N1", "N2"]
    assert all(item["offset_mm"] is None for item in defaults["openings"])
    legacy = deepcopy(defaults)
    del legacy["openings"]
    assert HouseSettings.model_validate(legacy).model_dump(mode="json") == defaults
    assert HouseSettings(openings=[]).openings == []


@pytest.mark.parametrize("updates", [
    {"openings": [opening(), opening()]},
    {"openings": [opening(offset_mm=99)]},
    {"openings": [opening(offset_mm=9001)]},
    {"openings": [opening(offset_mm=float("nan"))]},
    {"openings": [opening(offset_mm=True)]},
    {"openings": [opening(width_mm=299)]},
    {"openings": [opening(height_mm=299)]},
    {"openings": [opening(sill_mm=100)]},
    {"openings": [opening(id="N1")]},
    {"openings": [opening(host="W5")]},
    {"openings": [opening(id="W1")]},
    {"openings": [opening(id="D0")]},
    {"openings": [opening(height_mm=2901)]},
    {"openings": [opening(), opening(id="D2", offset_mm=2099)]},
    {"openings": [opening(kind="window", id="N1", height_mm=1200, sill_mm=1800)]},
    {"openings": [opening(), {**opening(id="D2", offset_mm=4000), "unrecognised": 1}]},
    {"length_mm": 3000, "openings": [opening(offset_mm=2200)]},
])
def test_invalid_plan_openings_cannot_reach_geometry(updates):
    with pytest.raises(ValidationError):
        HouseSettings(**updates)


def test_clearance_boundaries_and_independent_default_lists():
    settings = HouseSettings(openings=[opening(offset_mm=100), opening(id="D2", offset_mm=1200)])
    assert settings.openings[1].offset_mm == 1200
    assert HouseSettings(openings=[opening(offset_mm=8900, height_mm=2900)]).openings[0].height_mm == 2900
    assert OpeningSettings(id="N9", kind="window", host="W4", width_mm=300, height_mm=300).sill_mm == 0
    first = HouseSettings()
    first.openings.pop()
    assert len(HouseSettings().openings) == 3
    with pytest.raises(ValidationError):
        HouseSettings(length_mm=30000, width_mm=30000, openings=[
            opening(id=f"D{i + 1}", host=f"W{i % 4 + 1}", offset_mm=100 + (i // 4) * 400, width_mm=300)
            for i in range(41)
        ])


@pytest.fixture(scope="module")
def edited_house(tmp_path_factory):
    settings = HouseSettings(openings=[
        opening(width_mm=1200),
        opening(id="N1", kind="window", host="W3", offset_mm=500, width_mm=1800, height_mm=1200, sill_mm=900),
        opening(id="N2", kind="window", host="W2", offset_mm=2000, width_mm=1000, height_mm=1000, sill_mm=1100),
        opening(id="D2", host="W4", offset_mm=400, width_mm=900, height_mm=2200),
        opening(id="N3", kind="window", host="W1", offset_mm=5000, width_mm=800, height_mm=900, sill_mm=1200),
    ])
    directory = tmp_path_factory.mktemp("plan-edited")
    return build_house(settings, directory), directory


def test_plan_edits_cut_real_geometry_and_update_boq(edited_house):
    result, directory = edited_house
    q = result["quantities"]
    # Derive expected scope independently from rectangular shell and void areas.
    void_area = 1.2 * 2.1 + 1.8 * 1.2 + 1 * 1 + .9 * 2.2 + .8 * .9
    gross_volume = (10.4 * 8.4 - 10 * 8) * 3
    assert q["wall_volume_m3"] == pytest.approx(gross_volume - void_area * .2)
    assert q["paint_area_m2"] == pytest.approx(gross_volume / .2 * 2 - void_area * 2)
    assert (q["door_count"], q["window_count"]) == (2, 3)
    assert q["floor_area_m2"] == 80
    assert result["checks"]["wall_clashes"] == []
    boq = {item["code"]: item for item in result["boq"]}
    assert boq["DOOR"]["quantity"] == 2
    assert boq["DOOR"]["element_ids"] == ["D1", "D2"]
    assert boq["WINDOW"]["element_ids"] == ["N1", "N2", "N3"]
    assert "1200 x 2100" in boq["DOOR"]["description"]
    assert "D2: 900 x 2200 mm" in boq["DOOR"]["description"]
    model = ifcopenshell.open(str(directory / result["files"]["ifc"]))
    assert len(model.by_type("IfcOpeningElement")) == 5
    assert len(model.by_type("IfcDoor")) == 2
    assert len(model.by_type("IfcWindow")) == 3
    geometry_settings = ifcopenshell.geom.settings()
    exported_volume = 0.0
    for wall in model.by_type("IfcWall"):
        shape = ifcopenshell.geom.create_shape(geometry_settings, wall)
        exported_volume += get_volume(shape.geometry)
    assert exported_volume == pytest.approx(q["wall_volume_m3"])


def test_north_and_west_offsets_use_directional_inner_reference(edited_house):
    result, _ = edited_house
    drawings = {item["id"]: item for item in result["drawing"]["openings"]}
    assert drawings["N1"]["start_mm"] == pytest.approx(500)
    assert drawings["D2"]["start_mm"] == pytest.approx(400)
    assert drawings["N1"]["kind"] == "window"
    # The actual compiled frames share these spatial positions, not just labels.
    meshes = {item["id"]: item for item in result["mesh"]["objects"]}
    north_x = [vertex[0] for vertex in meshes["N1"]["vertices"]]
    west_y = [vertex[1] for vertex in meshes["D2"]["vertices"]]
    assert (min(north_x), max(north_x)) == pytest.approx((7.7, 9.5))
    assert (min(west_y), max(west_y)) == pytest.approx((6.7, 7.6))


def test_deleting_all_openings_restores_solid_wall_quantities(tmp_path):
    result = build_house(HouseSettings(openings=[]), tmp_path)
    assert result["drawing"]["openings"] == []
    assert result["quantities"]["door_count"] == result["quantities"]["window_count"] == 0
    assert result["quantities"]["wall_volume_m3"] == pytest.approx(22.08)
    assert result["quantities"]["paint_area_m2"] == pytest.approx(220.8)
    for item in result["boq"]:
        if item["code"] in {"DOOR", "WINDOW"}:
            assert item["quantity"] == item["amount"] == 0
            assert item["element_ids"] == []
            assert "No " in item["description"]


def test_fixed_and_centred_openings_respond_differently_to_resizing(tmp_path):
    fixed = opening(offset_mm=1000)
    centred = opening(id="N1", kind="window", host="W3", offset_mm=None, width_mm=1500, height_mm=1200, sill_mm=900)
    settings = HouseSettings(length_mm=12000, openings=[fixed, centred])
    result = build_house(settings, tmp_path)
    positions = {item["id"]: item["start_mm"] for item in result["drawing"]["openings"]}
    assert positions == {"D1": 1000, "N1": 5250}
    assert settings.openings[1].offset_mm is None


def test_legacy_opening_boq_text_is_stable_for_unchanged_sizes(tmp_path):
    result = build_house(HouseSettings(), tmp_path)
    rows = {item["code"]: item for item in result["boq"]}
    assert rows["DOOR"]["description"] == "1000 x 2100 mm door; shared illustrative opening allowance"
    assert rows["WINDOW"]["description"] == "1500 x 1200 mm windows; shared illustrative opening allowance"
    assert rows["DOOR"]["element_ids"] == ["D1"]
    assert rows["WINDOW"]["element_ids"] == ["N1", "N2"]
    from housesync.changes import compare_revisions

    legacy = deepcopy(result)
    legacy["revision"] = "a" * 12
    del legacy["settings"]["openings"]
    comparison = compare_revisions(legacy, result)
    assert comparison["has_changes"] is False
    assert comparison["items"] == []
    assert comparison["totals"]["net_change"] == 0
