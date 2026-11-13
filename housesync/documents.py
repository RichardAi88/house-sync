"""Revision-linked BOQ, technical schedule and drawing exports.

All formats consume the same compiled result. Drawing geometry is composed
once as vector primitives and written to SVG and PDF without an external
SVG converter or a Blender installation.
"""
from __future__ import annotations

import csv
import html
from math import ceil
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.workbook.properties import CalcProperties
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A3, A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

INK = "#173042"
PALE = "#EAF3F7"
GREY = "#61717B"
BORDER = "#D9D9D9"


def _data(result: dict[str, Any]) -> dict[str, Any]:
    settings = result["settings"]
    return settings.model_dump() if hasattr(settings, "model_dump") else settings


def _money(result: dict[str, Any]) -> str:
    currency = result["totals"].get("currency", _data(result).get("currency", "SGD"))
    return f"{currency} {result['totals']['subtotal']:,.2f}"


def _safe_cell(value: Any) -> Any:
    """Keep arbitrary project text from being interpreted as an Excel formula."""
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _measurement(result: dict[str, Any]) -> str:
    return result.get("measurement_rule", "Quantities are derived from the compiled model. Reveals and wastage are excluded.")


def _boq(result: dict[str, Any], output: Path) -> None:
    settings = _data(result)
    currency = result["totals"].get("currency", settings.get("currency", "SGD"))
    headers = ["Item", "Description", "Unit", "Quantity", "Unit rate", f"Amount ({currency})", "Element IDs"]
    wb = Workbook()
    ws = wb.active
    ws.title = "Bill of quantities"
    ws.sheet_view.showGridLines = False
    ws.merge_cells("A1:G1")
    ws["A1"] = "House bill of quantities"
    ws["A1"].font = Font(name="Calibri", size=19, bold=True, color="000000")
    ws.row_dimensions[1].height = 30
    ws.merge_cells("A2:G2")
    ws["A2"] = _safe_cell(settings["project_name"])
    ws["A2"].font = Font(name="Calibri", size=12, color="000000")
    ws.merge_cells("A3:G3")
    ws["A3"] = f"Design revision {result['revision']} | Currency {currency} | Illustrative unit rates"
    ws.merge_cells("A4:G4")
    ws["A4"] = "Export snapshot. Edit the application to regenerate all linked outputs."
    ws["A4"].font = Font(name="Calibri", italic=True, color=GREY[1:])
    header_row = 6
    for col, value in enumerate(headers, 1):
        cell = ws.cell(header_row, col, value)
        cell.fill = PatternFill("solid", fgColor=INK[1:])
        cell.font = Font(name="Calibri", bold=True, color="FFFFFF")
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    ws.row_dimensions[header_row].height = 29
    thin = Side(style="thin", color=BORDER[1:])
    for idx, item in enumerate(result["boq"], header_row + 1):
        values = [item["code"], item["description"], item["unit"], item["quantity"], item["rate"],
                  f"=ROUND(D{idx}*E{idx},2)", ", ".join(item.get("element_ids", []))]
        for col, value in enumerate(values, 1):
            cell = ws.cell(idx, col, value if col == 6 else _safe_cell(value))
            cell.font = Font(name="Calibri", size=11, color="000000")
            cell.border = Border(bottom=thin)
            cell.alignment = Alignment(vertical="center", wrap_text=True, horizontal="left" if col in (1, 2, 7) else "right")
            if idx % 2:
                cell.fill = PatternFill("solid", fgColor=PALE[1:])
            if col == 4:
                cell.number_format = "#,##0.000"
            if col in (5, 6):
                cell.number_format = "#,##0.00"
        description_lines = ceil(len(str(item["description"])) / 48)
        reference_lines = ceil(len(", ".join(item.get("element_ids", []))) / 22)
        ws.row_dimensions[idx].height = max(42, min(300, 14 * max(description_lines, reference_lines) + 10))
    last = header_row + len(result["boq"])
    total_row = last + 2
    ws.cell(total_row, 2, "Estimated subtotal")
    ws.cell(total_row, 6, f"=SUM(F{header_row + 1}:F{last})")
    for cell in ws[total_row]:
        cell.font = Font(name="Calibri", bold=True, size=12)
        cell.fill = PatternFill("solid", fgColor="DCEBF2")
    ws.cell(total_row, 6).number_format = f'"{currency} "#,##0.00'
    note_row = total_row + 3
    notes = [_measurement(result), "Unit rates are illustrative editable assumptions, not supplier quotations.",
             "This subtotal covers only the listed modelled items. Taxes, preliminaries and unmodelled work are excluded."]
    for offset, note in enumerate(notes):
        row = note_row + offset
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=7)
        ws.cell(row, 1, note).alignment = Alignment(wrap_text=True, vertical="center")
        ws.cell(row, 1).font = Font(name="Calibri", size=10, color=GREY[1:])
        ws.row_dimensions[row].height = 110 if offset == 0 else 26
    for col, width in {"A": 12, "B": 52, "C": 10, "D": 15, "E": 15, "F": 19, "G": 25}.items():
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "D7"
    ws.auto_filter.ref = f"A6:G{last}"
    ws.print_options.horizontalCentered = True
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_area = f"A1:G{note_row + len(notes) - 1}"
    ws.oddFooter.center.text = f"Revision {result['revision']}"
    wb.calculation = CalcProperties(fullCalcOnLoad=True)
    wb.save(output / "boq.xlsx")
    with (output / "boq.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["Project", _safe_cell(settings["project_name"]), "Revision", result["revision"], "Currency", currency])
        writer.writerow(headers)
        for item in result["boq"]:
            writer.writerow([_safe_cell(item["code"]), _safe_cell(item["description"]), item["unit"], item["quantity"],
                             item["rate"], item["amount"], ", ".join(item.get("element_ids", []))])
        writer.writerow(["", "Estimated subtotal", "", "", "", result["totals"]["subtotal"]])
        writer.writerow(["Measurement", _measurement(result)])
        writer.writerow(["Rates", "Illustrative assumptions. Taxes, preliminaries and unmodelled work excluded."])


def _opening_kind(opening: dict[str, Any]) -> str:
    """Use explicit compiled kind, retaining support for older saved snapshots."""
    return opening.get("kind") or ("door" if opening["id"].startswith("D") else "window")


def _opening_requirement(opening: dict[str, Any]) -> str:
    return (f"{opening['id']} on {opening['host']}: {opening['width_mm']:,.0f} x {opening['height_mm']:,.0f} mm opening; "
            f"sill {opening['sill_mm']:,.0f} mm; start offset {opening['start_mm']:,.0f} mm along the internal wall reference.")


def _specification_sections(result: dict[str, Any]) -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[str]]:
    settings = _data(result)
    drawing = result["drawing"]
    t = drawing["wall_thickness_mm"]
    dimensions = [
        ("Internal clear plan", f"{drawing['length_mm']:,.0f} x {drawing['width_mm']:,.0f} mm"),
        ("External wall footprint", f"{drawing['length_mm'] + 2*t:,.0f} x {drawing['width_mm'] + 2*t:,.0f} mm"),
        ("Wall height above floor", f"{drawing['height_mm']:,.0f} mm"),
        ("Wall thickness", f"{t:,.0f} mm"),
        ("Modelled floor area", f"{result['quantities']['floor_area_m2']:,.3f} m2"),
    ]
    openings = drawing["openings"]
    door = [o for o in openings if _opening_kind(o) == "door"]
    windows = [o for o in openings if _opening_kind(o) == "window"]
    materials = [
        ("Floor slab", f"{drawing.get('slab_thickness_mm', 150):,.0f} mm concrete slab within the internal room footprint. Finish product to be confirmed."),
        ("Walls", f"{t:,.0f} mm uniform masonry assembly. Construction and performance requirements to be confirmed."),
        ("Flat roof", f"{drawing.get('roof_thickness_mm', 200):,.0f} mm modelled roof, overhang {drawing.get('roof_overhang_mm', 300):,.0f} mm. Waterproofing specification to be confirmed."),
        ("Paint finish", f"{settings['paint_colour']} ({settings['paint_hex']}). Manufacturer product and physical colour reference to be confirmed."),
    ]
    materials.extend((f"Door {opening['id']}", _opening_requirement(opening)) for opening in door)
    materials.extend((f"Window {opening['id']}", _opening_requirement(opening)) for opening in windows)
    if not door:
        materials.append(("Doors", "None modelled."))
    if not windows:
        materials.append(("Windows", "None modelled."))
    notes = [
        "This draft technical schedule records the selected house geometry and finishes for attachment to a separately agreed contract.",
        f"The matching BOQ and drawings carry design revision {result['revision']}. The BOQ subtotal is {_money(result)} using illustrative rates.",
        _measurement(result),
        "Dimensions are in millimetres unless stated. Plan dimensions identify internal clear sizes and external wall sizes separately. Screen HEX colour is a visual reference.",
        "Opening start offsets follow the internal reference: W1 south eastwards; W2 east northwards; W3 north westwards; W4 west southwards.",
        "Detailed construction design, foundations, services, statutory submissions, commercial terms and unlisted work require separate definition.",
    ]
    return dimensions, materials, notes


