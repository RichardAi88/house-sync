"""Compare immutable house revisions and export their estimated BOQ difference.

Saved snapshots remain the authority. Matching is by BOQ code, never row order,
and all monetary calculations use decimal half-up rounding to two places.
"""
from __future__ import annotations

import csv
import html
import json
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.workbook.properties import CalcProperties
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

CENT = Decimal("0.01")
ZERO = Decimal(0)
INK = "173042"
TEAL = "1B6D65"
PALE = "EAF3F7"

FIELD_LABELS = {
    "project_name": ("Project name", "metadata"),
    "length_mm": ("Internal length", "dimensions"),
    "width_mm": ("Internal width", "dimensions"),
    "height_mm": ("Wall height", "dimensions"),
    "wall_thickness_mm": ("Wall thickness", "dimensions"),
    "paint_colour": ("Paint finish", "finish"),
    "paint_hex": ("Paint colour reference", "finish"),
    "currency": ("Currency", "pricing"),
    "rates.floor_m2": ("Floor rate / m2", "rates"),
    "rates.wall_m3": ("Wall construction rate / m3", "rates"),
    "rates.roof_m2": ("Roof rate / m2", "rates"),
    "rates.paint_m2": ("Paint rate / m2", "rates"),
    "rates.opening_each": ("Door/window allowance / each", "rates"),
}


