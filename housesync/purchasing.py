"""Fillable draft purchase orders from immutable, saved BOQ row facts.

This exporter deliberately has no pricing-library or model-engine dependency.
It copies the archived quantities, rates and amounts without calculating a new
price, supplier selection, tax treatment or material purchasing conversion.
"""
from __future__ import annotations

import html
import io
import re
from decimal import Decimal, InvalidOperation
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase.acroform import AcroForm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Flowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

INK = colors.HexColor("#173042")
TEAL = colors.HexColor("#1B6D65")
PALE = colors.HexColor("#EAF3F7")
GREY = colors.HexColor("#586973")
LINE = colors.HexColor("#C9D9DE")
CODE = re.compile(r"[A-Z][A-Z0-9_]{0,79}")
REVISION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}")


class UnknownPurchaseOrder(KeyError):
    """A valid BOQ code is absent from the requested saved revision."""


def _code(value: Any) -> str:
    if not isinstance(value, str) or CODE.fullmatch(value) is None:
        raise ValueError("Purchase order codes must use uppercase letters, numbers and underscores, starting with a letter (maximum 80 characters)")
    return value


def _number(value: Any, name: str, *, money: bool = False) -> str:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"Saved {name} must be a finite non-negative number")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"Saved {name} must be a finite non-negative number") from exc
    if not number.is_finite() or number < 0:
        raise ValueError(f"Saved {name} must be a finite non-negative number")
    # Formatting adds no quantisation: a saved 11.125 rate remains 11.125.
    decimals = max(2 if money else 0, -number.as_tuple().exponent)
    text = format(number, f",.{decimals}f")
    return text if money or "." not in text else text.rstrip("0").rstrip(".")


def _saved_rows(result: dict) -> tuple[str, dict[str, Any], str, dict[str, dict]]:
    if not isinstance(result, dict):
        raise ValueError("A saved house result is required")
    revision = result.get("revision")
    if not isinstance(revision, str) or REVISION.fullmatch(revision) is None:
        raise ValueError("Saved revision identity is missing or unsafe for purchase order filenames")
    settings = result.get("settings", {})
    if not isinstance(settings, dict):
        raise ValueError("Saved settings must be an object")
    totals = result.get("totals", {})
    if not isinstance(totals, dict):
        raise ValueError("Saved totals must be an object")
    currency = totals.get("currency", settings.get("currency"))
    if currency is not None and (not isinstance(currency, str) or re.fullmatch(r"[A-Z]{3}", currency) is None):
        raise ValueError("Saved currency must be an uppercase three-letter code")
    rows = result.get("boq")
    if not isinstance(rows, list):
        raise ValueError("Saved BOQ rows are required")
    indexed = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Saved BOQ rows must be objects")
        code = _code(row.get("code"))
        if code in indexed:
            raise ValueError(f"Saved BOQ has duplicate code {code}")
        indexed[code] = row
    return revision, settings, currency or "Not recorded", indexed


class _OrderFields(Flowable):
    """A single-page block of editable buyer-supplied commercial particulars."""

    def __init__(self, width: float):
        super().__init__()
        self.width, self.height = width, 380.0

    def draw(self) -> None:
        pdf: Canvas = self.canv
        form: AcroForm = pdf.acroForm
        pdf.setStrokeColor(LINE)
        pdf.line(0, self.height, self.width, self.height)
        pdf.setFillColor(TEAL)
        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(0, self.height - 17, "Order particulars - complete before issue")
        y = self.height - 36

        def row(fields: list[tuple[str, str, bool]], height: float) -> None:
            nonlocal y
            gutter = 14
            width = (self.width - gutter * (len(fields) - 1)) / len(fields)
            for index, (name, label, multiline) in enumerate(fields):
                x = index * (width + gutter)
                pdf.setFillColor(INK)
                pdf.setFont("Helvetica", 8)
                pdf.drawString(x, y, label)
                form.textfield(name=name, tooltip=label, x=x, y=y - height - 5, width=width, height=height,
                               value="", relative=True, fontName="Helvetica", fontSize=9, textColor=colors.black,
                               borderColor=LINE, fillColor=colors.white, borderWidth=.6, borderStyle="solid",
                               fieldFlags="multiline" if multiline else "", maxlen=3000 if multiline else 300,
                               forceBorder=True)
            y -= height + 20

        row([("buyer", "Buyer / company / contact", True), ("supplier", "Supplier / company / contact", True)], 30)
        row([("delivery_site", "Delivery site / address", True)], 34)
        row([("order_date", "Order date", False), ("required_by", "Required-by date", False)], 20)
        row([("quote_reference", "Supplier quote / reference", False)], 20)
        row([("notes", "Product / SKU, terms or delivery instructions", True)], 42)
        row([("prepared_by", "Prepared by", False), ("approved_by", "Approved by", False)], 20)
        pdf.setFillColor(GREY)
        pdf.setFont("Helvetica", 7.5)
        pdf.drawString(0, 22, "Complete the product / SKU, delivery details and agreed terms; confirm the saved BOQ basis before issue.")
        pdf.drawString(0, 11, "Typed names are not digital signatures. This draft records no approval and does not send an order.")


