"""Separate client quotations and estimator-only variation audit documents.

The client renderer takes only a public projection. It never receives model
snapshots or the comparison, and reads named, allowlisted fields rather than
serialising arbitrary dictionaries or adding an internal report attachment.
"""
from __future__ import annotations

import html
import io
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Flowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

INK = colors.HexColor("#173042")
TEAL = colors.HexColor("#1B6D65")
PALE = colors.HexColor("#EAF3F7")
GREY = colors.HexColor("#586973")
LINE = colors.HexColor("#C9D9DE")


def _text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    if not isinstance(value, str):
        raise ValueError("Quote document text fields must be plain strings")
    return value or fallback


def _numeric(value: Any) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError("Quote document amounts must be finite numbers")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("Quote document amounts must be finite numbers") from exc
    if not result.is_finite():
        raise ValueError("Quote document amounts must be finite numbers")
    return result


def _money(value: Any) -> str:
    return f"AUD {_numeric(value):,.2f}"


def _number(value: Any) -> str:
    number = _numeric(value)
    text = format(number, ",f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _styles() -> dict[str, ParagraphStyle]:
    return {
        "title": ParagraphStyle("QuoteTitle", fontName="Helvetica-Bold", fontSize=21, leading=26, textColor=INK, spaceAfter=10),
        "builder": ParagraphStyle("QuoteBuilder", fontName="Helvetica-Bold", fontSize=15, leading=19, textColor=INK),
        "heading": ParagraphStyle("QuoteHeading", fontName="Helvetica-Bold", fontSize=11, leading=15, textColor=TEAL, spaceBefore=10, spaceAfter=6, keepWithNext=True),
        "body": ParagraphStyle("QuoteBody", fontName="Helvetica", fontSize=9, leading=13, textColor=INK, spaceAfter=6),
        "small": ParagraphStyle("QuoteSmall", fontName="Helvetica", fontSize=7.5, leading=10.5, textColor=GREY, spaceAfter=5),
        "header": ParagraphStyle("QuoteHeader", fontName="Helvetica-Bold", fontSize=8, leading=11, textColor=colors.white),
        "value": ParagraphStyle("QuoteValue", fontName="Helvetica-Bold", fontSize=11, leading=15, textColor=INK, spaceAfter=2),
    }


def _paragraph(value: Any, styles: dict, style: str = "body") -> Paragraph:
    # Newline paragraphs remain client-supplied text. HTML-like text is never
    # interpreted as PDF markup, hyperlinks or a resource to be loaded.
    plain = _text(value)
    return Paragraph(html.escape(plain).replace("\n", "<br/>"), styles[style])


def _table(rows: list[list[Any]], widths: list[float], *, header: bool = False) -> Table:
    table = Table(rows, colWidths=widths, repeatRows=1 if header else 0, hAlign="LEFT", splitInRow=1)
    rules = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 8),
             ("RIGHTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 6),
             ("BOTTOMPADDING", (0, 0), (-1, -1), 6), ("LINEBELOW", (0, 0), (-1, -1), .4, LINE)]
    if header:
        rules.append(("BACKGROUND", (0, 0), (-1, 0), INK))
        rules.append(("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE]))
    table.setStyle(TableStyle(rules))
    return table


class _BrandHeader(Flowable):
    """A visible graphical logo placeholder, without inventing a builder logo."""

    def __init__(self, width: float, name: str, styles: dict):
        super().__init__()
        self.width = width
        self.name = _paragraph(name or "Builder name (to be completed)", styles, "builder")
        _, self.name_height = self.name.wrap(width - 92, 1000)
        self.height = max(78, self.name_height + 40)

    def draw(self) -> None:
        pdf: Canvas = self.canv
        y = self.height - 64
        pdf.setFillColor(colors.HexColor("#F4F6F8"))
        pdf.setStrokeColor(LINE)
        pdf.roundRect(0, y, 74, 64, 4, fill=1, stroke=1)
        pdf.setStrokeColor(colors.HexColor("#DEE6EB"))
        pdf.line(8, y + 8, 66, y + 56)
        pdf.line(8, y + 56, 66, y + 8)
        pdf.setFillColor(colors.HexColor("#F4F6F8"))
        pdf.rect(13, y + 23, 48, 18, fill=1, stroke=0)
        pdf.setFillColor(GREY)
        pdf.setFont("Helvetica-Bold", 9)
        pdf.drawCentredString(37, y + 29, "LOGO")
        self.name.drawOn(pdf, 92, self.height - self.name_height)
        pdf.setFillColor(INK)
        pdf.setFont("Helvetica-Bold", 20)
        pdf.drawString(92, 12, "VARIATION QUOTE")


class _Acceptance(Flowable):
    def __init__(self, width: float):
        super().__init__()
        self.width, self.height = width, 112

    def draw(self) -> None:
        pdf: Canvas = self.canv
        pdf.setFillColor(TEAL)
        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(0, 98, "Acceptance")
        pdf.setFillColor(INK)
        pdf.setFont("Helvetica", 8)
        pdf.drawString(0, 82, "I accept the scope and quoted total shown in this variation quotation.")
        for name, label, x, width in (("accepted_by", "Client / authorised name", 0, self.width*.47),
                                      ("accepted_on", "Date", self.width*.51, self.width*.49)):
            pdf.drawString(x, 65, label)
            pdf.acroForm.textfield(name=name, tooltip=label, value="", x=x, y=39, width=width, height=21,
                                  relative=True, fontName="Helvetica", fontSize=9, borderColor=LINE,
                                  fillColor=colors.white, textColor=colors.black, borderWidth=.6, forceBorder=True)
        pdf.drawString(0, 25, "Signature")
        pdf.acroForm.textfield(name="acceptance_signature", tooltip="Signature", value="", x=55, y=17,
                              width=self.width - 55, height=21, relative=True, fontName="Helvetica", fontSize=9,
                              borderColor=LINE, fillColor=colors.white, textColor=colors.black, borderWidth=.6, forceBorder=True)
        pdf.setFillColor(GREY)
        pdf.setFont("Helvetica", 7)
        pdf.drawString(0, 3, "Typed fields are not digital signatures. Return the signed quotation for the builder's records.")


def _build_pdf(story: list[Any], *, title: str, reference: str, pagesize: tuple[float, float] = A4) -> bytes:
    output = io.BytesIO()

    def footer(pdf: Canvas, document: Any) -> None:
        pdf.saveState()
        pdf.setStrokeColor(LINE)
        pdf.line(36, 31, pagesize[0] - 36, 31)
        pdf.setFillColor(GREY)
        pdf.setFont("Helvetica", 7)
        pdf.drawString(36, 19, reference)
        pdf.drawRightString(pagesize[0] - 36, 19, f"Page {document.page}")
        pdf.restoreState()

    doc = SimpleDocTemplate(output, pagesize=pagesize, leftMargin=36, rightMargin=36, topMargin=30, bottomMargin=42,
                            title=title, author="House Sync", invariant=True, pageCompression=0)
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()


def _allowance_sections(public: dict, styles: dict, width: float) -> list[Any]:
    content = []
    for key, title in (("pc_allowances", "Prime cost (PC) allowances"), ("ps_allowances", "Provisional sum (PS) allowances")):
        rows = public.get(key, [])
        if not isinstance(rows, list):
            raise ValueError("Public allowances must be lists")
        if not rows:
            content.append(_paragraph(f"{title}: none included in this variation.", styles, "small"))
            continue
        content.append(_paragraph(title, styles, "heading"))
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Public allowance rows must be objects")
            content.append(_paragraph(_text(row.get("description")), styles))
            cells = [[_paragraph(label, styles, "header") for label in ("Previous allowance", "Proposed allowance", "Movement")],
                     [_paragraph(_money(row[field]), styles) for field in ("before", "after", "movement")]]
            content.append(_table(cells, [width/3]*3, header=True))
            notes = row.get("notes", "")
            if isinstance(notes, list):
                for note in notes:
                    content.append(_paragraph(_text(note), styles, "small"))
            elif notes:
                content.append(_paragraph(_text(notes), styles, "small"))
            content.append(Spacer(1, 5))
    return content


def client_variation_quote_pdf(public: dict) -> bytes:
    """Render exclusively client-allowlisted values, without item-level costs."""
    if not isinstance(public, dict) or public.get("currency") != "AUD":
        raise ValueError("Client variation quotes require an AUD public projection")
    styles, width = _styles(), A4[0] - 72
    reference = _text(public.get("quote_reference"), "Variation quote")
    totals = public.get("totals")
    if not isinstance(totals, dict):
        raise ValueError("A public quote total schedule is required")
    story: list[Any] = [_BrandHeader(width, _text(public.get("builder_name")), styles), Spacer(1, 6),
                        _paragraph(reference, styles, "value"),
                        _paragraph(f"Project: {_text(public.get('project_name'))}", styles),
                        _paragraph(f"Issued: {_text(public.get('issued_on'))} | Valid until: {_text(public.get('valid_until'))}", styles),
                        _paragraph(f"Previous design: {_text(public.get('before_revision'))} | Proposed design: {_text(public.get('after_revision'))}", styles, "small")]
    if public.get("client_name"):
        story.append(_paragraph(f"Client: {_text(public['client_name'])}", styles))
    story.append(_paragraph("Variation scope", styles, "heading"))
    scope = public.get("scope", [])
    if not isinstance(scope, list):
        raise ValueError("Public scope must be a list of plain descriptions")
    if public.get("client_description") and public["client_description"] not in scope:
        story.append(_paragraph(_text(public["client_description"]), styles))
    for item in scope:
        story.append(_paragraph(_text(item), styles))
    if not scope and not public.get("client_description"):
        story.append(_paragraph("No physical design changes are described for this variation.", styles))
    story.extend(_allowance_sections(public, styles, width))
    story.append(_paragraph("Variation valuation - AUD, excluding GST unless shown", styles, "heading"))
    markup_label = _text(public.get("markup_label"), "Combined overhead and margin (markup on cost)")
    if public.get("overhead_margin_percent") is not None:
        markup_label += f" ({_number(public['overhead_margin_percent'])}%)"
    summary = [
        ("At-cost additions", totals["additions_at_cost"]),
        ("Credits / omissions at cost (deduct)", -_numeric(totals["credits_at_cost"])),
        ("Net at-cost difference", totals["at_cost_delta"]),
        (markup_label, totals["markup_amount"]),
        ("Fixed administration fee", totals["admin_fee"]),
        ("Subtotal excluding GST", totals["subtotal_ex_gst"]),
        (f"GST ({_number(totals['gst_rate'])}%)", totals["gst_amount"]),
        ("TOTAL INCLUDING GST (CREDIT)" if _numeric(totals["total_incl_gst"]) < 0 else "TOTAL INCLUDING GST", totals["total_incl_gst"]),
    ]
    table = _table([[_paragraph(label, styles, "value" if index in {5, 7} else "body"),
                     _paragraph(_money(value), styles, "value" if index in {5, 7} else "body")]
                    for index, (label, value) in enumerate(summary)], [width*.70, width*.30])
    table.setStyle(TableStyle([("BACKGROUND", (0, 2), (-1, 2), PALE), ("BACKGROUND", (0, 7), (-1, 7), colors.HexColor("#E5F0ED")),
                               ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    story.append(table)
    for key in ("credit_policy", "admin_policy"):
        if public.get(key):
            story.append(_paragraph(_text(public[key]), styles, "small"))
    notes = public.get("notes", [])
    if isinstance(notes, str):
        notes = [notes]
    if not isinstance(notes, list):
        raise ValueError("Public quote notes must be plain text")
    story.extend(_paragraph(_text(note), styles, "small") for note in notes)
    story.extend([Spacer(1, 10), _Acceptance(width)])
    return _build_pdf(story, title=f"Client variation quote {reference}", reference=f"{reference} | For review and acceptance")


def internal_variation_breakdown_pdf(internal: dict, comparison: dict) -> bytes:
    """Render estimator pricing detail separately from the client document."""
    if not isinstance(internal, dict) or not isinstance(comparison, dict):
        raise ValueError("An internal quote calculation and saved comparison are required")
    styles, width = _styles(), landscape(A4)[0] - 72
    quote = comparison.get("quote", {})
    public = quote.get("public", {}) if isinstance(quote, dict) else {}
    reference = _text(public.get("quote_reference"), "Internal variation")
    story: list[Any] = [_paragraph("INTERNAL VARIATION BREAKDOWN", styles, "title"),
                        _paragraph("Estimator copy - itemised pricing and measurement audit", styles),
                        _paragraph(reference, styles, "value"),
                        _paragraph(f"Previous design: {_text(comparison.get('before_revision'))} | Proposed design: {_text(comparison.get('after_revision'))}", styles, "small")]
    policy = internal.get("builder_pricing", {})
    if isinstance(policy, dict):
        story.append(_paragraph("Pricing basis", styles, "heading"))
        for field, label in (("overhead_margin_percent", "Combined overhead and margin (markup on cost)"),
                             ("admin_fee_aud", "Requested fixed administration fee"), ("gst_percent", "GST percentage"),
                             ("credit_treatment", "Credit treatment"), ("validity_days", "Quote validity days")):
            if field in policy:
                value = policy[field]
                shown = _money(value) if field == "admin_fee_aud" else f"{_number(value)}%" if field.endswith("percent") else str(value)
                story.append(_paragraph(f"{label}: {shown}", styles))
    summary = internal.get("summary", [])
    if isinstance(summary, list) and summary:
        story.append(_paragraph("Estimator change notes", styles, "heading"))
        story.extend(_paragraph(_text(note), styles, "small") for note in summary)
    internal_totals = internal.get("totals", {})
    if isinstance(internal_totals, dict) and internal_totals:
        story.append(_paragraph("Commercial calculation", styles, "heading"))
        order = ("boq_cost_delta", "allowance_cost_delta", "additions_at_cost", "credits_at_cost", "at_cost_delta",
                 "additions_markup", "credits_markup", "markup_amount", "additions_quote", "credits_quote", "admin_fee",
                 "subtotal_ex_gst", "gst_amount", "total_incl_gst")
        rows = [[_paragraph(key.replace("_", " ").capitalize(), styles), _paragraph(_money(internal_totals[key]), styles)]
                for key in order if key in internal_totals]
        if rows:
            story.append(_table(rows, [width*.68, width*.32]))
    if internal.get("rounding_policy"):
        story.append(_paragraph(_text(internal["rounding_policy"]), styles, "small"))
    if "requested_admin_fee_aud" in internal:
        story.append(_paragraph(f"Requested fixed administration fee: {_money(internal['requested_admin_fee_aud'])}; "
                                f"fee applied: {'Yes' if internal.get('admin_fee_applied') else 'No'}.", styles, "small"))
    story.append(_paragraph("Measured BOQ item differences", styles, "heading"))
    items = internal.get("boq_changes", comparison.get("items", []))
    headers = ["Item", "Description", "Quantity A", "Quantity B", "Rate A", "Rate B", "Amount A", "Amount B", "Difference"]
    rows = [[_paragraph(label, styles, "header") for label in headers]]
    for item in items:
        unit = _text(item.get("unit"))
        values = [item.get("code", ""), item.get("description", ""), f"{_number(item['before_quantity'])} {unit}",
                  f"{_number(item['after_quantity'])} {unit}", _number(item["before_rate"]), _number(item["after_rate"]),
                  _money(item["before_amount"]), _money(item["after_amount"]), _money(item["cost_delta"])]
        rows.append([_paragraph(value, styles, "small") for value in values])
    if len(rows) == 1:
        rows.append([_paragraph("No changed BOQ items", styles, "small")] + [""]*8)
    fixed = [78, 180, 58, 58, 51, 51, 70, 70]
    story.append(_table(rows, [*fixed, width - sum(fixed)], header=True))
    story.append(_paragraph("Quantity, rate and rounding basis", styles, "heading"))
    for item in items:
        lines = [f"{item.get('code', '')}: net quantity A {item.get('before_net_quantity', 'Not recorded')} to B {item.get('after_net_quantity', 'Not recorded')}; "
                 f"wastage A {item.get('before_wastage_percent', 'Not recorded')}% to B {item.get('after_wastage_percent', 'Not recorded')}%. "
                 f"Quantity effect {item.get('quantity_effect')}; rate effect {item.get('rate_effect')}; rounding {item.get('rounding_effect')} AUD."]
        for label, prefix in (("A", "before"), ("B", "after")):
            lines.append(f"{label} origin {item.get(prefix + '_rate_origin') or 'Not recorded'}; source {item.get(prefix + '_rate_source') or 'Not recorded'}; "
                         f"effective date {item.get(prefix + '_rate_effective_date') or 'Not recorded'}.")
            saved = item.get(prefix)
            if isinstance(saved, dict):
                fields = [f"{key.replace('_', ' ')}: {saved[key]}" for key in
                          ("description", "measured_quantity", "net_quantity", "gross_quantity", "wastage_percent", "paint_coats", "unit", "rate", "amount") if key in saved]
                lines.append(f"Saved {label}: " + "; ".join(fields) + ".")
        story.append(_paragraph("\n".join(lines), styles, "small"))
    allowance_changes = internal.get("allowance_changes", [])
    if isinstance(allowance_changes, list) and allowance_changes:
        story.append(_paragraph("Prime cost and provisional sum audit", styles, "heading"))
        for item in allowance_changes:
            if not isinstance(item, dict):
                continue
            story.append(_paragraph(f"Allowance {_text(item.get('id'))}", styles, "heading"))
            for label, key in (("A", "before"), ("B", "after")):
                saved = item.get(key)
                if not isinstance(saved, dict):
                    story.append(_paragraph(f"{label}: not present", styles, "small"))
                    continue
                story.append(_paragraph(f"{label}: {_text(saved.get('kind'))}; amount {_money(saved.get('amount', 0))}; "
                                        f"description {_text(saved.get('description'))}\nNote: {_text(saved.get('note'))}", styles, "small"))
            story.append(_paragraph(f"Before {_money(item.get('before_amount', 0))}; after {_money(item.get('after_amount', 0))}; "
                                    f"movement {_money(item.get('cost_delta', 0))}.", styles, "small"))
    for label, key in (("A", "before_rate_library"), ("B", "after_rate_library")):
        library = comparison.get(key) or {}
        story.append(_paragraph(f"Saved pricing library {label}: {library.get('sha256', 'Not recorded')}", styles, "small"))
        for code, item in sorted(library.get("rows", {}).items()):
            story.append(_paragraph(f"{code}: {item.get('rate')} {item.get('currency')}/{item.get('unit')}; source {item.get('source')}; "
                                    f"effective date {item.get('effective_date')}; notes {item.get('notes', '')}", styles, "small"))
    return _build_pdf(story, title=f"Internal variation breakdown {reference}", reference=f"{reference} | INTERNAL ESTIMATOR COPY", pagesize=landscape(A4))


def export_quote_documents(comparison: dict, directory: Path) -> dict[str, str]:
    """Write two separate documents; the client file is never given private data."""
    quote = comparison.get("quote")
    if not isinstance(quote, dict) or not isinstance(quote.get("public"), dict) or not isinstance(quote.get("internal"), dict):
        raise ValueError("A prepared public/internal variation quote is required")
    client = client_variation_quote_pdf(quote["public"])
    estimator = internal_variation_breakdown_pdf(quote["internal"], comparison)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "client-variation-quote.pdf").write_bytes(client)
    (directory / "internal-variation-breakdown.pdf").write_bytes(estimator)
    return {"client_quote_pdf": "client-variation-quote.pdf", "internal_breakdown_pdf": "internal-variation-breakdown.pdf"}