def _docx_table(document: Any, rows: list[tuple[str, str]]) -> None:
    table = document.add_table(rows=1, cols=2)
    table.autofit = False
    table.columns[0].width = Inches(1.7)
    table.columns[1].width = Inches(4.8)
    for index, row in enumerate([("Item", "Requirement")] + rows):
        cells = table.rows[0].cells if index == 0 else table.add_row().cells
        cells[0].width, cells[1].width = Inches(1.7), Inches(4.8)
        for cell, text in zip(cells, row, strict=True):
            cell.text = text
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            props = cell._tc.get_or_add_tcPr()
            borders = OxmlElement("w:tcBorders")
            for edge in ("top", "left", "bottom", "right"):
                element = OxmlElement(f"w:{edge}")
                element.set(qn("w:val"), "single")
                element.set(qn("w:sz"), "4")
                element.set(qn("w:color"), "D9D9D9")
                borders.append(element)
            props.append(borders)
            if index == 0:
                shade = OxmlElement("w:shd")
                shade.set(qn("w:fill"), "EAF3F7")
                props.append(shade)
            for paragraph in cell.paragraphs:
                paragraph.paragraph_format.space_before = Pt(4)
                paragraph.paragraph_format.space_after = Pt(4)
                for run in paragraph.runs:
                    run.font.size = Pt(9)
                    run.bold = index == 0
    document.add_paragraph().paragraph_format.space_after = Pt(0)


