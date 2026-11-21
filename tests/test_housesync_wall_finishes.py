"""Independent brick leaves stay coordinated across geometry, pricing and issued outputs."""

from copy import deepcopy
from io import BytesIO
from zipfile import ZipFile

import ifcopenshell
import ifcopenshell.util.element
import pytest
from docx import Document
from pydantic import ValidationError

from housesync.changes import compare_revisions
from housesync.documents import drawing_sheets, export_documents
from housesync.engine import build_house
from housesync.purchasing import purchase_orders_zip
from housesync.quotes import build_variation_quote
from housesync.schema import ExteriorFinish, HouseSettings
from housesync.server import Application


def test_new_editor_project_selects_facebrick_and_plaster_paint(tmp_path):
    state = Application(tmp_path).state()
    assert state["settings"]["exterior_finish"]["kind"] == "facebrick"
    assert state["settings"]["inner_leaf_finish"] == "plaster_paint"
    assert state["totals"]["subtotal"] == 46904.22
    assert Application(tmp_path).state()["revision"] == state["revision"]


@pytest.fixture(scope="module")
def builds(tmp_path_factory):
    root = tmp_path_factory.mktemp("brick-leaves")
    data = HouseSettings(exterior_finish=ExteriorFinish(name="Charcoal", hex="#555455"),
                         paint_colour="Chalk", paint_hex="#FAF8F3").model_dump(mode="json")
    cases = {"finished": data, "bare": {**data, "inner_leaf_finish": "bare_brick", "inner_brick_hex": "#C28961"},
             "exterior_only": {**data, "inner_leaf_finish": "bare_brick",
                               "exterior_finish": {"kind": "rendered_paint", "name": "Ochre", "hex": "#B77449"}}}
    return root, {case: build_house(HouseSettings.model_validate(values), root / case) for case, values in cases.items()}


def test_facebrick_inside_and_outside_are_distinct_on_every_wall(builds):
    root, cases = builds
    result = cases["finished"]
    assert result["totals"]["subtotal"] == 46904.22
    q = result["quantities"]
    assert (q["external_wall_face_area_m2"], q["external_render_area_m2"]) == (107.1, 0)
    assert (q["shell_inner_face_area_m2"], q["internal_plaster_area_m2"], q["paint_area_m2"]) == (102.3, 102.3, 102.3)
    paint = next(row for row in result["boq"] if row["code"] == "PAINT")
    assert (paint["group_id"], paint["net_quantity"], paint["amount"]) == ("ROOM1", 204.6, 1841.4)
    # Each normal points into the room from its outer plane. Returns and reveals use different slots.
    planes = {"W1": (1, -.2, 0), "W2": (0, 10.2, 10), "W3": (1, 8.2, 8), "W4": (0, -.2, 0)}
    for wall in (obj for obj in result["mesh"]["objects"] if obj["id"] in planes):
        axis, outer, inner = planes[wall["id"]]
        assert wall["face_colours"][:2] == ["#555455", "#FAF8F3"]
        assert set(wall["face_slots"]) == {0, 1, 2}
        for face, slot in zip(wall["faces"], wall["face_slots"], strict=True):
            points = [wall["vertices"][i] for i in face]
            for coord, expected in [(outer, 0), (inner, 1)]:
                if all(abs(p[axis] - coord) < 1e-5 for p in points):
                    assert slot == expected
            if wall["id"] in {"W1", "W3"} and any(all(abs(p[0] - x) < 1e-5 for p in points) for x in (-.2, 10.2)):
                assert slot == 0
    model = ifcopenshell.open(str(root / "finished" / "house.ifc"))
    assert len(model.by_type("IfcWall")) == 4 and len(model.by_type("IfcOpeningElement")) == 3
    for wall in model.by_type("IfcWall"):
        usage = ifcopenshell.util.element.get_material(wall)
        assert usage.is_a("IfcMaterialLayerSetUsage")
        assert [(layer.Material.Name, layer.LayerThickness) for layer in usage.ForLayerSet.MaterialLayers] == [
            ("outer_brick", 100), ("inner_common_brick", 100)]
        finishes = ifcopenshell.util.element.get_psets(wall)["HouseSpec_WallFinishes"]
        assert finishes["Exterior"] == "Charcoal #555455" and finishes["Interior"] == "Chalk #FAF8F3"