def purchase_order_pdf(result: dict, code: str) -> bytes:
    """Return one prefilled, fillable draft PDF for a saved BOQ item.

    A missing valid code raises UnknownPurchaseOrder. Invalid snapshot data or
    unsafe code/revision filenames raise ValueError. Optional historical rate
    and measurement metadata remain unknown rather than receiving new defaults.
    """
    code = _code(code)
    revision, settings, currency, rows = _saved_rows(result)
    if code not in rows:
        raise UnknownPurchaseOrder(code)
    row = rows[code]
    unit = row.get("unit")
    description = row.get("description")
    if not isinstance(unit, str) or not unit.strip() or not isinstance(description, str):
        raise ValueError(f"Saved BOQ {code} needs a description and measurement unit")
    gross = _number(row.get("gross_quantity", row.get("quantity")), "gross quantity")
    rate = _number(row.get("rate"), "unit rate", money=True)
    amount = _number(row.get("amount"), "line amount", money=True)
    reference = f"PO-{revision}-{code}"
    source = row.get("rate_source", settings.get("rate_source"))
    effective = row.get("rate_effective_date", settings.get("rate_date"))
    origin = row.get("rate_origin")
    library = result.get("rate_library") or {}
    if not isinstance(library, dict):
        raise ValueError("Saved rate library metadata must be an object")

    styles = {
        "title": ParagraphStyle("POTitle", fontName="Helvetica-Bold", fontSize=21, leading=25, textColor=INK, spaceAfter=5),
        "heading": ParagraphStyle("POHeading", fontName="Helvetica-Bold", fontSize=10.5, leading=14, textColor=TEAL, spaceBefore=6, spaceAfter=4),
        "body": ParagraphStyle("POBody", fontName="Helvetica", fontSize=9, leading=12, textColor=INK, spaceAfter=4),
        "small": ParagraphStyle("POSmall", fontName="Helvetica", fontSize=7.5, leading=10, textColor=GREY, spaceAfter=4),
        "header": ParagraphStyle("POHeader", fontName="Helvetica-Bold", fontSize=8, leading=10, textColor=colors.white),
    }

    def paragraph(value: Any, style: str = "body") -> Paragraph:
        return Paragraph(html.escape(str(value)), styles[style])

    width = A4[0] - 72
    story: list[Any] = [paragraph("DRAFT PURCHASE ORDER", "title"),
                        paragraph("Saved BOQ reference - pending commercial completion and approval", "small"),
                        paragraph(f"Project: {settings.get('project_name') or 'Not recorded'}"),
                        paragraph(f"Reference: {reference} | Saved design revision: {revision}", "small"),
                        paragraph(f"{code} - scope and saved price", "heading"), paragraph(description)]
    headings = ["Saved gross quantity", "Unit", f"Saved unit rate ({currency})", f"Saved line amount ({currency})"]
    table = Table([[paragraph(value, "header") for value in headings],
                   [paragraph(gross), paragraph(unit), paragraph(rate), paragraph(amount)]],
                  colWidths=[width * .24, width * .12, width * .31, width * .33], repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), INK), ("BACKGROUND", (0, 1), (-1, -1), PALE),
                              ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 6),
                              ("BOTTOMPADDING", (0, 0), (-1, -1), 6), ("LEFTPADDING", (0, 0), (-1, -1), 8),
                              ("RIGHTPADDING", (0, 0), (-1, -1), 8)]))
    story.extend([table, Spacer(1, 6)])
    measured = []
    for key, label in (("measured_quantity", "Measured net geometry"), ("net_quantity", "Net priced quantity"),
                       ("wastage_percent", "Wastage"), ("paint_coats", "Paint coats")):
        if row.get(key) is not None:
            suffix = "%" if key == "wastage_percent" else f" {unit}" if key in {"measured_quantity", "net_quantity"} else ""
            measured.append(f"{label}: {_number(row[key], label)}{suffix}")
    if measured:
        story.append(paragraph(" | ".join(measured), "small"))
    if code == "PAINT":
        note = ("Painting retains the saved m2-per-coat measurement basis and recorded coat count. No conversion to litres, cans or product coverage is made."
                if row.get("paint_coats") is not None else
                f"Painting uses the saved {unit} quantity. A coat count was not recorded for this item; no coat multiplier or volume conversion is inferred.")
        story.append(paragraph(note, "small"))
    story.append(paragraph(f"Rate origin: {origin or 'Not recorded'} | Rate effective date: {effective or 'Not recorded'}", "small"))
    story.append(paragraph(f"Applied rate source: {source or 'Not recorded'}", "small"))
    if library.get("sha256"):
        story.append(paragraph(f"Saved rate library SHA-256: {library['sha256']}", "small"))
    if source and str(source).casefold().startswith("illustrative"):
        story.append(paragraph(f"Illustrative {currency} rates, not a tender estimate or a supplier quotation.", "small"))
    story.append(paragraph("Quantities and prices are copied from the saved revision without re-pricing. Tax, freight and commercial terms require agreement.", "small"))
    story.extend([Spacer(1, 7), _OrderFields(width)])

    def footer(pdf: Canvas, document: Any) -> None:
        pdf.saveState()
        pdf.setStrokeColor(LINE)
        pdf.line(36, 28, A4[0] - 36, 28)
        pdf.setFillColor(GREY)
        pdf.setFont("Helvetica", 7)
        pdf.drawString(36, 17, f"{reference} | DRAFT - pending approval")
        pdf.drawRightString(A4[0] - 36, 17, f"Page {document.page}")
        pdf.restoreState()

    output = io.BytesIO()
    document = SimpleDocTemplate(output, pagesize=A4, leftMargin=36, rightMargin=36, topMargin=28, bottomMargin=38,
                                 title=f"Draft purchase order {reference}", author="House Sync", invariant=True, pageCompression=0)
    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()


def purchase_orders_zip(result: dict) -> bytes:
    """Return one deterministic named PDF per saved BOQ row, without file writes."""
    revision, _settings, _currency, rows = _saved_rows(result)
    output = io.BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for code in rows:
            entry = ZipInfo(f"PO-{revision}-{code}.pdf", date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = ZIP_DEFLATED
            entry.create_system = 0
            archive.writestr(entry, purchase_order_pdf(result, code))
    return output.getvalue()