def _specification(result: dict[str, Any], output: Path) -> None:
    settings = _data(result)
    dimensions, materials, notes = _specification_sections(result)
    doc = Document()
    section = doc.sections[0]
    section.top_margin = section.bottom_margin = Inches(.65)
    section.left_margin = section.right_margin = Inches(.7)
    section.page_width, section.page_height = Inches(8.27), Inches(11.69)
    for name in ("Normal", "Title", "Heading 1", "Heading 2"):
        style = doc.styles[name]
        style.font.name = "Calibri"
        style.font.color.rgb = RGBColor(0, 0, 0)
    doc.styles["Normal"].font.size = Pt(9)
    doc.styles["Normal"].paragraph_format.space_after = Pt(5)
    doc.styles["Title"].font.size = Pt(23)
    doc.styles["Heading 1"].font.size = Pt(12)
    doc.styles["Heading 1"].paragraph_format.space_before = Pt(10)
    doc.add_paragraph("House technical specification", "Title")
    p = doc.add_paragraph(settings["project_name"])
    p.runs[0].bold = True
    doc.add_paragraph(f"Draft attachment to contract   |   Design revision {result['revision']}")
    doc.add_paragraph(notes[0])
    doc.add_heading("Dimensions", level=1)
    _docx_table(doc, dimensions)
    doc.add_heading("Materials and finishes", level=1)
    _docx_table(doc, materials)
    doc.add_heading("Measurement and scope", level=1)
    for note in notes[1:]:
        doc.add_paragraph(note)
    footer = section.footer.paragraphs[0]
    footer.text = f"House Sync   |   {result['revision']}   |   Draft technical schedule"
    for run in footer.runs:
        run.font.size = Pt(8)
        run.font.color.rgb = RGBColor.from_string(GREY[1:])
    doc.save(output / "specification.docx")

    styles = getSampleStyleSheet()
    title = ParagraphStyle("HouseTitle", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=22,
                           leading=26, textColor=colors.black, alignment=TA_LEFT, spaceAfter=10)
    body = ParagraphStyle("HouseBody", parent=styles["BodyText"], fontName="Helvetica", fontSize=9, leading=12, spaceAfter=7)
    heading = ParagraphStyle("HouseHeading", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=12,
                             leading=15, spaceBefore=10, spaceAfter=6, textColor=colors.black)
    story: list[Any] = [Paragraph("House technical specification", title), Paragraph(html.escape(settings["project_name"]), body),
                        Paragraph(f"Draft attachment to contract | Design revision {result['revision']}", body), Paragraph(html.escape(notes[0]), body)]
    for label, rows in (("Dimensions", dimensions), ("Materials and finishes", materials)):
        story.append(Paragraph(label, heading))
        table_data = [[Paragraph(html.escape(a), body), Paragraph(html.escape(b), body)] for a, b in [("Item", "Requirement")] + rows]
        table = Table(table_data, colWidths=[43 * mm, 130 * mm], repeatRows=1, hAlign="LEFT")
        table.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), .4, colors.HexColor(BORDER)),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(PALE)),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.extend([table, Spacer(1, 3)])
    story.append(KeepTogether([Paragraph("Measurement and scope", heading),
                               *(Paragraph(html.escape(note), body) for note in notes[1:])]))

    def footer(pdf: Any, _document: Any) -> None:
        pdf.saveState()
        pdf.setFont("Helvetica", 8)
        pdf.setFillColor(colors.HexColor(GREY))
        pdf.drawString(18 * mm, 12 * mm, f"House Sync | Revision {result['revision']} | Draft technical schedule")
        pdf.drawRightString(A4[0] - 18 * mm, 12 * mm, str(pdf.getPageNumber()))
        pdf.restoreState()

    pdf_doc = SimpleDocTemplate(str(output / "specification.pdf"), pagesize=A4, rightMargin=18 * mm,
                               leftMargin=18 * mm, topMargin=16 * mm, bottomMargin=20 * mm,
                               title=f"{settings['project_name']} technical specification", author="House Sync")
    pdf_doc.build(story, onFirstPage=footer, onLaterPages=footer)


