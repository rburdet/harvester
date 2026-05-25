from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    HRFlowable,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


WORDMARK = colors.Color(0.62, 0.62, 0.62)
NAVY = colors.Color(0.20, 0.31, 0.45)
HEADER_INK = colors.Color(0.40, 0.42, 0.47)
INK = colors.Color(0.10, 0.10, 0.10)
HAIRLINE = colors.Color(0.78, 0.78, 0.78)
SHADE = colors.Color(0.94, 0.94, 0.94)


@dataclass
class BillTo:
    name: str
    address_lines: list[str] = field(default_factory=list)
    email: str = ""


@dataclass
class LineItem:
    description: str
    qty: float = 1.0
    unit_price: float = 0.0

    @property
    def total(self) -> float:
        return self.qty * self.unit_price


@dataclass
class InvoiceMeta:
    vendor_name: str
    vendor_title: str
    number: int | str
    invoice_date: date
    bill_to: BillTo
    items: list[LineItem]
    currency: str = "$"
    min_table_rows: int = 9  # pad with blank rows for the classic invoice look


def _money(currency: str, amount: float) -> str:
    # Match the sample template: no decimals (e.g. "$8333").
    return f"{currency}{int(round(amount))}"


def _qty(q: float) -> str:
    return str(int(q)) if q == int(q) else f"{q:g}"


def _styles():
    return {
        "wordmark": ParagraphStyle(
            "wordmark", fontName="Helvetica-Bold", fontSize=32, leading=36,
            alignment=2, textColor=WORDMARK,
        ),
        "vendor_name": ParagraphStyle(
            "vn", fontName="Helvetica-Bold", fontSize=12, leading=16, textColor=INK,
        ),
        "vendor_title": ParagraphStyle(
            "vt", fontName="Helvetica-Bold", fontSize=11, leading=14, textColor=INK,
        ),
        "meta_line": ParagraphStyle(
            "ml", fontName="Helvetica", fontSize=10, leading=14, textColor=INK,
            alignment=2,
        ),
        "billto_label": ParagraphStyle(
            "bl", fontName="Helvetica-Bold", fontSize=9, leading=12, textColor=NAVY,
        ),
        "bill_company": ParagraphStyle(
            "bc", fontName="Helvetica-Bold", fontSize=10, leading=13, textColor=INK,
        ),
        "bill_line": ParagraphStyle(
            "bln", fontName="Helvetica", fontSize=9, leading=12, textColor=INK,
        ),
        "th": ParagraphStyle(
            "th", fontName="Helvetica-Bold", fontSize=8.5, leading=11,
            textColor=HEADER_INK, alignment=1,
        ),
        "th_left": ParagraphStyle(
            "thl", fontName="Helvetica-Bold", fontSize=8.5, leading=11,
            textColor=HEADER_INK, alignment=0,
        ),
        "th_right": ParagraphStyle(
            "thr", fontName="Helvetica-Bold", fontSize=8.5, leading=11,
            textColor=HEADER_INK, alignment=2,
        ),
        "td": ParagraphStyle(
            "td", fontName="Helvetica", fontSize=10, leading=13, textColor=INK,
        ),
        "td_center": ParagraphStyle(
            "tdc", fontName="Helvetica", fontSize=10, leading=13, textColor=INK,
            alignment=1,
        ),
        "td_right": ParagraphStyle(
            "tdr", fontName="Helvetica", fontSize=10, leading=13, textColor=INK,
            alignment=2,
        ),
        "totals_label": ParagraphStyle(
            "tl", fontName="Helvetica-Bold", fontSize=9, leading=12,
            textColor=HEADER_INK, alignment=2,
        ),
        "totals_value": ParagraphStyle(
            "tv", fontName="Helvetica-Bold", fontSize=10, leading=13, textColor=INK,
            alignment=2,
        ),
        "due_label": ParagraphStyle(
            "dl", fontName="Helvetica-Bold", fontSize=14, leading=18, textColor=NAVY,
            alignment=2,
        ),
        "due_value": ParagraphStyle(
            "dv", fontName="Helvetica-Bold", fontSize=12, leading=16, textColor=INK,
            alignment=2,
        ),
    }


def _header_block(meta: InvoiceMeta, styles) -> Table:
    # Row 1: wordmark "INVOICE" alone on the right.
    # Rows 2/3: vendor name+title on the left, invoice no/date on the right.
    rows = [
        ["", Paragraph("INVOICE", styles["wordmark"])],
        [
            Paragraph(meta.vendor_name, styles["vendor_name"]),
            Paragraph(f'<b>Invoice No:</b>  {meta.number}', styles["meta_line"]),
        ],
        [
            Paragraph(meta.vendor_title, styles["vendor_title"]),
            Paragraph(
                f'<b>Invoice Date:</b>  {meta.invoice_date.isoformat()}',
                styles["meta_line"],
            ),
        ],
    ]
    t = Table(rows, colWidths=[4.0 * inch, 3.0 * inch])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (1, 0), 16),  # gap under the wordmark row
    ]))
    return t


