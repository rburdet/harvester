from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from .aggregate import DayEntry


HAIRLINE = colors.Color(0.85, 0.85, 0.85)
INK = colors.Color(0.1, 0.1, 0.1)
MUTED = colors.Color(0.45, 0.45, 0.45)


@dataclass
class ReportMeta:
    title: str = "Timesheet"
    brand: str = ""
    timeframe: str = ""
    author: str = ""
    project: str = ""
    task: str = ""
    # Top-right wordmark on the Clients Report. Distinct from `brand` (which is the
    # client name) — this is the Harvest account/company name (your own org).
    account: str = ""


def _styles():
    return {
        "title": ParagraphStyle(
            "title", fontName="Helvetica-Bold", fontSize=26, leading=30, textColor=INK,
        ),
        "brand": ParagraphStyle(
            "brand", fontName="Helvetica", fontSize=20, leading=24,
            textColor=INK, alignment=2,  # right
        ),
        "label": ParagraphStyle(
            "label", fontName="Helvetica", fontSize=9, textColor=MUTED, leading=14,
        ),
        "value": ParagraphStyle(
            "value", fontName="Helvetica-Bold", fontSize=9, textColor=INK, leading=14,
        ),
        "cell": ParagraphStyle(
            "cell", fontName="Helvetica", fontSize=9, textColor=INK, leading=12,
        ),
        "cell_b": ParagraphStyle(
            "cell_b", fontName="Helvetica-Bold", fontSize=9, textColor=INK, leading=12,
        ),
    }


def _header_table(meta: ReportMeta, styles) -> Table:
    title_row = Table(
        [[Paragraph(meta.title, styles["title"]), Paragraph(meta.brand or "", styles["brand"])]],
        colWidths=[3.5 * inch, 3.5 * inch],
    )
    title_row.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, HAIRLINE),
    ]))
    return title_row


def _meta_table(meta: ReportMeta, total_hours: float, styles) -> Table:
    rows = [
        [
            Paragraph("Timeframe", styles["label"]),
            Paragraph(meta.timeframe, styles["value"]),
            Paragraph("Project", styles["label"]),
            Paragraph(meta.project, styles["value"]),
        ],
        [
            Paragraph("Total", styles["label"]),
            Paragraph(f"{total_hours:.1f} Hours", styles["value"]),
            Paragraph("Task", styles["label"]),
            Paragraph(meta.task, styles["value"]),
        ],
        [
            Paragraph("Author", styles["label"]),
            Paragraph(meta.author, styles["value"]),
            Paragraph("", styles["label"]),
            Paragraph("", styles["value"]),
        ],
    ]
    t = Table(rows, colWidths=[0.9 * inch, 2.4 * inch, 0.7 * inch, 3.0 * inch])
    t.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return t


def _entries_table(entries: list[DayEntry], total: float, styles) -> Table:
    headers = ["Date", "Day", "Hours", "Tickets", "Source"]
    data: list[list] = [[Paragraph(h, styles["label"]) for h in headers]]
    for e in entries:
        weekday = e.day.strftime("%a")
        source = (
            f"carried from {e.carried_from.isoformat()}"
            if e.carried_from
            else f"{e.commit_count} commits"
        )
        data.append([
            Paragraph(e.day.strftime("%m/%d/%Y"), styles["cell"]),
            Paragraph(weekday, styles["cell"]),
            Paragraph(f"{e.hours:.1f}", styles["cell"]),
            Paragraph(", ".join(e.tickets), styles["cell"]),
            Paragraph(source, styles["cell"]),
        ])
    data.append([
        Paragraph("Total", styles["cell_b"]),
        Paragraph("", styles["cell_b"]),
        Paragraph(f"{total:.1f}", styles["cell_b"]),
        Paragraph("", styles["cell_b"]),
        Paragraph("", styles["cell_b"]),
    ])
    t = Table(
        data,
        colWidths=[0.95 * inch, 0.55 * inch, 0.65 * inch, 2.5 * inch, 2.35 * inch],
        repeatRows=1,
    )
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, HAIRLINE),     # under header
        ("LINEABOVE", (0, -1), (-1, -1), 0.5, HAIRLINE),   # above total
        ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.97, 0.97, 0.97)),
    ]))
    return t


def _on_page(canvas_, doc):
    canvas_.saveState()
    canvas_.setFont("Helvetica", 9)
    canvas_.setFillColor(MUTED)
    canvas_.drawCentredString(letter[0] / 2, 0.5 * inch, f"Page {doc.page} of {doc.page}")
    canvas_.restoreState()


def _clients_header(title: str, account: str, styles) -> Table:
    row = Table(
        [[Paragraph(title, styles["title"]), Paragraph(account or "", styles["brand"])]],
        colWidths=[3.5 * inch, 3.5 * inch],
    )
    row.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, HAIRLINE),
    ]))
    return row