def drawing_primitives(result: dict[str, Any]) -> tuple[float, float, list[dict[str, Any]]]:
    """Compose a dimensioned sheet in top-left logical page coordinates."""
    settings = _data(result)
    d = result["drawing"]
    length, width, height, t = (d[key] for key in ("length_mm", "width_mm", "height_mm", "wall_thickness_mm"))
    roof_t, slab_t = d.get("roof_thickness_mm", 200), d.get("slab_thickness_mm", 150)
    overhang = d.get("roof_overhang_mm", 300)
    page_w, page_h = landscape(A3)
    primitives: list[dict[str, Any]] = []

    def rect(x: float, y: float, w: float, h: float, fill: str = "none", stroke: str = INK, line_width: float = 1) -> None:
        primitives.append(dict(kind="rect", x=x, y=y, w=w, h=h, fill=fill, stroke=stroke, line_width=line_width))

    def line(x1: float, y1: float, x2: float, y2: float, stroke: str = INK, line_width: float = 1) -> None:
        primitives.append(dict(kind="line", x1=x1, y1=y1, x2=x2, y2=y2, stroke=stroke, line_width=line_width))

    def text(x: float, y: float, value: str, size: float = 10, fill: str = INK, anchor: str = "start", bold: bool = False,
             rotation: float = 0) -> None:
        primitives.append(dict(kind="text", x=x, y=y, text=str(value), size=size, fill=fill, anchor=anchor, bold=bold, rotation=rotation))

    def dim_h(x1: float, x2: float, y: float, ref_y: float, label: str) -> None:
        line(x1, ref_y, x1, y + 5, GREY, .5)
        line(x2, ref_y, x2, y + 5, GREY, .5)
        line(x1, y, x2, y, GREY, .65)
        for x in (x1, x2):
            line(x - 3, y + 3, x + 3, y - 3, GREY, .8)
        text((x1 + x2) / 2, y - 6, label, 9, GREY, "middle")

    def dim_v(y1: float, y2: float, x: float, ref_x: float, label: str) -> None:
        line(ref_x, y1, x + 5, y1, GREY, .5)
        line(ref_x, y2, x + 5, y2, GREY, .5)
        line(x, y1, x, y2, GREY, .65)
        for y in (y1, y2):
            line(x - 3, y + 3, x + 3, y - 3, GREY, .8)
        text(x - 7, (y1 + y2) / 2, label, 9, GREY, "middle", rotation=-90)

    rect(28, 28, page_w - 56, page_h - 56, "#FFFFFF", BORDER, .7)
    text(48, 64, "House plan and elevations", 24, bold=True)
    text(48, 88, settings["project_name"], 12)
    text(page_w - 48, 62, f"REV {result['revision']}", 11, anchor="end", bold=True)
    text(page_w - 48, 84, "A3 landscape | All dimensions in mm", 10, GREY, "end")
    line(48, 108, page_w - 48, 108, BORDER, .8)

    # Floor plan. The model origin is the internal southwest corner.
    text(68, 145, "01  Floor plan", 14, bold=True)
    text(68, 164, "Internal clear sizes and external wall footprint", 9, GREY)
    scale = min(470 / (length + 2*t), 380 / (width + 2*t))
    px, py = 120.0, 230.0
    ew, eh = (length + 2*t)*scale, (width + 2*t)*scale
    rect(px, py, ew, eh, settings["paint_hex"], INK, 1.1)
    rect(px + t*scale, py + t*scale, length*scale, width*scale, "#FFFFFF", INK, .7)
    for opening in d["openings"]:
        host, start, ow = opening["host"], opening["start_mm"], opening["width_mm"]
        fill = "#FFFFFF" if _opening_kind(opening) == "door" else "#DCEFF7"
        if host == "W1":
            x, y, w, h = px + (t+start)*scale, py + (width+t)*scale, ow*scale, t*scale
            label_x, label_y = x + w/2, y - 8
        elif host == "W2":
            x, y, w, h = px + (length+t)*scale, py + (width+t-start-ow)*scale, t*scale, ow*scale
            label_x, label_y = x - 16, y + h/2
        elif host == "W3":
            x, y, w, h = px + (t+length-start-ow)*scale, py, ow*scale, t*scale
            label_x, label_y = x + w/2, y + h + 17
        else:
            x, y, w, h = px, py + (t+start)*scale, t*scale, ow*scale
            label_x, label_y = x + w + 16, y + h/2
        rect(x, y, w, h, fill, INK, .65)
        text(label_x, label_y, opening["id"], 9, anchor="middle")
    text(px + ew/2, py + eh/2 - 4, "HOUSE", 13, anchor="middle", bold=True)
    text(px + ew/2, py + eh/2 + 16, f"{result['quantities']['floor_area_m2']:,.2f} m2 floor area", 10, GREY, "middle")
    dim_h(px, px + ew, py - 37, py, f"{length+2*t:,.0f} overall")
    dim_h(px+t*scale, px+(t+length)*scale, py+eh+33, py+eh, f"{length:,.0f} clear")
    dim_v(py, py+eh, px-38, px, f"{width+2*t:,.0f}")
    dim_v(py+t*scale, py+(t+width)*scale, px+ew+40, px+ew, f"{width:,.0f} clear")
    nx, ny = px+ew+48, py-47
    line(nx, ny+22, nx, ny, INK, 1.2)
    line(nx, ny, nx-4, ny+9, INK, 1.2)
    line(nx, ny, nx+4, ny+9, INK, 1.2)
    text(nx, ny-7, "N", 10, anchor="middle", bold=True)

    # Front and east elevations use the same compiled opening dimensions.
    for host, label, run, top in (("W1", "02  Front elevation facing south", length, 155),
                                   ("W2", "03  Side elevation facing east", width, 455)):
        ex, ey = 730.0, top + 68
        es = min(350 / (run+2*t+2*overhang), 145 / (height+roof_t+slab_t))
        ewidth, eheight = (run+2*t)*es, height*es
        text(710, top, label, 13, bold=True)
        rect(ex-overhang*es, ey, ewidth+2*overhang*es, roof_t*es, "#D8DFE3", INK, .8)
        rect(ex, ey+roof_t*es, ewidth, eheight, settings["paint_hex"], INK, .8)
        floor_y = ey+(roof_t+height)*es
        rect(ex+t*es, floor_y, run*es, slab_t*es, "#E5E8EA", INK, .8)
        line(ex-12, floor_y+slab_t*es, ex+ewidth+12, floor_y+slab_t*es, GREY, .7)
        for opening in d["openings"]:
            if opening["host"] != host:
                continue
            ox = ex+(t+opening["start_mm"])*es
            oy = floor_y-(opening["sill_mm"]+opening["height_mm"])*es
            rect(ox, oy, opening["width_mm"]*es, opening["height_mm"]*es,
                 "#E2F2F8" if _opening_kind(opening) == "window" else "#F4F0E9", INK, .7)
            text(ox+opening["width_mm"]*es/2, oy-7, opening["id"], 9, anchor="middle")
        host_openings = [opening for opening in d["openings"] if opening["host"] == host]
        if len(host_openings) == 1:
            opening = host_openings[0]
            text(ex+ewidth/2, floor_y+slab_t*es+46,
                 f"{opening['id']}  {opening['width_mm']:,.0f} x {opening['height_mm']:,.0f}  sill {opening['sill_mm']:,.0f}",
                 9, GREY, "middle")
        elif host_openings:
            text(ex+ewidth/2, floor_y+slab_t*es+46, "Opening sizes and offsets: see schedule A002", 8, GREY, "middle")
        dim_h(ex, ex+ewidth, floor_y+slab_t*es+26, floor_y+slab_t*es, f"{run+2*t:,.0f} wall overall")
        dim_v(ey+roof_t*es, floor_y, ex-30, ex, f"{height:,.0f}")

    text(68, 697, f"Wall thickness {t:,.0f} mm | Roof thickness {roof_t:,.0f} mm | Roof overhang {overhang:,.0f} mm | Slab thickness {slab_t:,.0f} mm", 10)
    rect(68, 714, 24, 15, settings["paint_hex"], BORDER, .6)
    text(102, 726, f"Paint finish  {settings['paint_colour']}  {settings['paint_hex']}", 10)
    text(68, 754, "Diagrammatic model drawings. Dimensions govern. Confirm construction details before issue.", 9, GREY)
    line(48, page_h-64, page_w-48, page_h-64, BORDER, .8)
    text(48, page_h-44, f"House Sync | Design revision {result['revision']}", 9, GREY)
    text(page_w-48, page_h-44, "Sheet A001 | Draft", 9, GREY, "end")
    return page_w, page_h, primitives


