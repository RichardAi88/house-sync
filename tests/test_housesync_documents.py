"""Contract tests for model-linked exports, not their visual styling."""
from __future__ import annotations

import csv
import xml.etree.ElementTree as ET
from copy import deepcopy
from decimal import ROUND_HALF_UP, Decimal
from zipfile import ZipFile

import pytest
from docx import Document
from openpyxl import load_workbook
from reportlab import rl_config

from housesync.documents import drawing_primitives, drawing_sheets, export_documents


@pytest.fixture
def compiled_result():
    return {
        "settings": {"project_name": "Sample <house> & studio", "length_mm": 10000, "width_mm": 8000,
                     "height_mm": 3000, "wall_thickness_mm": 200, "paint_colour": "Light blue", "paint_hex": "#ADD8E6", "currency": "SGD"},
        "revision": "abc123def456",
        "drawing": {"length_mm": 10000, "width_mm": 8000, "height_mm": 3000, "wall_thickness_mm": 200,
                    "slab_thickness_mm": 150, "roof_thickness_mm": 200, "roof_overhang_mm": 300,
                    "openings": [{"id": "D1", "host": "W1", "start_mm": 4500, "width_mm": 1000, "height_mm": 2100, "sill_mm": 0},
                                 {"id": "N1", "host": "W3", "start_mm": 4250, "width_mm": 1500, "height_mm": 1200, "sill_mm": 900},
                                 {"id": "N2", "host": "W2", "start_mm": 3250, "width_mm": 1500, "height_mm": 1200, "sill_mm": 900}]},
        "quantities": {"floor_area_m2": 80},
        "boq": [{"code": "FLOOR", "description": "Floor slab", "quantity": 80.0, "unit": "m2", "rate": 80.0, "amount": 6400.0, "element_ids": ["S0"]},
                {"code": "PAINT", "description": "Paint Light blue #ADD8E6", "quantity": 200.0, "unit": "m2", "rate": 12.0, "amount": 2400.0, "element_ids": ["W1", "W2", "W3", "W4"]}],
        "totals": {"currency": "SGD", "subtotal": 8800.0},
        "measurement_rule": "Both wall faces; openings deducted. Reveals, wastage and coat multipliers excluded.",
    }


