"""Room/partition drawings and the saved, reconciling room BOQ schedules."""
from __future__ import annotations

import csv
import xml.etree.ElementTree as ET
from copy import deepcopy
from decimal import Decimal
from zipfile import ZipFile

import pytest
from openpyxl import load_workbook
from reportlab import rl_config
from reportlab.pdfbase.pdfmetrics import stringWidth

from housesync.documents import drawing_primitives, drawing_sheets, export_documents


@pytest.fixture
def rooms_result():
    rooms = [
        {"id": "ROOM1", "name": "Living & Dining", "outline_mm": [[0, 0], [3950, 0], [3950, 8000], [0, 8000]],
         "area_m2": 31.6, "label_point_mm": [1975, 4000],
         "finishes": {"floor": "Timber <oak> & finish", "ceiling": "White plasterboard", "skirting": "Timber 100 mm", "paint": "Soft blue"}},
        {"id": "ROOM2", "name": "Bedroom", "outline_mm": [[4050, 0], [10000, 0], [10000, 8000], [4050, 8000]],
         "area_m2": 47.6, "label_point_mm": [7025, 4000],
         "finishes": {"floor": "Ceramic tile", "ceiling": "Acoustic lining", "skirting": "Tile 100 mm", "paint": "Warm white"}},
    ]
    partitions = [{"id": "P1", "start_mm": [4000, 0], "end_mm": [4000, 8000], "thickness_mm": 100,
                   "height_mm": 3000, "wall_type": "Stud partition", "footprint_mm": [[3950, 0], [4050, 0], [4050, 8000], [3950, 8000]]}]
    definitions = [
        ("SHELL", "Shell", "WALL", 20, 0, 50, "m3"), ("SHELL", "Shell", "EXTERNAL_RENDER", 10, 0, 50, "m2"),
        ("ROOM1", "Living & Dining", "FLOOR_FINISH", 31.6, 10, 65, "m2"),
        ("ROOM1", "Living & Dining", "CEILING", 31.6, 5, 45, "m2"), ("ROOM1", "Living & Dining", "PAINT", 50, 0, 9, "m2"),
        ("ROOM2", "Bedroom", "FLOOR_FINISH", 47.6, 10, 65, "m2"),
        ("ROOM2", "Bedroom", "CEILING", 47.6, 5, 45, "m2"), ("ROOM2", "Bedroom", "PAINT", 70, 0, 9, "m2"),
    ]
    boq = []
    for group_id, group_name, rate_code, net, waste, rate, unit in definitions:
        gross = Decimal(str(net)) * (1 + Decimal(str(waste))/100)
        boq.append({"code": f"{group_id}_{rate_code}", "rate_code": rate_code, "group_id": group_id, "group_name": group_name,
                    "description": f"{group_name} {rate_code}", "quantity": float(gross), "gross_quantity": float(gross),
                    "net_quantity": net, "measured_quantity": net/2 if rate_code == "PAINT" else net,
                    "paint_coats": 2, "wastage_percent": waste, "unit": unit, "rate": rate,
                    "amount": float(gross * rate), "element_ids": [group_id]})
    return {
        "settings": {"project_name": "Room drawing audit", "length_mm": 10000, "width_mm": 8000, "height_mm": 3000,
                     "wall_thickness_mm": 200, "paint_colour": "Light blue", "paint_hex": "#ADD8E6", "currency": "AUD", "paint_coats": 2},
        "revision": "abc123def456", "rooms": rooms, "partitions": partitions,
        "drawing": {"length_mm": 10000, "width_mm": 8000, "height_mm": 3000, "wall_thickness_mm": 200,
                    "slab_thickness_mm": 150, "roof_thickness_mm": 200, "roof_overhang_mm": 300,
                    "rooms": rooms, "partitions": partitions,
                    "openings": [
                        {"id": "D7", "kind": "door", "host": "P1", "start_mm": 3000, "width_mm": 900, "height_mm": 2100, "sill_mm": 0},
                        {"id": "N8", "kind": "window", "host": "P1", "start_mm": 5000, "width_mm": 1200, "height_mm": 1000, "sill_mm": 900},
                        {"id": "N9", "kind": "window", "host": "W4", "start_mm": 700, "width_mm": 900, "height_mm": 1000, "sill_mm": 900},
                    ]},
        "quantities": {"floor_area_m2": 80}, "boq": boq,
        "boq_groups": [{"id": "SHELL", "name": "Shell", "subtotal": 1500},
                       {"id": "ROOM1", "name": "Living & Dining", "subtotal": 4202.5},
                       {"id": "ROOM2", "name": "Bedroom", "subtotal": 6282.5}],
        "totals": {"currency": "AUD", "subtotal": 11985},
        "rate_library": {"sha256": "a" * 64, "currency": "AUD", "rows": {
            "FLOOR_FINISH": {"rate": 65, "currency": "AUD", "source": "illustrative", "effective_date": "2026-11-17", "notes": "Finish allowance"}}},
        "measurement_rule": "Compiled clear room areas and wall faces; room groups reconcile to the house estimate.",
    }


