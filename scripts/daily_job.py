#!/usr/bin/env python3
"""
Daily hail outreach job.

Steps:
  1. Determine pull mode (first run vs. incremental)
  2. Get target zip codes from hail_events for REPORT_YEAR
  3. Query RentCast for Active listings listed in the last 24 hours per zip
  4. Process each listing: DNC check, agent upsert, listing insert, hail event link
  5. Generate PDF report via reportlab
  6. Email report to internal_email recipients via Gmail SMTP
  7. Log job run status
"""
import logging
import os
import smtplib
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from io import BytesIO

import requests
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from db import get_conn

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

REPORT_YEAR = date.today().year - 1
PAGE_SIZE = 500
REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reports")


# ─── RentCast ────────────────────────────────────────────────────────────────


def fetch_listings_for_zip(zipcode, since_date):
    """Fetch Active listings for a zip listed on or after since_date.

    Paginates only as long as results keep appearing. Stops early if a full
    page contains no listings newer than since_date (RentCast returns newest
    first), avoiding runaway pagination on large zip codes.
    """
    url = "https://api.rentcast.io/v1/listings/sale"
    headers = {"X-Api-Key": os.environ["RENTCAST_API_KEY"]}
    offset = 0
    results = []

    while True:
        resp = requests.get(
            url,
            headers=headers,
            params={
                "zipCode": zipcode,
                "status": "Active",
                "limit": PAGE_SIZE,
                "offset": offset,
            },
            timeout=30,
        )
        resp.raise_for_status()
        page = resp.json()
        if not isinstance(page, list) or not page:
            break

        page_had_match = False
        for item in page:
            raw = item.get("listedDate") or item.get("listingDate") or ""
            if raw:
                try:
                    listed = datetime.fromisoformat(raw[:10]).date()
                    if listed >= since_date:
                        results.append(item)
                        page_had_match = True
                except ValueError:
                    results.append(item)
                    page_had_match = True
            else:
                # No date on listing — include it and let dedup handle it
                results.append(item)
                page_had_match = True

        # If an entire page had nothing newer than our cutoff, stop paginating
        if not page_had_match:
            break
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    return results


# ─── Listing processor ───────────────────────────────────────────────────────