def drawing_sheets(result: dict[str, Any]) -> list[tuple[float, float, list[dict[str, Any]]]]:
    """Coordinate the main drawing, remaining elevations and a paginated opening schedule."""
    sheets = [drawing_primitives(result)]
    settings, d = _data(result), result["drawing"]
    length, width, height, t = (d[key] for key in ("length_mm", "width_mm", "height_mm", "wall_thickness_mm"))
    roof_t, slab_t = d.get("roof_thickness_mm", 200), d.get("slab_thickness_mm", 150)
    overhang = d.get("roof_overhang_mm", 300)
    page_w, page_h = landscape(A3)
    openings = list(d["openings"])
    chunks = [openings[:10]]
    chunks.extend(openings[index:index + 24] for index in range(10, len(openings), 24))
    for sheet_index, chunk in enumerate(chunks, 2):
        primitives: list[dict[str, Any]] = []

        def rect(x, y, w, h, fill="none", stroke=INK, line_width=1, *, _target=primitives):
            _target.append(dict(kind="rect", x=x, y=y, w=w, h=h, fill=fill, stroke=stroke, line_width=line_width))

        def line(x1, y1, x2, y2, stroke=INK, line_width=1, *, _target=primitives):
            _target.append(dict(kind="line", x1=x1, y1=y1, x2=x2, y2=y2, stroke=stroke, line_width=line_width))

        def text(x, y, value, size=10, fill=INK, anchor="start", bold=False, rotation=0, *, _target=primitives):
            _target.append(dict(kind="text", x=x, y=y, text=str(value), size=size, fill=fill,
                                   anchor=anchor, bold=bold, rotation=rotation))

        rect(28, 28, page_w - 56, page_h - 56, "#FFFFFF", BORDER, .7)
        text(48, 64, "Elevations and opening schedule" if sheet_index == 2 else "Opening schedule continued", 24, bold=True)
        text(48, 88, settings["project_name"], 12)
        text(page_w - 48, 62, f"REV {result['revision']}", 11, anchor="end", bold=True)
        text(page_w - 48, 84, "A3 landscape | All dimensions in mm", 10, GREY, "end")
        line(48, 108, page_w - 48, 108, BORDER, .8)
        if sheet_index == 2:
            for host, label, run, ex in (("W3", "04  Rear elevation facing north", length, 100),
                                         ("W4", "05  Side elevation facing west", width, 670)):
                text(ex - 20, 145, label, 14, bold=True)
                es = min(420 / (run + 2*t + 2*overhang), 140 / (height + roof_t + slab_t))
                ey = 195.0
                ewidth, eheight = (run + 2*t)*es, height*es
                rect(ex - overhang*es, ey, ewidth + 2*overhang*es, roof_t*es, "#D8DFE3", INK, .8)
                rect(ex, ey + roof_t*es, ewidth, eheight, settings["paint_hex"], INK, .8)
                floor_y = ey + (roof_t + height)*es
                rect(ex + t*es, floor_y, run*es, slab_t*es, "#E5E8EA", INK, .8)
                line(ex - 12, floor_y + slab_t*es, ex + ewidth + 12, floor_y + slab_t*es, GREY, .7)
                for opening in openings:
                    if opening["host"] != host:
                        continue
                    ox = ex + (t + opening["start_mm"])*es
                    oy = floor_y - (opening["sill_mm"] + opening["height_mm"])*es
                    rect(ox, oy, opening["width_mm"]*es, opening["height_mm"]*es,
                         "#E2F2F8" if _opening_kind(opening) == "window" else "#F4F0E9", INK, .7)
                    text(ox + opening["width_mm"]*es/2, oy - 7, opening["id"], 9, anchor="middle")
                dy = floor_y + slab_t*es + 25
                line(ex, dy, ex + ewidth, dy, GREY, .65)
                for x in (ex, ex + ewidth):
                    line(x, floor_y + slab_t*es, x, dy + 5, GREY, .5)
                    line(x - 3, dy + 3, x + 3, dy - 3, GREY, .8)
                text(ex + ewidth/2, dy - 6, f"{run + 2*t:,.0f} wall overall", 9, GREY, "middle")
                text(ex + ewidth/2, dy + 19, f"Wall height {height:,.0f} | Opening sizes and offsets below", 9, GREY, "middle")
        top = 430 if sheet_index == 2 else 145
        text(68, top, "Opening schedule" + (" continued" if sheet_index > 2 else ""), 14, bold=True)
        text(68, top + 20, "Start and end offsets use the internal clear wall line. Sizes identify modelled opening voids.", 9, GREY)
        x0, y0, row_height = 68.0, top + 35, 22.0
        widths = [70, 90, 130, 180, 130, 210, page_w - 136 - 810]
        headings = ["ID", "Type", "Wall", "Opening W x H", "Sill", "Start offset", "End clearance"]
        rows = []
        for opening in chunk:
            run = length if opening["host"] in {"W1", "W3"} else width
            end = run - opening["start_mm"] - opening["width_mm"]
            rows.append([opening["id"], _opening_kind(opening).capitalize(), opening["host"],
                         f"{opening['width_mm']:,.0f} x {opening['height_mm']:,.0f}", f"{opening['sill_mm']:,.0f}",
                         f"{opening['start_mm']:,.0f}", f"{end:,.0f}"])
        if not rows:
            rows = [["-", "None modelled", "-", "-", "-", "-", "-"]]
        for row_index, row in enumerate([headings, *rows]):
            x, y = x0, y0 + row_height*row_index
            for value, cell_width in zip(row, widths, strict=True):
                rect(x, y, cell_width, row_height, INK if row_index == 0 else PALE if row_index % 2 else "#FFFFFF", BORDER, .4)
                text(x + 7, y + 15, value, 9, "#FFFFFF" if row_index == 0 else INK, bold=row_index == 0)
                x += cell_width
        text(68, 750, "Reference directions: W1 south eastwards; W2 east northwards; W3 north westwards; W4 west southwards.", 9, GREY)
        line(48, page_h - 64, page_w - 48, page_h - 64, BORDER, .8)
        text(48, page_h - 44, f"House Sync | Design revision {result['revision']}", 9, GREY)
        text(page_w - 48, page_h - 44, f"Sheet A{sheet_index:03} | Draft", 9, GREY, "end")
        sheets.append((page_w, page_h, primitives))
    return sheets