def test_plan_shows_names_clear_areas_partition_dimension_and_internal_openings(rooms_result):
    _, _, primitives = drawing_primitives(rooms_result)
    labels = [item["text"] for item in primitives if item["kind"] == "text"]
    for text in ("Living & Dining", "Bedroom", "31.60 m2 clear", "47.60 m2 clear", "P1  8,000 mm"):
        assert text in labels
    # Each internal opening occurs on the plan, with no exterior-elevation copy.
    assert labels.count("D7") == labels.count("N8") == 1
    voids = [item for item in primitives if item["kind"] == "polygon" and item["line_width"] == .65]
    assert len(voids) == 2
    scale = 470 / 10400
    for opening, void in zip(rooms_result["drawing"]["openings"], voids, strict=False):
        x = [point[0] for point in void["points"]]
        y = [point[1] for point in void["points"]]
        assert min(x) == pytest.approx(120 + (200 + 3950)*scale)
        assert max(x) - min(x) == pytest.approx(100*scale)
        assert max(y) - min(y) == pytest.approx(opening["width_mm"]*scale)


def test_opening_schedule_uses_partition_host_run_and_original_direction(rooms_result):
    changed = deepcopy(rooms_result)
    changed["partitions"].append({"id": "P2", "start_mm": [4000, 4000], "end_mm": [0, 4000], "thickness_mm": 100, "height_mm": 3000, "wall_type": "Stud"})
    changed["drawing"]["openings"].append({"id": "D8", "kind": "door", "host": "P2", "start_mm": 500, "width_mm": 900, "height_mm": 2100, "sill_mm": 0})
    labels = [item["text"] for _, _, primitives in drawing_sheets(changed) for item in primitives if item["kind"] == "text"]
    assert "P2  4,000 mm" in labels
    assert "2,600" in labels  # 4000 mm host minus 500 start minus 900 opening.
    assert any("Partition offsets follow" in text for text in labels)


def test_room_finish_schedule_and_scope_survive_word_pdf_svg_exports(tmp_path, rooms_result, monkeypatch):
    monkeypatch.setattr(rl_config, "pageCompression", 0)
    export_documents(rooms_result, tmp_path)
    with ZipFile(tmp_path / "specification.docx") as package:
        root = ET.fromstring(package.read("word/document.xml"))
    text = " ".join(node.text or "" for node in root.iter() if node.tag.endswith("}t"))
    for value in ("Room finish schedule", "ROOM1 - Living & Dining", "Timber <oak> & finish", "Ceramic tile", "31.600 m2", "47.600 m2",
                  "Partition P1", "Stud partition", "Living & Dining - Floor finish", "Bedroom - Paint measurement"):
        assert value in text
    pdf = (tmp_path / "specification.pdf").read_bytes()
    for value in (b"Room finish schedule", b"Timber", b"Ceramic tile", b"31.600", b"47.600", b"Stud partition"):
        assert value in pdf
    svg = (tmp_path / "drawings.svg").read_text(encoding="utf-8")
    ET.fromstring(svg)
    assert "Living &amp; Dining" in svg and "31.60 m2 clear" in svg and "<polygon" in svg
    assert (tmp_path / "drawings.pdf").read_bytes().startswith(b"%PDF-")