def _clients_meta_table(timeframe: str, total_hours: float, styles) -> Table:
    rows = [
        [
            Paragraph("Timeframe", styles["label"]),
            Paragraph(timeframe, styles["value"]),
            Paragraph("Clients", styles["label"]),
            Paragraph("All clients", styles["value"]),
        ],
        [
            Paragraph("Total", styles["label"]),
            Paragraph(f"{total_hours:.1f} Hours", styles["value"]),
            Paragraph("Projects", styles["label"]),
            Paragraph("All projects", styles["value"]),
        ],
        [
            Paragraph("", styles["label"]),
            Paragraph("", styles["value"]),
            Paragraph("Tasks", styles["label"]),
            Paragraph("All tasks", styles["value"]),
        ],
        [
            Paragraph("", styles["label"]),
            Paragraph("", styles["value"]),
            Paragraph("Team", styles["label"]),
            Paragraph("Everyone", styles["value"]),
        ],
    ]
    t = Table(rows, colWidths=[0.9 * inch, 2.4 * inch, 0.9 * inch, 2.8 * inch])
    t.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return t


def _clients_table(client_name: str, total_hours: float, styles) -> Table:
    headers = ["Client name", "Hours", "Billable hours", "Billable amount", "Invoiced amount"]
    data = [
        [Paragraph(h, styles["label"]) for h in headers],
        [
            Paragraph(client_name, styles["cell"]),
            Paragraph(f"{total_hours:.1f}", styles["cell"]),
            Paragraph(f"{total_hours:.1f}", styles["cell"]),
            Paragraph("", styles["cell"]),
            Paragraph("", styles["cell"]),
        ],
        [
            Paragraph("Total", styles["cell_b"]),
            Paragraph(f"{total_hours:.1f}", styles["cell_b"]),
            Paragraph(f"{total_hours:.1f}", styles["cell_b"]),
            Paragraph("", styles["cell_b"]),
            Paragraph("", styles["cell_b"]),
        ],
    ]
    t = Table(
        data,
        colWidths=[2.4 * inch, 0.9 * inch, 1.2 * inch, 1.3 * inch, 1.2 * inch],
        repeatRows=1,
    )
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.96, 0.96, 0.96)),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, HAIRLINE),     # under header
        ("LINEABOVE", (0, -1), (-1, -1), 0.5, HAIRLINE),   # above total
    ]))
    return t


def write_clients_report(
    entries: list[DayEntry],
    out_dir: Path,
    frm: date,
    to: date,
    *,
    meta: ReportMeta,
) -> Path:
    """Render a Harvest-style "Clients Report" PDF: one row per client with totals."""
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / f"harvest_clients_report_from{frm.isoformat()}to{to.isoformat()}.pdf"
    total = sum(e.hours for e in entries)
    styles = _styles()
    timeframe = f"{frm.strftime('%m/%d/%Y')} – {to.strftime('%m/%d/%Y')}"
    # Top-right wordmark is the Harvest account (your own org), NOT the
    # client name — don't fall back to meta.brand, which is the client.
    account = meta.account or ""
    client_name = meta.brand or ""

    doc = BaseDocTemplate(
        str(pdf_path),
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
        title=f"Clients Report {frm.isoformat()} to {to.isoformat()}",
        author=meta.author,
    )
    frame = Frame(
        doc.leftMargin, doc.bottomMargin,
        doc.width, doc.height,
        leftPadding=0, bottomPadding=0, rightPadding=0, topPadding=0,
        showBoundary=0,
    )
    doc.addPageTemplates([PageTemplate(id="main", frames=frame, onPage=_on_page)])

    story = [
        _clients_header("Clients Report", account, styles),
        Spacer(1, 0.20 * inch),
        _clients_meta_table(timeframe, total, styles),
        Spacer(1, 0.30 * inch),
        _clients_table(client_name, total, styles),
    ]
    doc.build(story)
    return pdf_path


def write(
    entries: list[DayEntry],
    out_dir: Path,
    month_label: str,
    *,
    meta: ReportMeta,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / f"timesheet_{month_label}.pdf"
    total = sum(e.hours for e in entries)
    styles = _styles()

    doc = BaseDocTemplate(
        str(pdf_path),
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
        title=f"{meta.title} {month_label}",
        author=meta.author,
    )
    frame = Frame(
        doc.leftMargin, doc.bottomMargin,
        doc.width, doc.height,
        leftPadding=0, bottomPadding=0, rightPadding=0, topPadding=0,
        showBoundary=0,
    )
    doc.addPageTemplates([PageTemplate(id="main", frames=frame, onPage=_on_page)])

    story = [
        _header_table(meta, styles),
        Spacer(1, 0.20 * inch),
        _meta_table(meta, total, styles),
        Spacer(1, 0.30 * inch),
        _entries_table(entries, total, styles),
    ]
    doc.build(story)
    return pdf_path