def _decimal(value: Any, name: str, *, money: bool = False) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{name} must be a finite, non-negative number")
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0:
            raise ValueError(f"{name} must be a finite, non-negative number")
        if money and number != number.quantize(CENT):
            raise ValueError(f"{name} must have no more than two decimal places")
        return number
    except (InvalidOperation, TypeError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite, non-negative number") from exc


def _rounded(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _float(value: Decimal) -> float:
    return float(value) if value else 0.0


def _text(value: Any) -> str:
    if value is None:
        return "Not set"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (int, float, Decimal)):
        return f"{Decimal(str(value)):,.6f}".rstrip("0").rstrip(".")
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _flat(settings: dict, prefix: str = "") -> dict[str, Any]:
    flattened = {}
    for key, value in settings.items():
        field = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flattened.update(_flat(value, field))
        else:
            flattened[field] = value
    return flattened



LEGACY_OPENINGS = (
    {"id": "D1", "kind": "door", "host": "W1", "offset_mm": None, "width_mm": 1000, "height_mm": 2100, "sill_mm": 0},
    {"id": "N1", "kind": "window", "host": "W3", "offset_mm": None, "width_mm": 1500, "height_mm": 1200, "sill_mm": 900},
    {"id": "N2", "kind": "window", "host": "W2", "offset_mm": None, "width_mm": 1500, "height_mm": 1200, "sill_mm": 900},
)
WALL_LABELS = {"W1": "south, eastwards", "W2": "east, northwards", "W3": "north, westwards", "W4": "west, southwards"}


def _openings(settings: dict) -> dict[str, dict]:
    """Normalise historical implicit openings without changing saved snapshots."""
    source = settings.get("openings", LEGACY_OPENINGS)
    if not isinstance(source, (list, tuple)) or len(source) > 40:
        raise ValueError("Saved openings must be a list of at most 40 elements")
    result = {}
    for opening in source:
        if not isinstance(opening, dict) or not isinstance(opening.get("id"), str) or not re.fullmatch(r"[DN][1-9][0-9]{0,2}", opening["id"]):
            raise ValueError("Saved openings require valid stable D/N element IDs")
        identity = opening["id"]
        if identity in result:
            raise ValueError(f"Saved openings have duplicate element ID {identity}")
        if opening.get("kind") not in {"door", "window"} or opening.get("host") not in WALL_LABELS:
            raise ValueError(f"Saved opening {identity} has an invalid kind or host")
        for field in ("width_mm", "height_mm", "sill_mm"):
            number = _decimal(opening.get(field), f"Saved opening {identity} {field}")
            if field != "sill_mm" and number <= 0:
                raise ValueError(f"Saved opening {identity} must have positive dimensions")
        if opening.get("offset_mm") is not None:
            _decimal(opening["offset_mm"], f"Saved opening {identity} offset_mm")
        result[identity] = {**opening, "offset_mm": opening.get("offset_mm")}
    return result


def _opening_start(settings: dict, opening: dict) -> float | None:
    if opening["offset_mm"] is not None:
        return opening["offset_mm"]
    span = settings.get("length_mm" if opening["host"] in {"W1", "W3"} else "width_mm")
    if span is None:
        return None
    return float((_decimal(span, "Saved wall span") - _decimal(opening["width_mm"], "Saved opening width")) / 2)


def _opening_position(settings: dict, opening: dict) -> str:
    start = _opening_start(settings, opening)
    if opening["offset_mm"] is None:
        return "centred" + (f" ({_text(start)} mm start)" if start is not None else "")
    return f"{_text(start)} mm start"


def _opening_description(settings: dict, opening: dict) -> str:
    return (f"{opening['kind'].capitalize()} {opening['id']} on {opening['host']} ({WALL_LABELS[opening['host']]}), "
            f"{_text(opening['width_mm'])} x {_text(opening['height_mm'])} mm; sill {_text(opening['sill_mm'])} mm; "
            f"{_opening_position(settings, opening)} from the inner wall reference")


def _opening_changes(before: dict, after: dict) -> list[dict]:
    old_rows, new_rows = _openings(before), _openings(after)
    changes = []
    for identity in sorted(old_rows.keys() | new_rows.keys(), key=lambda value: (value[0], int(value[1:]))):
        old, new = old_rows.get(identity), new_rows.get(identity)
        if old is None or new is None:
            added = old is None
            opening = new if added else old
            settings = after if added else before
            changes.append({"field": f"openings.{identity}", "label": f"{opening['kind'].capitalize()} {identity}",
                            "before": old, "after": new, "description": f"{'Added' if added else 'Removed'} {_opening_description(settings, opening)}.",
                            "category": "openings", "element_id": identity, "status": "added" if added else "removed"})
            continue
        label = f"{new['kind'].capitalize()} {identity}"
        for field, field_label in (("kind", "type"), ("host", "host wall"), ("width_mm", "width"),
                                   ("height_mm", "height"), ("sill_mm", "sill height")):
            if old[field] == new[field]:
                continue
            old_text, new_text = _text(old[field]), _text(new[field])
            if field == "host":
                old_text, new_text = f"{old[field]} ({WALL_LABELS[old[field]]})", f"{new[field]} ({WALL_LABELS[new[field]]})"
            suffix = " mm" if field.endswith("_mm") else ""
            changes.append({"field": f"openings.{identity}.{field}", "label": f"{label} {field_label}",
                            "before": old[field], "after": new[field],
                            "description": f"{label} {field_label}: {old_text}{suffix} to {new_text}{suffix}.",
                            "category": "openings", "element_id": identity, "status": "modified"})
        old_start, new_start = _opening_start(before, old), _opening_start(after, new)
        if old["offset_mm"] != new["offset_mm"] or old_start != new_start:
            changes.append({"field": f"openings.{identity}.offset_mm", "label": f"{label} position",
                            "before": old["offset_mm"], "after": new["offset_mm"],
                            "before_start_mm": old_start, "after_start_mm": new_start,
                            "description": f"{label} position: {_opening_position(before, old)} to {_opening_position(after, new)}; offsets follow each host's inner reference direction.",
                            "category": "openings", "element_id": identity, "status": "modified"})
    return changes


def _snapshot(result: dict, name: str) -> tuple[dict[str, dict], str, Decimal]:
    if not isinstance(result, dict) or not isinstance(result.get("settings"), dict):
        raise ValueError(f"{name} is missing its saved settings")
    if not isinstance(result.get("revision"), str) or not result["revision"]:
        raise ValueError(f"{name} is missing its revision identity")
    totals = result.get("totals", {})
    currency = totals.get("currency")
    if not isinstance(currency, str) or not currency or currency != result["settings"].get("currency"):
        raise ValueError(f"{name} currency is missing or inconsistent")
    if not isinstance(result.get("boq"), list):
        raise ValueError(f"{name} is missing its saved BOQ")
    rows: dict[str, dict] = {}
    for item in result["boq"]:
        if not isinstance(item, dict) or not isinstance(item.get("code"), str) or not item["code"].strip():
            raise ValueError(f"{name} has an invalid BOQ item code")
        code = item["code"]
        if code in rows:
            raise ValueError(f"{name} has duplicate BOQ code {code}")
        if not isinstance(item.get("unit"), str) or not item["unit"].strip():
            raise ValueError(f"{name} item {code} is missing its unit")
        quantity = _decimal(item.get("quantity"), f"{name} {code} quantity")
        rate = _decimal(item.get("rate"), f"{name} {code} rate")
        amount = _decimal(item.get("amount"), f"{name} {code} amount", money=True)
        if _rounded(quantity * rate) != amount:
            raise ValueError(f"{name} item {code} amount does not equal rounded quantity times rate")
        if not isinstance(item.get("description"), str):
            raise ValueError(f"{name} item {code} is missing its description")
        element_ids = item.get("element_ids", [])
        if not isinstance(element_ids, list) or any(not isinstance(value, str) for value in element_ids):
            raise ValueError(f"{name} item {code} has invalid element references")
        rows[code] = {**item, "quantity": quantity, "rate": rate, "amount": amount, "element_ids": sorted(set(element_ids))}
    subtotal = _decimal(totals.get("subtotal"), f"{name} subtotal", money=True)
    if sum((item["amount"] for item in rows.values()), ZERO) != subtotal:
        raise ValueError(f"{name} BOQ amounts do not reconcile to the saved subtotal")
    return rows, currency, subtotal


def compare_revisions(before: dict, after: dict) -> dict[str, Any]:
    """Return the factual description and estimated cost difference from A to B.

    Positive cost deltas are increases; negative deltas are credits. Currency
    changes are rejected because an exchange rate cannot be inferred.
    """
    old_rows, currency, old_total = _snapshot(before, "Revision A")
    new_rows, new_currency, new_total = _snapshot(after, "Revision B")
    if currency != new_currency:
        raise ValueError("Choose revisions with the same currency. No currency conversion is applied.")

    old_settings, new_settings = (_flat({key: value for key, value in result["settings"].items() if key != "openings"})
                                  for result in (before, after))
    ordered_fields = list(FIELD_LABELS) + sorted((old_settings.keys() | new_settings.keys()) - FIELD_LABELS.keys())
    setting_changes = []
    for field in ordered_fields:
        old, new = old_settings.get(field), new_settings.get(field)
        if old == new:
            continue
        label, category = FIELD_LABELS.get(field, (field.replace("_", " ").capitalize(), "settings"))
        suffix = " mm" if field.endswith("_mm") else ""
        setting_changes.append({"field": field, "label": label, "before": old, "after": new,
                                "description": f"{label}: {_text(old)}{suffix} to {_text(new)}{suffix}.", "category": category})
    setting_changes.extend(_opening_changes(before["settings"], after["settings"]))

    items = []
    for code in dict.fromkeys([*old_rows, *new_rows]):
        old, new = old_rows.get(code), new_rows.get(code)
        if old == new:
            continue
        old_quantity, new_quantity = (row["quantity"] if row else ZERO for row in (old, new))
        old_rate, new_rate = (row["rate"] if row else ZERO for row in (old, new))
        old_amount, new_amount = (row["amount"] if row else ZERO for row in (old, new))
        old_description, new_description = (row["description"] if row else "" for row in (old, new))
        old_unit, new_unit = (row["unit"] if row else "" for row in (old, new))
        delta = new_amount - old_amount
        quantity_delta: Decimal | None = new_quantity - old_quantity
        comparable = not (old and new and old_unit != new_unit)
        if old is None:
            status = "added"
            description = f"Added {new_description}: {_text(new_quantity)} {new_unit}."
            quantity_effect, rate_effect, rounding_effect = delta, ZERO, ZERO
        elif new is None:
            status = "removed"
            description = f"Removed {old_description}: {_text(old_quantity)} {old_unit}."
            quantity_effect, rate_effect, rounding_effect = delta, ZERO, ZERO
        elif not comparable:
            status = "unit_changed"
            description = f"Measurement unit changed from {old_unit} to {new_unit}; quantity and rate effects are not directly comparable."
            quantity_delta = quantity_effect = rate_effect = rounding_effect = None
        else:
            parts = []
            if old_quantity != new_quantity:
                parts.append(f"Quantity {_text(old_quantity)} to {_text(new_quantity)} {new_unit}")
            if old_rate != new_rate:
                parts.append(f"unit rate {_text(old_rate)} to {_text(new_rate)} {currency}/{new_unit}")
            if old_description != new_description:
                parts.append(f"description changed from '{old_description}' to '{new_description}'")
            if old["element_ids"] != new["element_ids"]:
                parts.append("linked model elements changed")
            if not parts:
                # Extra snapshot fields do not imply a design or price change.
                continue
            description = "; ".join(parts) + "."
            status = "description_changed" if old_quantity == new_quantity and old_rate == new_rate else "modified"
            quantity_effect = _rounded((new_quantity - old_quantity) * old_rate)
            rate_effect = _rounded(new_quantity * (new_rate - old_rate))
            rounding_effect = delta - quantity_effect - rate_effect
        items.append({
            "code": code, "description": description,
            "before_description": old_description, "after_description": new_description,
            "unit": new_unit or old_unit if comparable else f"{old_unit} to {new_unit}",
            "before_unit": old_unit, "after_unit": new_unit,
            "before_quantity": _float(old_quantity), "after_quantity": _float(new_quantity),
            "quantity_delta": _float(quantity_delta) if quantity_delta is not None else None,
            "before_rate": _float(old_rate), "after_rate": _float(new_rate),
            "before_amount": _float(old_amount), "after_amount": _float(new_amount), "cost_delta": _float(delta),
            "quantity_effect": _float(quantity_effect) if quantity_effect is not None else None,
            "rate_effect": _float(rate_effect) if rate_effect is not None else None,
            "rounding_effect": _float(rounding_effect) if rounding_effect is not None else None,
            "status": status, "element_ids": sorted(set((old or {}).get("element_ids", []) + (new or {}).get("element_ids", []))),
        })

    net_change = new_total - old_total
    if sum((Decimal(str(item["cost_delta"])) for item in items), ZERO) != net_change:
        raise ValueError("Changed BOQ items do not reconcile to the project cost difference")
    comparable = all(item["quantity_effect"] is not None for item in items)
    totals = {
        "before": _float(old_total), "after": _float(new_total), "net_change": _float(net_change),
        "additions": _float(sum((Decimal(str(item["cost_delta"])) for item in items if item["cost_delta"] > 0), ZERO)),
        "omissions": _float(-sum((Decimal(str(item["cost_delta"])) for item in items if item["cost_delta"] < 0), ZERO)),
    }
    for key in ("quantity_effect", "rate_effect", "rounding_effect"):
        totals[key] = _float(sum((Decimal(str(item[key])) for item in items), ZERO)) if comparable else None
    notes = [
        "This is the estimated difference between saved, modelled BOQ snapshots. Unit rates are illustrative unless replaced with project rates.",
        "Cost increases and decreases include quantity, rate and scope changes. A colour or description change can have zero cost at the saved rates.",
        "For matching units: quantity effect = (quantity B - quantity A) x rate A; rate effect = quantity B x (rate B - rate A). A rounding adjustment reconciles to the saved line amounts.",
        "Added and removed items are assigned to the quantity/scope effect. A changed measurement unit has no quantity/rate decomposition.",
        "Only affected BOQ items are listed; project totals include the complete saved BOQ. Omissions are positive absolute cost decreases or credits.",
        "Demolition, abortive work, rework, waste, fees, preliminaries, taxes and programme/time effects are excluded unless separately modelled and priced. This report is not an approved contract variation.",
    ]
    return {
        "before_revision": before["revision"], "after_revision": after["revision"],
        "before_label": before["settings"].get("project_name", "Revision A"),
        "after_label": after["settings"].get("project_name", "Revision B"),
        "before_created_at": before.get("created_at"), "after_created_at": after.get("created_at"),
        "currency": currency, "setting_changes": setting_changes, "items": items, "totals": totals,
        "summary": [item["description"] for item in setting_changes] or [item["description"] for item in items],
        "has_changes": bool(setting_changes or items), "notes": notes,
    }


def _safe_cell(value: Any) -> Any:
    return "'" + value if isinstance(value, str) and value.startswith(("=", "+", "-", "@")) else value


def _signed(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+,.2f}" if value else "0.00"


def _xlsx(comparison: dict, directory: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Change report"
    sheet.sheet_view.showGridLines = False
    sheet.merge_cells("A1:M1")
    sheet["A1"] = "House change report"
    sheet["A1"].font = Font(name="Calibri", size=20, bold=True, color=INK)
    sheet.row_dimensions[1].height = 32
    for row, text in [(2, f"A {comparison['before_revision']} - {comparison['before_label']}"),
                      (3, f"B {comparison['after_revision']} - {comparison['after_label']}"),
                      (4, f"Currency {comparison['currency']}. Saved estimate snapshots; only affected BOQ items shown below.")]:
        sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=13)
        sheet.cell(row, 1, _safe_cell(text))
        sheet.row_dimensions[row].height = 22
    labels = ["Estimate A", "Estimate B", "Cost increases", "Cost decreases / credits", "Net estimated change"]
    values = [comparison["totals"][key] for key in ("before", "after", "additions", "omissions", "net_change")]
    spans = [(1, 3), (4, 5), (6, 7), (8, 10), (11, 13)]
    for label, value, (first, last) in zip(labels, values, spans, strict=True):
        for row in (6, 7):
            sheet.merge_cells(start_row=row, start_column=first, end_row=row, end_column=last)
        sheet.cell(6, first, label).font = Font(name="Calibri", size=10, color=INK)
        cell = sheet.cell(7, first, value)
        cell.font = Font(name="Calibri", size=16, bold=True, color=TEAL)
        cell.alignment = Alignment(horizontal="left", vertical="center")
        cell.number_format = '#,##0.00;[Red](#,##0.00);"0.00"'
    sheet.row_dimensions[7].height = 30
    row = 10
    sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=13)
    sheet.cell(row, 1, "Description of changes").font = Font(name="Calibri", bold=True, size=13, color=INK)
    row += 1
    for text in comparison["summary"] or ["No design, description or cost changes between these saved revisions."]:
        sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=13)
        sheet.cell(row, 1, _safe_cell(text)).alignment = Alignment(wrap_text=True, vertical="center")
        sheet.row_dimensions[row].height = 28
        row += 1
    row += 2
    header_row = row
    headers = ["Item", "Description of change", "Unit", "Quantity A", "Quantity B", "Rate A", "Rate B", "Amount A", "Amount B",
               "Cost change", "Quantity/scope", "Rate effect", "Rounding adjustment"]
    for column, text in enumerate(headers, 1):
        cell = sheet.cell(row, column, text)
        cell.fill = PatternFill("solid", fgColor=INK)
        cell.font = Font(name="Calibri", bold=True, color="FFFFFF")
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    sheet.row_dimensions[row].height = 34
    first_data_row = row + 1
    for item in comparison["items"]:
        row += 1
        values = [item[key] for key in ("code", "description", "unit", "before_quantity", "after_quantity", "before_rate", "after_rate", "before_amount", "after_amount")]
        values.append(f"=ROUND(I{row}-H{row},2)")
        if item["status"] == "unit_changed":
            values.extend(["n/a", "n/a", "n/a"])
        elif item["status"] in {"added", "removed"}:
            values.extend([f"=J{row}", 0, 0])
        else:
            values.extend([f"=ROUND((E{row}-D{row})*F{row},2)", f"=ROUND(E{row}*(G{row}-F{row}),2)", f"=ROUND(J{row}-K{row}-L{row},2)"])
        for column, value in enumerate(values, 1):
            cell = sheet.cell(row, column, value if column >= 10 else _safe_cell(value))
            cell.font = Font(name="Calibri", size=10, color="000000")
            cell.alignment = Alignment(wrap_text=True, vertical="center", horizontal="left" if column < 4 else "right")
            if row % 2:
                cell.fill = PatternFill("solid", fgColor=PALE)
            if column in (4, 5):
                cell.number_format = "#,##0.000"
            elif column >= 6:
                cell.number_format = '#,##0.00;[Red](#,##0.00);"0.00"'
        sheet.row_dimensions[row].height = min(120, max(48, 14 * (len(item["description"]) // 65 + 1)))
    last_data_row = row
    row += 2
    sheet.cell(row, 2, "Net estimated change (affected items)")
    sheet.cell(row, 10, f"=SUM(J{first_data_row}:J{last_data_row})" if comparison["items"] else 0)
    for key, column in (("quantity_effect", 11), ("rate_effect", 12), ("rounding_effect", 13)):
        sheet.cell(row, column, f"=SUM({chr(64 + column)}{first_data_row}:{chr(64 + column)}{last_data_row})"
                   if comparison["items"] and comparison["totals"][key] is not None else "n/a" if comparison["totals"][key] is None else 0)
    for cell in sheet[row]:
        cell.fill = PatternFill("solid", fgColor="D8EAE7")
        cell.font = Font(name="Calibri", bold=True, color=INK)
        cell.number_format = '#,##0.00;[Red](#,##0.00);"0.00"'
    row += 3
    for note in comparison["notes"]:
        sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=13)
        sheet.cell(row, 1, note).alignment = Alignment(wrap_text=True, vertical="center")
        sheet.cell(row, 1).font = Font(name="Calibri", size=10, color="53636B")
        sheet.row_dimensions[row].height = 32 if len(note) < 220 else 44
        row += 1
    for column, width in {"A": 12, "B": 65, "C": 15, "D": 13, "E": 13, "F": 14, "G": 14, "H": 17, "I": 17, "J": 17, "K": 18, "L": 17, "M": 18}.items():
        sheet.column_dimensions[column].width = width
    sheet.freeze_panes = f"D{first_data_row}"
    sheet.auto_filter.ref = f"A{header_row}:M{max(first_data_row, last_data_row)}"
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A3
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.print_area = f"A1:M{row - 1}"
    sheet.oddFooter.center.text = f"A {comparison['before_revision']} to B {comparison['after_revision']}"
    workbook.calculation = CalcProperties(fullCalcOnLoad=True)
    workbook.save(directory / "changes.xlsx")


def _csv(comparison: dict, directory: Path) -> None:
    with (directory / "changes.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["From revision A", comparison["before_revision"], "To revision B", comparison["after_revision"], "Currency", comparison["currency"]])
        writer.writerow(["Estimate A", comparison["totals"]["before"], "Estimate B", comparison["totals"]["after"], "Net estimated change", comparison["totals"]["net_change"]])
        writer.writerow(["Description of changes"])
        for description in comparison["summary"] or ["No design, description or cost changes."]:
            writer.writerow([_safe_cell(description)])
        keys = ["code", "description", "before_unit", "after_unit", "before_quantity", "after_quantity", "before_rate", "after_rate", "before_amount", "after_amount", "cost_delta", "quantity_effect", "rate_effect", "rounding_effect", "status", "element_ids"]
        writer.writerow(keys)
        for item in comparison["items"]:
            writer.writerow([_safe_cell(", ".join(item[key]) if key == "element_ids" else item[key]) for key in keys])
        for note in comparison["notes"]:
            writer.writerow(["Note", note])


def _pdf(comparison: dict, directory: Path) -> None:
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="ChangeTitle", fontName="Helvetica-Bold", fontSize=20, leading=25, textColor=colors.HexColor("#" + INK), spaceAfter=10))
    styles.add(ParagraphStyle(name="ChangeBody", fontName="Helvetica", fontSize=9, leading=12, spaceAfter=5))
    styles.add(ParagraphStyle(name="ChangeSmall", fontName="Helvetica", fontSize=7.5, leading=10, spaceAfter=4))
    styles.add(ParagraphStyle(name="ChangeHeader", fontName="Helvetica-Bold", fontSize=7.5, leading=10, textColor=colors.white))
    paragraph = lambda value, style="ChangeBody": Paragraph(html.escape(str(value)), styles[style])
    story = [paragraph("House change report", "ChangeTitle"),
             paragraph(f"A {comparison['before_revision']} - {comparison['before_label']}"),
             paragraph(f"B {comparison['after_revision']} - {comparison['after_label']}"),
             paragraph(f"Saved estimate comparison in {comparison['currency']}."), Spacer(1, 8)]
    total_keys = ["before", "after", "additions", "omissions", "net_change"]
    total_labels = ["Estimate A", "Estimate B", "Cost increases", "Cost decreases / credits", "Net estimated change"]
    summary = Table([[paragraph(label, "ChangeSmall") for label in total_labels],
                     [paragraph(f"{comparison['totals'][key]:,.2f}") for key in total_keys]], colWidths=[152] * 5)
    summary.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#" + PALE)), ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#C9D9DE")),
                                ("LEFTPADDING", (0, 0), (-1, -1), 10), ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    story.extend([summary, Spacer(1, 12), paragraph("Description of changes", "Heading3")])
    for description in comparison["summary"] or ["No design, description or cost changes between these saved revisions."]:
        story.append(paragraph(description))
    story.extend([Spacer(1, 8), paragraph("Affected BOQ items", "Heading3")])
    headers = ["Item", "Description of change", "Qty A", "Qty B", "Unit", "Rate A", "Rate B", "Amount A", "Amount B", "Cost change"]
    table_rows = [[paragraph(header, "ChangeHeader") for header in headers]]
    for item in comparison["items"]:
        values = [item["code"], item["description"], _text(item["before_quantity"]), _text(item["after_quantity"]), item["unit"],
                  _text(item["before_rate"]), _text(item["after_rate"]), f"{item['before_amount']:,.2f}", f"{item['after_amount']:,.2f}", _signed(item["cost_delta"])]
        table_rows.append([paragraph(value, "ChangeSmall") for value in values])
    if not comparison["items"]:
        table_rows.append([paragraph("No affected BOQ items", "ChangeSmall")] + [""] * 9)
    table = Table(table_rows, colWidths=[45, 206, 43, 43, 42, 50, 50, 61, 61, 65], repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#" + INK)), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                              ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#" + PALE)]),
                              ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                              ("LINEBELOW", (0, 0), (-1, -1), 0.35, colors.HexColor("#D5E0E4"))]))
    story.extend([table, Spacer(1, 10)])
    effects = comparison["totals"]
    story.append(paragraph(f"Quantity/scope effect: {_signed(effects['quantity_effect'])}; rate effect: {_signed(effects['rate_effect'])}; rounding adjustment: {_signed(effects['rounding_effect'])} {comparison['currency']}.", "ChangeSmall"))
    for note in comparison["notes"]:
        story.append(paragraph(note, "ChangeSmall"))

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#53636B"))
        canvas.drawString(30, 18, f"A {comparison['before_revision']} to B {comparison['after_revision']}")
        canvas.drawRightString(810, 18, f"Page {document.page}")
        canvas.restoreState()

    document = SimpleDocTemplate(str(directory / "changes.pdf"), pagesize=landscape(A4), rightMargin=30, leftMargin=30, topMargin=28, bottomMargin=34,
                                title="House change report", author="House Sync")
    document.build(story, onFirstPage=footer, onLaterPages=footer)