def test_room_boq_headings_subtotals_and_grand_total_do_not_double_count(tmp_path, rooms_result):
    export_documents(rooms_result, tmp_path)
    worksheet = load_workbook(tmp_path / "boq.xlsx", data_only=False).active
    assert worksheet["A7"].value == "SHELL - Shell"
    assert worksheet["A11"].value == "ROOM1 - Living & Dining"
    assert worksheet["A16"].value == "ROOM2 - Bedroom"
    assert worksheet["H10"].value == "=SUM(H8:H9)"
    assert worksheet["H15"].value == "=SUM(H12:H14)"
    assert worksheet["H20"].value == "=SUM(H17:H19)"
    assert worksheet["H22"].value == "=SUM(H10,H15,H20)"
    with (tmp_path / "boq.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream))
    subtotals = [Decimal(row[7]) for row in rows if len(row) > 7 and row[1].endswith(" subtotal") and row[1] != "Estimated subtotal"]
    assert subtotals == [Decimal("1500"), Decimal("4202.5"), Decimal("6282.5")]
    assert sum(subtotals) == Decimal(str(rooms_result["totals"]["subtotal"]))
    rate_sheet = load_workbook(tmp_path / "boq.xlsx")["Rate provenance"]
    row = next(row for row in rate_sheet.iter_rows(values_only=True) if row[0] == "ROOM1_FLOOR_FINISH")
    assert row[4] == 65 and row[8] == "illustrative"  # Rate-code lookup, not the unique room BOQ code.


def test_inconsistent_saved_group_total_is_rejected(tmp_path, rooms_result):
    rooms_result["boq_groups"][1]["subtotal"] += 1
    with pytest.raises(ValueError, match="does not reconcile"):
        export_documents(rooms_result, tmp_path)


def test_default_legacy_boq_positions_remain_unshifted(tmp_path, rooms_result):
    result = deepcopy(rooms_result)
    result.pop("rooms")
    result.pop("partitions")
    result["drawing"].pop("rooms")
    result["drawing"].pop("partitions")
    result["drawing"]["openings"] = []
    export_documents(result, tmp_path)
    worksheet = load_workbook(tmp_path / "boq.xlsx", data_only=False).active
    assert worksheet["A6"].value == "Item"
    assert worksheet["A7"].value == result["boq"][0]["code"]
    assert worksheet["F7"].value == "=ROUND(D7*(1+E7/100),6)"
    assert worksheet["H7"].value == "=ROUND(F7*G7,2)"


def test_compiled_two_room_snapshot_exports_its_actual_groups_and_finishes(tmp_path):
    from housesync.engine import build_house
    from housesync.layout import derive_layout
    from housesync.schema import HouseSettings

    settings = HouseSettings().model_dump(mode="json")
    settings["partitions"] = [{"id": "P1", "start_mm": [4000, 0], "end_mm": [4000, 8000], "thickness_mm": 100,
                               "height_mm": 3000, "wall_type": "Stud partition"}]
    settings["openings"].append({"id": "D2", "kind": "door", "host": "P1", "offset_mm": 3000,
                                  "width_mm": 900, "height_mm": 2100, "sill_mm": 0})
    layout = derive_layout(HouseSettings.model_validate(settings))
    settings["rooms"] = [{"id": room["id"], "name": name, "finishes": {"floor": finish, "paint": paint}}
                         for room, name, finish, paint in zip(layout["rooms"], ["Living & Dining", "Bedroom"],
                                                             ["Timber floor", "Ceramic tile"], ["Light blue", "Warm white"], strict=True)]
    result = build_house(HouseSettings.model_validate(settings), tmp_path / "compiled")
    assert [room["area_m2"] for room in result["rooms"]] == [31.6, 47.6]
    export_documents(result, tmp_path / "documents")
    _, _, primitives = drawing_primitives(result)
    labels = [item["text"] for item in primitives if item["kind"] == "text"]
    assert labels.count("D2") == 1
    assert {"Living & Dining", "Bedroom", "31.60 m2 clear", "47.60 m2 clear"} <= set(labels)
    worksheet = load_workbook(tmp_path / "documents" / "boq.xlsx", data_only=False).active
    subtotal_cells = []
    for group in result["boq_groups"]:
        heading = next(row[0] for row in worksheet.iter_rows() if row[0].value == f"{group['id']} - {group['name']}")
        subtotal_row = next(row for row in worksheet.iter_rows() if row[1].value == f"{group['name']} subtotal")
        assert subtotal_row[7].value.startswith(f"=SUM(H{heading.row + 1}:")
        subtotal_cells.append(subtotal_row[7].coordinate)
    grand = next(row[7] for row in worksheet.iter_rows() if row[1].value == "Estimated subtotal")
    assert grand.value == "=SUM(" + ",".join(subtotal_cells) + ")"
    assert sum(Decimal(str(group["subtotal"])) for group in result["boq_groups"]) == Decimal(str(result["totals"]["subtotal"]))
    with ZipFile(tmp_path / "documents" / "specification.docx") as package:
        content = package.read("word/document.xml")
    assert b"Room finish schedule" in content and b"Timber floor" in content and b"Warm white" in content


@pytest.mark.parametrize("name", ["A"*120, ("Living Dining Area "*7)[:120]])
def test_long_name_in_narrow_room_uses_bounded_reference_and_full_name_schedule(tmp_path, rooms_result, name):
    room = rooms_result["rooms"][0]
    room.update(name=name, outline_mm=[[0, 0], [900, 0], [900, 900], [0, 900]], label_point_mm=[450, 450], area_m2=.81)
    _, _, plan = drawing_primitives(rooms_result)
    labels = [item for item in plan if item.get("room_id") == room["id"] and item["text"]]
    assert labels and labels[0]["text"] == "R1"
    assert all(item["room_label_abbreviated"] for item in labels)
    assert name not in [item["text"] for item in plan if item["kind"] == "text"]
    # Independent typography/coordinate check against the 900 x 900 mm clear
    # rectangle: no displayed name/reference/area extends through its walls.
    scale = 470 / 10400
    left, right = 120 + 200*scale, 120 + 1100*scale
    top, bottom = 230 + (200 + 8000 - 900)*scale, 230 + (200 + 8000)*scale
    for item in labels:
        width = stringWidth(item["text"], "Helvetica-Bold" if item["bold"] else "Helvetica", item["size"])
        assert left <= item["x"] - width/2 < item["x"] + width/2 <= right
        assert top <= item["y"] - item["size"] < item["y"] + item["size"]*.25 <= bottom
    sheets = drawing_sheets(rooms_result)
    assert len(sheets) == 3
    schedule = sheets[-1][2]
    assert "Room name and clear area schedule" in [item["text"] for item in schedule if item["kind"] == "text"]
    name_lines = [item["text"] for item in schedule if item.get("full_room_name") == name]
    assert "".join(name_lines).replace(" ", "") == name.replace(" ", "")
    assert any(item.get("text") == room["id"] for item in schedule)
    export_documents(rooms_result, tmp_path)
    svg = ET.fromstring((tmp_path / "drawings.svg").read_text(encoding="utf-8"))
    assert any(node.attrib.get("aria-label") == name for node in svg.iter())


def test_named_single_room_keeps_name_and_area_without_an_extra_schedule(rooms_result):
    result = deepcopy(rooms_result)
    room = result["rooms"][0]
    room.update(name="Studio & Reading", outline_mm=[[0, 0], [10000, 0], [10000, 8000], [0, 8000]],
                label_point_mm=[5000, 4000], area_m2=80)
    result["rooms"] = [room]
    result["partitions"] = []
    result["drawing"]["openings"] = []
    _, _, plan = drawing_primitives(result)
    labels = [item["text"] for item in plan if item["kind"] == "text"]
    assert "Studio & Reading" in labels and "80.00 m2 clear" in labels
    assert not any(item.get("room_label_abbreviated") for item in plan)
    assert len(drawing_sheets(result)) == 2
