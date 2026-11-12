"""Contract tests for model-linked exports, not their visual styling."""
from __future__ import annotations

import csv
import xml.etree.ElementTree as ET
from copy import deepcopy
from zipfile import ZipFile

import pytest
from openpyxl import load_workbook

from housesync.documents import drawing_primitives, export_documents


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
    assert ws["F7"].value == "=ROUND(D7*E7,2)"
    assert ws["F10"].value == "=SUM(F7:F8)"
    with (tmp_path / "boq.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream))
    assert rows[0][3] == compiled_result["revision"]
    assert float(rows[2][5]) == 6400
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