def test_bare_brick_omits_only_selected_finishes_and_prices_credit(builds, tmp_path):
    _, cases = builds
    before, bare, painted_outside = (cases[key] for key in ("finished", "bare", "exterior_only"))
    assert {case["quantities"]["wall_volume_m3"] for case in cases.values()} == {20.94}
    assert {case["quantities"]["skirting_length_m"] for case in cases.values()} == {35}
    assert bare["quantities"]["paint_area_m2"] == 0 and bare["totals"]["subtotal"] == 42055.20
    assert not {"PAINT", "INTERNAL_PLASTER", "EXTERNAL_RENDER"} & {row["code"] for row in bare["boq"]}
    with ZipFile(BytesIO(purchase_orders_zip(bare))) as orders:
        assert len(orders.namelist()) == 8
    comparison = compare_revisions(before, bare)
    quote = build_variation_quote(before, bare, comparison, "2026-11-21")["public"]
    assert quote["totals"]["credits_at_cost"] == 4849.02
    assert "internal brick leaf bare" in " ".join(quote["scope"])
    assert painted_outside["quantities"]["paint_area_m2"] == 107.1
    outside_paint = next(row for row in painted_outside["boq"] if row["code"] == "PAINT")
    assert outside_paint["group_id"] == "SHELL" and "Ochre" in outside_paint["description"] and "Chalk" not in outside_paint["description"]
    export_documents(bare, tmp_path)
    doc = Document(tmp_path / "specification.docx")
    spec = " ".join(cell.text for table in doc.tables for row in table.rows for cell in row.cells)
    assert "Exposed facebrick - Charcoal (#555455)" in spec and "Bare common brick, ready for finishes (#C28961)" in spec
    assert "Chalk" not in spec
    sheets = drawing_sheets(bare)
    assert sum(p.get("fill") == "#555455" for _, _, primitives in sheets for p in primitives) >= 5
    assert any(p.get("fill") == "#C28961" for p in sheets[0][2])


def test_bare_shell_preserves_partition_faces_and_room_paint(tmp_path):
    data = HouseSettings(exterior_finish=ExteriorFinish(), inner_leaf_finish="bare_brick").model_dump(mode="json")
    data["partitions"] = [{"id": "P1", "start_mm": [4000, 0], "end_mm": [4000, 8000]}]
    data["openings"].append({"id": "D2", "kind": "door", "host": "P1", "offset_mm": 1500,
                             "width_mm": 1000, "height_mm": 2100, "sill_mm": 0})
    result = build_house(HouseSettings.model_validate(data), tmp_path)
    q = result["quantities"]
    assert q["shell_inner_face_area_m2"] == pytest.approx(102.3 - 2 * .1 * 3)
    assert q["paint_area_m2"] == q["internal_plaster_area_m2"] == pytest.approx(2 * (8 * 3 - 2.1))
    assert all(room["paint_area_m2"] == pytest.approx(21.9) for room in result["rooms"])
    assert "PAINT" not in {row["code"] for row in result["boq"]}
    assert sum(row["net_quantity"] for row in result["boq"] if row["rate_code"] == "PAINT") == pytest.approx(87.6)


def test_reopening_a_shared_colour_preserves_boq_and_variation(tmp_path):
    legacy = {"paint_colour": "Slate", "paint_hex": "#636569"}
    s = HouseSettings.model_validate(legacy)
    assert (s.exterior_finish.kind, s.exterior_finish.name, s.exterior_finish.hex) == ("rendered_paint", "Slate", "#636569")
    current = build_house(s, tmp_path / "current")
    assert current["totals"]["subtotal"] == 53330.22
    old = deepcopy(current)
    old["revision"] = "a" * 12
    for key in ("exterior_finish", "inner_leaf_finish", "inner_brick_hex"):
        del old["settings"][key]
    assert not compare_revisions(old, current)["has_changes"]
    moved = s.model_copy(deep=True)
    moved.exterior_finish.name, moved.exterior_finish.hex = "Sand", "#E8CCA5"
    edited = build_house(moved, tmp_path / "edited")
    changes = compare_revisions(current, edited)
    assert {row["field"] for row in changes["setting_changes"]} == {"exterior_finish.name", "exterior_finish.hex"}
    assert changes["totals"]["net_change"] == 0
    scope = build_variation_quote(current, edited, changes, "2026-11-21")["public"]["scope"]
    assert any("external brick leaf" in text and "Sand" in text for text in scope)
    assert edited["settings"]["paint_hex"] == "#636569"


@pytest.mark.parametrize("update", [{"inner_leaf_finish": "paint_only"}, {"inner_brick_hex": "pink"},
                                    {"exterior_finish": {"kind": "wood"}}, {"exterior_finish": {"hex": "#FFF"}},
                                    {"exterior_finish": {"name": "   "}}])
def test_invalid_finish_selections(update):
    with pytest.raises(ValidationError):
        HouseSettings.model_validate(update)