def process_listing(conn, item):
    """
    Process a single RentCast listing. Returns True if newly inserted.

    All DB operations run in a single transaction per listing.
    Rolls back (and returns False) on DNC match or duplicate listing_id.
    """
    agent_info = item.get("listingAgent") or {}
    agent_email = (agent_info.get("email") or "").strip().lower()
    agent_name = (agent_info.get("name") or "").strip()
    brokerage = (agent_info.get("company") or agent_info.get("brokerage") or "").strip()
    rentcast_id = (item.get("id") or "").strip()
    address = (item.get("formattedAddress") or item.get("address") or "").strip()
    city_val = (item.get("city") or "").strip()
    state_val = (item.get("state") or "").strip()
    zipcode_val = (item.get("zipCode") or item.get("zipcode") or item.get("zip") or "").strip()

    raw_date = item.get("listedDate") or item.get("listingDate") or ""
    listing_date = None
    if raw_date:
        try:
            listing_date = datetime.fromisoformat(raw_date[:10]).date()
        except ValueError:
            pass

    if not agent_email or not rentcast_id:
        return False

    cur = conn.cursor()
    try:
        # DNC check — skip listing entirely if agent is on the list
        cur.execute(
            "SELECT 1 FROM do_not_contact_list WHERE LOWER(agent_email) = %s",
            (agent_email,),
        )
        if cur.fetchone():
            conn.rollback()
            log.debug("DNC skip: %s", agent_email)
            return False

        # Agent upsert — name/brokerage written on INSERT only, counter always incremented.
        # ON CONFLICT handles the case where a prior partial run already inserted this agent,
        # eliminating the SELECT-then-INSERT race that caused duplicate key violations.
        cur.execute(
            """
            INSERT INTO agents (agent_name, agent_email, brokerage)
            VALUES (%s, %s, %s)
            ON CONFLICT (agent_email) DO UPDATE
                SET agent_number_properties_listed =
                    agents.agent_number_properties_listed + 1
            RETURNING id
            """,
            (agent_name, agent_email, brokerage),
        )
        agent_id = cur.fetchone()[0]

        # Listing dedup — skip (and roll back agent counter) if already imported
        cur.execute(
            "SELECT id FROM listings WHERE rentcast_listing_id = %s",
            (rentcast_id,),
        )
        if cur.fetchone():
            conn.rollback()
            return False

        # Insert listing
        cur.execute(
            """
            INSERT INTO listings
                (rentcast_listing_id, address, city, state, zipcode, listing_date, agent_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (rentcast_id, address, city_val, state_val, zipcode_val, listing_date, agent_id),
        )
        listing_id = cur.fetchone()[0]

        # Link to matching hail events in REPORT_YEAR
        cur.execute(
            "SELECT id FROM hail_events"
            " WHERE zipcode = %s AND EXTRACT(YEAR FROM hail_date) = %s",
            (zipcode_val, REPORT_YEAR),
        )
        hail_rows = cur.fetchall()
        for (hail_event_id,) in hail_rows:
            cur.execute(
                "INSERT INTO affected_listings (hail_event_id, listing_id)"
                " VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (hail_event_id, listing_id),
            )

        conn.commit()
        return True

    except Exception:
        conn.rollback()
        raise

    finally:
        cur.close()


# ─── PDF generation ──────────────────────────────────────────────────────────


def generate_pdf(rows, report_date_str):
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
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2E4057")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F2F2")]),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]))
        elements.append(t)
        elements.append(Spacer(1, 0.15 * inch))
        elements.append(Paragraph(f"Total: {len(rows)} listing(s)", styles["Normal"]))

    doc.build(elements)
    buf.seek(0)
    return buf.read()


# ─── Email ───────────────────────────────────────────────────────────────────


def send_report(recipients, pdf_bytes, report_date_str):
    subject = f"Hail Outreach Report — {report_date_str}"
    msg = MIMEMultipart()
    msg["From"] = os.environ["SMTP_FROM"]
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.attach(MIMEText("Please find the daily hail outreach report attached.", "plain"))

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


# ─── Main ────────────────────────────────────────────────────────────────────


def main():
    run_start = datetime.now()
    report_date_str = date.today().isoformat()
    conn = get_conn()
    listings_found = 0
    new_listings = 0

    try:
        # Step 1 — Determine lookback window (last successful run, or 24 hours ago)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT run_at FROM job_runs WHERE status = 'success'"
                " ORDER BY run_at DESC LIMIT 1"
            )
            row = cur.fetchone()
        conn.commit()
        last_run_at = row[0] if row else datetime.now() - timedelta(hours=24)
        since_date = last_run_at.date()
        log.info("Fetching Active listings listed on or after %s", since_date)

        # Step 2 — Target zip codes from last year's hail events
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT zipcode FROM hail_events"
                " WHERE EXTRACT(YEAR FROM hail_date) = %s",
                (REPORT_YEAR,),
            )
            zipcodes = [r[0] for r in cur.fetchall()]
        conn.commit()
        log.info("REPORT_YEAR=%d — %d target zip code(s)", REPORT_YEAR, len(zipcodes))

        # Step 3 + 4 — Fetch Active listings and process (one call per zip)
        for zipcode in zipcodes:
            try:
                listings = fetch_listings_for_zip(zipcode, since_date)
            except requests.RequestException as exc:
                log.warning("RentCast error zip=%s: %s", zipcode, exc)
                continue

            listings_found += len(listings)
            if listings:
                log.info("zip=%s — %d listing(s)", zipcode, len(listings))

            for item in listings:
                try:
                    if process_listing(conn, item):
                        new_listings += 1
                except Exception as exc:
                    log.warning(
                        "Error processing listing id=%s: %s", item.get("id"), exc
                    )

        log.info("Totals: %d listings found, %d newly inserted.", listings_found, new_listings)

        # Step 5 — Generate PDF report
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT l.address, l.zipcode, a.agent_name, a.agent_email,
                       he.hail_date, a.agent_report_date
                FROM affected_listings al
                JOIN listings l  ON l.id  = al.listing_id
                JOIN agents a    ON a.id  = l.agent_id
                JOIN hail_events he ON he.id = al.hail_event_id
                WHERE l.imported_at >= %s
                ORDER BY l.address
                """,
                (run_start,),
            )
            report_rows = cur.fetchall()
        conn.commit()

        pdf_bytes = generate_pdf(report_rows, report_date_str)

        os.makedirs(REPORTS_DIR, exist_ok=True)
        pdf_path = os.path.join(REPORTS_DIR, f"hail_report_{report_date_str}.pdf")
        with open(pdf_path, "wb") as fh:
            fh.write(pdf_bytes)
        log.info("PDF saved: %s (%d row(s))", pdf_path, len(report_rows))

        # Update agent_report_date for every agent appearing on this report
        if report_rows:
            agent_emails = list({(r[3] or "").lower() for r in report_rows if r[3]})
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE agents SET agent_report_date = NOW()"
                        " WHERE LOWER(agent_email) = ANY(%s)",
                        (agent_emails,),
                    )

        # Step 6 — Email report
        with conn.cursor() as cur:
            cur.execute(
                "SELECT emp_email FROM internal_email WHERE emp_email IS NOT NULL"
            )
            recipients = [r[0] for r in cur.fetchall()]
        conn.commit()

        if recipients:
            send_report(recipients, pdf_bytes, report_date_str)
        else:
            log.warning("No recipients in internal_email table — skipping email.")

        # Step 7 — Log success
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO job_runs (run_at, listings_found, new_listings, status)"
                    " VALUES (%s, %s, %s, 'success')",
                    (run_start, listings_found, new_listings),
                )
        log.info("Job completed successfully.")

    except Exception as exc:
        log.error("Job failed: %s", exc)
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO job_runs (run_at, listings_found, new_listings, status)"
                        " VALUES (%s, %s, %s, 'failed')",
                        (run_start, listings_found, new_listings),
                    )
        except Exception:
            pass
        raise

    finally:
        conn.close()


if __name__ == "__main__":
    main()