def _drawings(result: dict[str, Any], output: Path) -> None:
    sheets = drawing_sheets(result)
    width = sheets[0][0]
    svg_height = sum(sheet[1] for sheet in sheets) + 12 * (len(sheets) - 1)
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.2f}pt" height="{svg_height:.2f}pt" viewBox="0 0 {width:.2f} {svg_height:.2f}">',
           f'<title>{html.escape(_data(result)["project_name"])} drawings revision {result["revision"]}</title>',
           '<rect width="100%" height="100%" fill="white"/>']
    pdf = canvas.Canvas(str(output / "drawings.pdf"), pagesize=sheets[0][:2])
    pdf.setTitle(f"{_data(result)['project_name']} drawings revision {result['revision']}")
    pdf.setAuthor("House Sync")
    offset_y = 0.0
    for width, height, primitives in sheets:
        svg.append(f'<g transform="translate(0 {offset_y:.3f})">')
        pdf.setPageSize((width, height))
        for p in primitives:
            if p["kind"] == "rect":
                svg.append(f'<rect x="{p["x"]:.3f}" y="{p["y"]:.3f}" width="{p["w"]:.3f}" height="{p["h"]:.3f}" fill="{p["fill"]}" stroke="{p["stroke"]}" stroke-width="{p["line_width"]}"/>')
                pdf.setStrokeColor(colors.HexColor(p["stroke"]))
                pdf.setLineWidth(p["line_width"])
                fill = p["fill"] != "none"
                if fill:
                    pdf.setFillColor(colors.HexColor(p["fill"]))
                pdf.rect(p["x"], height-p["y"]-p["h"], p["w"], p["h"], fill=int(fill), stroke=1)
            elif p["kind"] == "line":
                svg.append(f'<line x1="{p["x1"]:.3f}" y1="{p["y1"]:.3f}" x2="{p["x2"]:.3f}" y2="{p["y2"]:.3f}" stroke="{p["stroke"]}" stroke-width="{p["line_width"]}"/>')
                pdf.setStrokeColor(colors.HexColor(p["stroke"]))
                pdf.setLineWidth(p["line_width"])
                pdf.line(p["x1"], height-p["y1"], p["x2"], height-p["y2"])
            else:
                weight = "bold" if p["bold"] else "normal"
                transform = f' transform="rotate({p["rotation"]} {p["x"]:.3f} {p["y"]:.3f})"' if p["rotation"] else ""
                svg.append(f'<text x="{p["x"]:.3f}" y="{p["y"]:.3f}" font-family="Arial, sans-serif" font-size="{p["size"]}" font-weight="{weight}" text-anchor="{p["anchor"]}" fill="{p["fill"]}"{transform}>{html.escape(p["text"])}</text>')
                pdf.setFillColor(colors.HexColor(p["fill"]))
                pdf.setFont("Helvetica-Bold" if p["bold"] else "Helvetica", p["size"])
                draw = {"start": pdf.drawString, "middle": pdf.drawCentredString, "end": pdf.drawRightString}[p["anchor"]]
                if p["rotation"]:
                    pdf.saveState()
                    pdf.translate(p["x"], height-p["y"])
                    pdf.rotate(-p["rotation"])
                    draw(0, 0, p["text"])
                    pdf.restoreState()
                else:
                    draw(p["x"], height-p["y"], p["text"])
        svg.append("</g>")
        offset_y += height + 12
        pdf.showPage()
    svg.append("</svg>")
    (output / "drawings.svg").write_text("\n".join(svg), encoding="utf-8")
    pdf.save()


def export_documents(result: dict[str, Any], output_dir: Path) -> dict[str, str]:
    """Write all three document families from a single model revision."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    _boq(result, output_dir)
    _specification(result, output_dir)
    _drawings(result, output_dir)
    return {"boq_xlsx": "boq.xlsx", "boq_csv": "boq.csv", "specification_docx": "specification.docx",
            "specification_pdf": "specification.pdf", "drawings_svg": "drawings.svg", "drawings_pdf": "drawings.pdf"}
