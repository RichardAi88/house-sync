"""Independent geometric and revision checks for the House Sync example."""

import json

import ifcopenshell
import ifcopenshell.geom
import pytest
from ifcopenshell.util.shape import get_volume
from pydantic import ValidationError

from housesync.engine import build_house
from housesync.schema import HouseSettings


@pytest.fixture(scope="module")
def baseline(tmp_path_factory):
    directory = tmp_path_factory.mktemp("house-baseline")
    return build_house(HouseSettings(), directory), directory


def test_compiled_quantities_openings_and_corners(baseline):
    result, directory = baseline
    quantities = result["quantities"]
    # Independent rectangular-shell calculation, not a copy of IR summation.
    gross_shell_m3 = ((10.4 * 8.4) - (10 * 8)) * 3
    void_area_m2 = (1 * 2.1) + (2 * 1.5 * 1.2)
    assert quantities["floor_area_m2"] == 80
    assert quantities["wall_volume_m3"] == pytest.approx(gross_shell_m3 - void_area_m2 * 0.2)
    exposed_perimeters_m = 2 * (10 + 8) + 2 * (10.4 + 8.4)
    assert quantities["paint_area_m2"] == pytest.approx(exposed_perimeters_m * 3 - 2 * void_area_m2)
    assert quantities["roof_area_m2"] == pytest.approx(11 * 9)
    assert quantities["door_count"] == 1
    assert quantities["window_count"] == 2
    assert result["checks"]["wall_clashes"] == []
    model = ifcopenshell.open(str(directory / result["files"]["ifc"]))
    assert len(model.by_type("IfcWall")) == 4
    assert len(model.by_type("IfcOpeningElement")) == 3
    assert len(model.by_type("IfcDoor")) == 1
    assert len(model.by_type("IfcWindow")) == 2
    # Keep the shape owner alive while inspecting its C++ geometry wrapper.
    exported_volume = 0.0
    geometry_settings = ifcopenshell.geom.settings()
    for wall in model.by_type("IfcWall"):
        shape = ifcopenshell.geom.create_shape(geometry_settings, wall)
        exported_volume += get_volume(shape.geometry)
    assert exported_volume == pytest.approx(quantities["wall_volume_m3"])
    assert len(result["revision"]) == 12
    json.dumps(result, allow_nan=False)
    assert all(max(face) < len(item["vertices"]) for item in result["mesh"]["objects"] for face in item["faces"])


def test_dimension_change_updates_model_quantities_and_revision(baseline, tmp_path):
    before, _ = baseline
    after = build_house(HouseSettings(length_mm=12000), tmp_path)
    assert after["revision"] != before["revision"]
    assert after["quantities"]["floor_area_m2"] == 96
    assert after["quantities"]["paint_area_m2"] - before["quantities"]["paint_area_m2"] == pytest.approx(24)
    assert after["drawing"]["length_mm"] == 12000
    assert after["totals"]["subtotal"] > before["totals"]["subtotal"]
    assert max(v[0] for item in after["mesh"]["objects"] for v in item["vertices"]) == pytest.approx(12.5)


def test_finish_change_preserves_geometry_and_changes_description(baseline, tmp_path):
    before, _ = baseline
    after = build_house(HouseSettings(paint_colour="Warm white", paint_hex="#FAF9F6"), tmp_path)
    assert after["revision"] != before["revision"]
    assert after["quantities"] == before["quantities"]
    assert after["totals"] == before["totals"]
    assert "Warm white" in next(row["description"] for row in after["boq"] if row["code"] == "PAINT")
    before_walls = [item for item in before["mesh"]["objects"] if item["kind"] == "wall"]
    after_walls = [item for item in after["mesh"]["objects"] if item["kind"] == "wall"]
    assert [item["vertices"] for item in after_walls] == [item["vertices"] for item in before_walls]
    assert {item["colour"] for item in after_walls} == {"#FAF9F6"}


@pytest.mark.parametrize("updates", [
    {"length_mm": 1000}, {"height_mm": float("nan")}, {"wall_thickness_mm": 0},
    {"paint_hex": "blue"}, {"project_name": "bad\nname"}, {"rates": {"paint_m2": -1}},
])
def test_invalid_inputs_rejected(updates):
    with pytest.raises(ValidationError):
        HouseSettings(**updates)