def _billto_block(bill: BillTo, styles) -> list:
    flow: list = []
    flow.append(HRFlowable(width=2.4 * inch, thickness=0.5, color=HAIRLINE,
                           spaceBefore=0, spaceAfter=4, hAlign="LEFT"))
    flow.append(Paragraph("BILL TO", styles["billto_label"]))
    flow.append(HRFlowable(width=2.4 * inch, thickness=0.5, color=HAIRLINE,
                           spaceBefore=4, spaceAfter=6, hAlign="LEFT"))
    flow.append(Paragraph(bill.name, styles["bill_company"]))
    for line in bill.address_lines:
        flow.append(Paragraph(line, styles["bill_line"]))
    if bill.email:
        flow.append(Paragraph(
            f'<link href="mailto:{bill.email}" color="#1a44cc">'
            f'<u>{bill.email}</u></link>',
            styles["bill_line"],
        ))
    return flow


def _items_table(meta: InvoiceMeta, styles) -> Table:
    headers = [
        Paragraph("DESCRIPTION", styles["th_left"]),
        Paragraph("QTY", styles["th"]),
        Paragraph("UNIT PRICE", styles["th_right"]),
        Paragraph("TOTAL", styles["th_right"]),
    ]
    rows: list[list] = [headers]
    for item in meta.items:
        rows.append([
            Paragraph(item.description, styles["td"]),
            Paragraph(_qty(item.qty), styles["td_center"]),
            Paragraph(_money(meta.currency, item.unit_price), styles["td_right"]),
            Paragraph(_money(meta.currency, item.total), styles["td_right"]),
        ])
    # Pad with empty rows so the table looks consistent regardless of item count.
    while len(rows) < meta.min_table_rows + 1:  # +1 for header
        rows.append(["", "", "", ""])

    t = Table(
        rows,
        colWidths=[3.0 * inch, 1.0 * inch, 1.5 * inch, 1.5 * inch],
        repeatRows=1,
    )

    style: list = [
        ("BACKGROUND", (0, 0), (-1, 0), SHADE),
        ("BOX", (0, 0), (-1, -1), 0.5, HAIRLINE),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, HAIRLINE),
        ("LINEAFTER", (0, 0), (-2, -1), 0.5, HAIRLINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]
    # Alternating shade on the empty padding rows (first empty row is shaded).
    n_data = len(meta.items)
    for i in range(n_data + 1, len(rows)):
        if (i - (n_data + 1)) % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), SHADE))

    t.setStyle(TableStyle(style))
    return t


def _totals_block(meta: InvoiceMeta, styles) -> Table:
    subtotal = sum(i.total for i in meta.items)
    rows = [
        [
            "", "",
            Paragraph("SUBTOTAL", styles["totals_label"]),
            Paragraph(_money(meta.currency, subtotal), styles["totals_value"]),
        ],
        [
            "", "",
            Paragraph("Balance Due", styles["due_label"]),
            Paragraph(_money(meta.currency, subtotal), styles["due_value"]),
        ],
    ]
    t = Table(rows, colWidths=[3.0 * inch, 1.0 * inch, 1.5 * inch, 1.5 * inch])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        # Heavy underline below "Balance Due" value, matching the template.
        ("LINEBELOW", (3, 1), (3, 1), 1.5, INK),
        ("TOPPADDING", (0, 1), (-1, 1), 10),
    ]))
    return t


def write_invoice(meta: InvoiceMeta, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / f"invoice_{meta.number}.pdf"
    styles = _styles()

    doc = BaseDocTemplate(
        str(pdf_path),
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.6 * inch,
        title=f"Invoice {meta.number}",
        author=meta.vendor_name,
    )
    frame = Frame(
        doc.leftMargin, doc.bottomMargin,
        doc.width, doc.height,
        leftPadding=0, bottomPadding=0, rightPadding=0, topPadding=0,
        showBoundary=0,
    )
    doc.addPageTemplates([PageTemplate(id="main", frames=frame)])

    story: list = [
        _header_block(meta, styles),
        Spacer(1, 0.20 * inch),
    ]
    story.extend(_billto_block(meta.bill_to, styles))
    story += [
        Spacer(1, 0.30 * inch),
        _items_table(meta, styles),
        _totals_block(meta, styles),
        Spacer(1, 0.40 * inch),
        HRFlowable(width=2.0 * inch, thickness=0.5, color=HAIRLINE, hAlign="LEFT"),
    ]
    doc.build(story)
    return pdf_path
