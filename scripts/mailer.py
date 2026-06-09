"""Gmail SMTP email sender for the weekly outreach report."""
import logging
import os
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

log = logging.getLogger(__name__)


def send_report(recipients, pdf_bytes, report_date_str):
    subject = f"Hail Outreach Report — {report_date_str}"
    msg = MIMEMultipart()
    msg["From"] = os.environ["SMTP_FROM"]
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.attach(MIMEText("Please find the weekly hail outreach report attached.", "plain"))

    part = MIMEApplication(pdf_bytes, Name=f"hail_report_{report_date_str}.pdf")
    part["Content-Disposition"] = (
        f'attachment; filename="hail_report_{report_date_str}.pdf"'
    )
    msg.attach(part)

    with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", 587))) as server:
        server.ehlo()
        server.starttls()
        server.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
        server.sendmail(os.environ["SMTP_FROM"], recipients, msg.as_string())

    log.info("Report emailed to: %s", ", ".join(recipients))