def test_document_families_share_revision_and_current_data(tmp_path, compiled_result):
    files = export_documents(compiled_result, tmp_path)
    assert set(files) == {"boq_xlsx", "boq_csv", "specification_docx", "specification_pdf", "drawings_svg", "drawings_pdf"}
    assert all((tmp_path / value).stat().st_size > 100 for value in files.values())
    wb = load_workbook(tmp_path / "boq.xlsx", data_only=False)
    ws = wb.active
    assert compiled_result["revision"] in ws["A3"].value
    assert ws["D7"].value == 80
    assert ws["E7"].value == 0
    assert ws["F7"].value == "=ROUND(D7*(1+E7/100),6)"
    assert ws["H7"].value == "=ROUND(F7*G7,2)"
    assert ws["H10"].value == "=SUM(H7:H8)"
    with (tmp_path / "boq.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream))
    assert rows[0][3] == compiled_result["revision"]
    assert float(rows[2][7]) == 6400
    with ZipFile(tmp_path / "specification.docx") as zipped:
        text = zipped.read("word/document.xml").decode()
    assert compiled_result["revision"] in text
    assert "Light blue" in text
    assert "#ADD8E6" in text
    svg_text = (tmp_path / "drawings.svg").read_text(encoding="utf-8")
    ET.fromstring(svg_text)
    assert compiled_result["revision"] in svg_text
    assert "Sample &lt;house&gt; &amp; studio" in svg_text
    assert "10,000 clear" in svg_text
    assert "10,400 wall overall" in svg_text
    for name in ("specification.pdf", "drawings.pdf"):
        assert (tmp_path / name).read_bytes().startswith(b"%PDF-")


def test_expanded_boq_exports_net_waste_gross_and_rates_and_specifies_each_trade(tmp_path, compiled_result, monkeypatch):
    result = deepcopy(compiled_result)
    result["settings"].update(currency="AUD", paint_coats=2, rate_source="Demonstration assumptions; no supplier quotes", rate_date="2026-11-17")
    result["boq"] = []
    definitions = [
        ("FLOOR_FINISH", "Floor finish; clear internal area", "m2", 80, 10, 65, ["ROOM1"]),
        ("CEILING", "Ceiling; clear internal area", "m2", 80, 5, 45, ["ROOM1"]),
        ("SKIRTING", "Skirting; internal perimeter less door widths", "m", 35, 10, 18, ["W1", "W2", "W3", "W4"]),
        ("INTERNAL_PLASTER", "Internal plaster/render; net internal wall faces", "m2", 102.3, 5, 28, ["W1", "W2", "W3", "W4"]),
        ("EXTERNAL_RENDER", "External render; net external wall faces", "m2", 107.1, 5, 40, ["W1", "W2", "W3", "W4"]),
        ("PAINT", "Wall paint; measured net area 209.4 m2 x 2 coats", "m2", 418.8, 0, 9, ["W1", "W2", "W3", "W4"]),
    ]
    for code, description, unit, net, waste, rate, ids in definitions:
        gross = Decimal(str(net)) * (1 + Decimal(str(waste)) / 100)
        item = {"code": code, "description": description, "unit": unit, "net_quantity": net, "wastage_percent": waste,
                "gross_quantity": float(gross), "quantity": float(gross), "rate": rate,
                "amount": float((gross * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)), "element_ids": ids}
        if code == "PAINT":
            item.update(measured_quantity=209.4, paint_coats=2)
        result["boq"].append(item)
    result["totals"] = {"currency": "AUD", "subtotal": float(sum(Decimal(str(item["amount"])) for item in result["boq"]))}
    # Uncompressed test output lets us verify the PDF carries the same trade
    # headings without adding a PDF parser to the application's dependencies.
    monkeypatch.setattr(rl_config, "pageCompression", 0)
    export_documents(result, tmp_path)
    worksheet = load_workbook(tmp_path / "boq.xlsx", data_only=False).active
    assert [cell.value for cell in worksheet[6]][3:8] == ["Net quantity", "Wastage %", "Gross quantity", "Unit rate", "Amount (AUD)"]
    assert "Illustrative AUD rates, not a tender estimate" in worksheet["A3"].value
    assert "Demonstration assumptions; no supplier quotes" in worksheet["A4"].value
    assert "2026-11-17" in worksheet["A4"].value
    with (tmp_path / "boq.csv").open(encoding="utf-8-sig", newline="") as stream:
        csv_rows = list(csv.reader(stream))
    for idx, item in enumerate(result["boq"], 7):
        assert worksheet.cell(idx, 4).value == item["net_quantity"]
        assert worksheet.cell(idx, 5).value == item["wastage_percent"]
        assert worksheet.cell(idx, 6).value == f"=ROUND(D{idx}*(1+E{idx}/100),6)"
        assert worksheet.cell(idx, 7).value == item["rate"]
        assert worksheet.cell(idx, 8).value == f"=ROUND(F{idx}*G{idx},2)"
        row = csv_rows[idx - 5]
        assert row[0] == item["code"]
        assert [float(value) for value in row[3:8]] == pytest.approx([
            item["net_quantity"], item["wastage_percent"], item["gross_quantity"], item["rate"], item["amount"],
        ])
    assert worksheet["H14"].value == "=SUM(H7:H12)"
    assert float(csv_rows[8][7]) == result["totals"]["subtotal"]
    doc = Document(tmp_path / "specification.docx")
    spec_text = " ".join([paragraph.text for paragraph in doc.paragraphs] + [cell.text for table in doc.tables for row in table.rows for cell in row.cells])
    for label in ("Floor finish", "Ceiling", "Skirting", "Internal plaster/render", "External render", "Paint measurement"):
        assert label in spec_text
        assert label.encode() in (tmp_path / "specification.pdf").read_bytes()
    for expected in ("Net 80.000000 m2; wastage 10%; gross priced quantity 88.000000 m2", "Measured net wall area 209.400000 m2; 2 coat(s)",
                     "net coated quantity 418.800000 m2", "Illustrative AUD rates, not a tender estimate", "2026-11-17"):
        assert expected in spec_text
    assert result["revision"] in spec_text


def test_drawing_uses_compiled_openings_and_new_finish(compiled_result):
    changed = deepcopy(compiled_result)
    changed["settings"].update(paint_colour="Warm white", paint_hex="#F7F5EE")
    changed["drawing"]["openings"][0]["width_mm"] = 1200
    _, _, primitives = drawing_primitives(changed)
    labels = [p["text"] for p in primitives if p["kind"] == "text"]
    assert any("D1  1,200 x 2,100" in text for text in labels)
    assert any("Warm white  #F7F5EE" in text for text in labels)
    assert any(p.get("fill") == "#F7F5EE" for p in primitives)


def test_untrusted_project_name_is_spreadsheet_text(tmp_path, compiled_result):
    compiled_result["settings"]["project_name"] = '=HYPERLINK("https://example.test")'
    export_documents(compiled_result, tmp_path)
    wb = load_workbook(tmp_path / "boq.xlsx", data_only=False)
    assert wb.active["A2"].data_type == "s"
    assert wb.active["A2"].value.startswith("'=")


def test_changed_openings_flow_into_plan_all_elevations_and_specification(tmp_path, compiled_result):
    changed = deepcopy(compiled_result)
    changed["drawing"]["openings"] = [
        {"id": "D7", "kind": "door", "host": "W3", "start_mm": 1600, "width_mm": 1200, "height_mm": 2200, "sill_mm": 0},
        {"id": "N8", "kind": "window", "host": "W4", "start_mm": 700, "width_mm": 900, "height_mm": 1000, "sill_mm": 1100},
        {"id": "N9", "kind": "window", "host": "W1", "start_mm": 4000, "width_mm": 1700, "height_mm": 1200, "sill_mm": 800},
    ]
    export_documents(changed, tmp_path)
    svg = ET.fromstring((tmp_path / "drawings.svg").read_text(encoding="utf-8"))
    labels = [node.text for node in svg.iter() if node.tag.endswith("}text")]
    assert "D1" not in labels and "N1" not in labels and "N2" not in labels
    assert {"D7", "N8", "N9", "W1", "W3", "W4"} <= set(labels)
    assert "04  Rear elevation facing north" in labels
    assert "05  Side elevation facing west" in labels
    assert "1,200 x 2,200" in labels and "1,600" in labels and "1,100" in labels
    with ZipFile(tmp_path / "specification.docx") as zipped:
        root = ET.fromstring(zipped.read("word/document.xml"))
    text = " ".join(node.text or "" for node in root.iter() if node.tag.endswith("}t"))
    assert "D7 on W3: 1,200 x 2,200 mm opening; sill 0 mm; start offset 1,600 mm" in text
    assert "N8 on W4: 900 x 1,000 mm opening; sill 1,100 mm; start offset 700 mm" in text
    assert "D1 on" not in text


def test_four_plan_hosts_respect_inner_reference_directions(compiled_result):
    changed = deepcopy(compiled_result)
    changed["drawing"]["openings"] = [
        {"id": "N1", "host": "W1", "start_mm": 1000, "width_mm": 1200, "height_mm": 1200, "sill_mm": 900},
        {"id": "N2", "host": "W2", "start_mm": 2000, "width_mm": 900, "height_mm": 1200, "sill_mm": 900},
        {"id": "N3", "host": "W3", "start_mm": 2500, "width_mm": 1000, "height_mm": 1200, "sill_mm": 900},
        {"id": "N4", "host": "W4", "start_mm": 500, "width_mm": 700, "height_mm": 1200, "sill_mm": 900},
    ]
    _, _, primitives = drawing_primitives(changed)
    boxes = [p for p in primitives if p["kind"] == "rect" and p["line_width"] == .65]
    assert len(boxes) == 4
    scale = 470 / 10400
    expected = [(120 + 1200*scale, 230 + 8200*scale, 1200*scale, 200*scale),
                (120 + 10200*scale, 230 + 5300*scale, 200*scale, 900*scale),
                (120 + 6700*scale, 230, 1000*scale, 200*scale),
                (120, 230 + 700*scale, 200*scale, 700*scale)]
    for box, dimensions in zip(boxes, expected, strict=True):
        assert tuple(box[key] for key in ("x", "y", "w", "h")) == pytest.approx(dimensions)


def test_opening_schedule_paginates_and_empty_scope_is_explicit(tmp_path, compiled_result):
    changed = deepcopy(compiled_result)
    changed["drawing"].update(length_mm=30000, width_mm=30000)
    changed["drawing"]["openings"] = [
        {"id": f"N{index+1}", "kind": "window", "host": f"W{index//10+1}", "start_mm": 200 + (index % 10)*2000,
         "width_mm": 500, "height_mm": 1000, "sill_mm": 900}
        for index in range(40)
    ]
    sheets = drawing_sheets(changed)
    assert len(sheets) == 4
    for _, height, primitives in sheets:
        assert all(0 <= primitive["y"] < height for primitive in primitives if primitive["kind"] == "text")
    export_documents(changed, tmp_path / "many")
    svg = ET.fromstring((tmp_path / "many" / "drawings.svg").read_text(encoding="utf-8"))
    assert "N40" in [node.text for node in svg.iter() if node.tag.endswith("}text")]
    assert b"/Count 4" in (tmp_path / "many" / "drawings.pdf").read_bytes()
    changed["drawing"]["openings"] = []
    export_documents(changed, tmp_path / "none")
    with ZipFile(tmp_path / "none" / "specification.docx") as zipped:
        assert b"None modelled." in zipped.read("word/document.xml")
    labels = [primitive["text"] for _, _, sheet in drawing_sheets(changed) for primitive in sheet if primitive["kind"] == "text"]
    assert "None modelled" in labels