def _variation_pdf(comparison: dict, directory: Path) -> None:
    """Prepare an unsigned variation order with the saved A-to-B estimate evidence."""
    styles = getSampleStyleSheet()
    for name, size, leading, bold, colour in [
        ("VOTitle", 22, 27, True, INK),
        ("VOHeading", 11, 15, True, TEAL),
        ("VOBody", 9, 12, False, INK),
        ("VOSmall", 7.5, 10, False, INK),
        ("VOHeader", 7.5, 10, True, "FFFFFF"),
    ]:
        styles.add(ParagraphStyle(name=name, fontName="Helvetica-Bold" if bold else "Helvetica",
                                  fontSize=size, leading=leading, textColor=colors.HexColor("#" + colour), spaceAfter=4))
    paragraph = lambda value, style="VOBody": Paragraph(html.escape(str(value)), styles[style])
    reference = f"VO-{comparison['before_revision']}-{comparison['after_revision']}"
    width = landscape(A4)[0] - 72
    totals = comparison["totals"]
    currency = comparison["currency"]
    story = [paragraph("DRAFT VARIATION ORDER", "VOTitle"),
             paragraph("Pending contractual review and approval. All amounts are saved model estimates.", "VOSmall"), Spacer(1, 8)]
    metadata = [
        [f"Project: {comparison['after_label']}", f"Variation reference: {reference}"],
        ["Contract reference: ______________________________", "Instruction reference: ______________________________"],
        [f"Baseline A: {comparison['before_revision']} - {comparison['before_label']}",
         f"Proposed B: {comparison['after_revision']} - {comparison['after_label']}"],
        [f"Snapshot A: {comparison.get('before_created_at') or 'Not recorded'}",
         f"Snapshot B: {comparison.get('after_created_at') or 'Not recorded'}"],
    ]
    metadata_table = Table([[paragraph(value, "VOSmall") for value in row] for row in metadata], colWidths=[width / 2] * 2)
    metadata_table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#" + PALE)),
                                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                        ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                                        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story.extend([metadata_table, Spacer(1, 10), paragraph(f"Estimated valuation - {currency}", "VOHeading")])
    labels = ["Baseline estimate A", "Additions", "Omissions / credits (deduct)", "Net estimated variation", "Revised estimate B"]
    keys = ["before", "additions", "omissions", "net_change", "after"]
    values = [f"{totals[key]:,.2f}" if key != "net_change" else _signed(totals[key]) for key in keys]
    valuation = Table([[paragraph(value, "VOSmall") for value in labels],
                       [paragraph(value) for value in values]], colWidths=[width / 5] * 5)
    valuation.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#E5F0ED")),
                                   ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                   ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                                   ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story.extend([valuation, Spacer(1, 11), paragraph("Description of variation", "VOHeading")])
    for description in comparison["summary"] or ["No design, description or cost changes between the selected saved revisions."]:
        story.append(paragraph(description))
    story.extend([paragraph("Reason / instruction: __________________________________________________________________________________", "VOSmall"),
                  paragraph("Programme impact: Not assessed. Agreed extension of time (if any): __________________________________________", "VOSmall"),
                  Spacer(1, 10), paragraph("Review and authorisation", "VOHeading")])
    approval_rows = [
        ["Prepared by", "Contractor / valuation review", "Employer / authorised approval"],
        ["Name: __________________________", "Name: __________________________", "Name: __________________________"],
        ["Signature: _______________________", "Signature: _______________________", "Signature: _______________________"],
        ["Date: ___________________________", "Date: ___________________________", "Date: ___________________________"],
    ]
    approval = Table([[paragraph(value, "VOSmall") for value in row] for row in approval_rows], colWidths=[width / 3] * 3)
    approval.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#" + PALE)),
                                  ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#C9D9DE")),
                                  ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#D5E0E4")),
                                  ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                  ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                                  ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story.append(KeepTogether([approval, Spacer(1, 8), paragraph(
        "Exporting this draft does not issue an instruction, certify payment or record approval. Complete the contract particulars, valuation and authorisation before issue.", "VOSmall")]))
    story.extend([PageBreak(), paragraph("VARIATION COST BREAKDOWN", "VOTitle"),
                  paragraph(f"{reference} | {currency} | Saved revision A to saved revision B", "VOSmall"),
                  paragraph("Affected BOQ items", "VOHeading")])
    headers = ["Item", "Description of change", "Qty A", "Qty B", "Unit", "Rate A", "Rate B", "Amount A", "Amount B", "Cost change"]
    rows = [[paragraph(value, "VOHeader") for value in headers]]
    for item in comparison["items"]:
        values = [item["code"], item["description"], _text(item["before_quantity"]), _text(item["after_quantity"]), item["unit"],
                  _text(item["before_rate"]), _text(item["after_rate"]), f"{item['before_amount']:,.2f}",
                  f"{item['after_amount']:,.2f}", _signed(item["cost_delta"])]
        rows.append([paragraph(value, "VOSmall") for value in values])
    if not comparison["items"]:
        rows.append([paragraph("No affected BOQ items", "VOSmall")] + [""] * 9)
    widths = [45, width - 460, 43, 43, 42, 50, 50, 61, 61, 65]
    table = Table(rows, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#" + INK)),
                              ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#" + PALE)]),
                              ("VALIGN", (0, 0), (-1, -1), "TOP"),
                              ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                              ("LINEBELOW", (0, 0), (-1, -1), 0.35, colors.HexColor("#D5E0E4"))]))
    story.extend([table, Spacer(1, 10), paragraph(
        f"Net of affected items: {_signed(totals['net_change'])} {currency}. Baseline estimate {totals['before']:,.2f} + additions {totals['additions']:,.2f} - omissions {totals['omissions']:,.2f} = revised estimate {totals['after']:,.2f} {currency}.", "VOBody"),
        paragraph("Valuation basis and qualifications", "VOHeading")])
    for note in comparison["notes"]:
        story.append(paragraph(note, "VOSmall"))

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#53636B"))
        canvas.drawString(30, 18, f"{reference} | DRAFT - Pending approval")
        canvas.drawRightString(landscape(A4)[0] - 30, 18, f"Page {document.page}")
        canvas.restoreState()

    document = SimpleDocTemplate(str(directory / "variation-order.pdf"), pagesize=landscape(A4),
                                rightMargin=30, leftMargin=30, topMargin=28, bottomMargin=34,
                                title="Draft variation order", author="House Sync", pageCompression=0)
    document.build(story, onFirstPage=footer, onLaterPages=footer)


def export_change_report(comparison: dict, output_dir: Path) -> dict[str, str]:
    """Write the A-to-B evidence and an unsigned draft variation order."""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    _xlsx(comparison, directory)
    _csv(comparison, directory)
    _pdf(comparison, directory)
    _variation_pdf(comparison, directory)
    (directory / "changes.json").write_text(json.dumps(comparison, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return {"changes_xlsx": "changes.xlsx", "changes_csv": "changes.csv", "changes_pdf": "changes.pdf", "changes_json": "changes.json", "variation_pdf": "variation-order.pdf"}
