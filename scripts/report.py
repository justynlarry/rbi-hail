"""PDF report generation via reportlab."""
import logging
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

log = logging.getLogger(__name__)


def generate_pdf(rows, report_date_str):
    """Return PDF bytes for the weekly outreach report."""
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        rightMargin=0.5 * inch,
        leftMargin=0.5 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
    )
    styles = getSampleStyleSheet()
    elements = []

    elements.append(
        Paragraph(f"Hail Outreach Report — {report_date_str}", styles["Title"])
    )
    elements.append(Spacer(1, 0.2 * inch))

    if not rows:
        elements.append(
            Paragraph(f"No new listings found for {report_date_str}.", styles["Normal"])
        )
    else:
        headers = [
            "Property Address",
            "Zip Code",
            "Agent Name",
            "Agent Email",
            "Hail Date",
            "Last on Report",
        ]
        col_widths = [
            2.1 * inch,
            0.75 * inch,
            1.4 * inch,
            2.0 * inch,
            0.85 * inch,
            1.1 * inch,
        ]

        table_data = [headers]
        for address, zipcode, agent_name, agent_email, hail_date, last_report in rows:
            table_data.append([
                address or "",
                zipcode or "",
                agent_name or "",
                agent_email or "",
                str(hail_date) if hail_date else "",
                str(last_report.date()) if last_report else "First time",
            ])

        t = Table(table_data, colWidths=col_widths, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0),  colors.HexColor("#2E4057")),
            ("TEXTCOLOR",     (0, 0), (-1, 0),  colors.white),
            ("FONTNAME",      (0, 0), (-1, 0),  "Helvetica-Bold"),
            ("FONTSIZE",      (0, 0), (-1, -1), 8),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F2F2")]),
            ("GRID",          (0, 0), (-1, -1), 0.25, colors.grey),
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING",   (0, 0), (-1, -1), 4),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
        ]))
        elements.append(t)
        elements.append(Spacer(1, 0.15 * inch))
        elements.append(Paragraph(f"Total: {len(rows)} listing(s)", styles["Normal"]))

    doc.build(elements)
    buf.seek(0)
    return buf.read()
