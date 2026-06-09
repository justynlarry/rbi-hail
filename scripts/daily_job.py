#!/usr/bin/env python3
"""
Weekly hail outreach job.

Steps:
  1. Acquire advisory lock — abort immediately if another instance is running
  2. Determine lookback window (last successful run, or 7 days ago)
  3. Get target zip codes from hail_events within a rolling 365-day window
  4. Load DNC set once from both do_not_contact_list and agents.do_not_contact
  5. Fetch Active listings from RentCast (parallel, one call per zip, with retry)
  6. Process each listing: DNC check, agent upsert, listing insert, hail event link
  7. Generate PDF report via reportlab
  8. Email report to internal_email recipients via Gmail SMTP
  9. Purge PDFs older than REPORT_RETENTION_DAYS
 10. Log job run status
"""
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db import get_conn
from mailer import send_report
from pipeline import process_listing
from rentcast import fetch_listings_for_zip
from report import generate_pdf

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reports")
REPORT_RETENTION_DAYS = int(os.environ.get("REPORT_RETENTION_DAYS", 90))
API_WORKERS = int(os.environ.get("API_WORKERS", 5))

# Stable integer key for the session-level advisory lock; prevents concurrent runs.
_ADVISORY_LOCK_ID = 7_891_011


def _acquire_lock(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (_ADVISORY_LOCK_ID,))
        acquired = cur.fetchone()[0]
    conn.commit()
    return acquired


def _load_dnc_set(conn):
    """Load all DNC emails into a set — covers both the authoritative list and the agent flag."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT LOWER(agent_email) FROM do_not_contact_list
             WHERE agent_email IS NOT NULL
            UNION
            SELECT LOWER(agent_email) FROM agents
             WHERE do_not_contact = TRUE AND agent_email IS NOT NULL
            """
        )
        dnc = {r[0] for r in cur.fetchall()}
    conn.commit()
    return dnc


def _purge_old_reports(cutoff_date):
    try:
        for fname in os.listdir(REPORTS_DIR):
            if not fname.endswith(".pdf"):
                continue
            fpath = os.path.join(REPORTS_DIR, fname)
            if date.fromtimestamp(os.path.getmtime(fpath)) < cutoff_date:
                os.remove(fpath)
                log.info("Purged old report: %s", fname)
    except OSError as exc:
        log.warning("Report purge skipped: %s", exc)


def main():
    run_start = datetime.now()
    report_date_str = date.today().isoformat()
    conn = get_conn()
    listings_found = 0
    new_listings = 0

    try:
        # Step 1 — Advisory lock: abort if already running
        if not _acquire_lock(conn):
            log.error("Another instance is already running — aborting.")
            return

        # Step 2 — Lookback window (last successful run, or 7 days ago)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT run_at FROM job_runs WHERE status = 'success'"
                " ORDER BY run_at DESC LIMIT 1"
            )
            row = cur.fetchone()
        conn.commit()
        last_run_at = row[0] if row else datetime.now() - timedelta(days=7)
        since_date = last_run_at.date()
        log.info("Fetching Active listings listed on or after %s", since_date)

        # Step 3 — Target zip codes in rolling 365-day window
        hail_cutoff = date.today() - timedelta(days=365)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT zipcode FROM hail_events"
                " WHERE hail_date >= %s AND hail_date <= CURRENT_DATE",
                (hail_cutoff,),
            )
            zipcodes = [r[0] for r in cur.fetchall()]
        conn.commit()
        log.info(
            "Hail window %s → %s — %d target zip(s)",
            hail_cutoff, date.today(), len(zipcodes),
        )

        # Step 4 — Load DNC set once (both do_not_contact_list and agents.do_not_contact)
        dnc_set = _load_dnc_set(conn)
        log.info("DNC set loaded: %d address(es)", len(dnc_set))

        # Step 5 — Fetch listings in parallel, process sequentially
        zip_listings: dict[str, list] = {}
        with ThreadPoolExecutor(max_workers=API_WORKERS) as pool:
            futures = {
                pool.submit(fetch_listings_for_zip, z, since_date): z
                for z in zipcodes
            }
            for future in as_completed(futures):
                zipcode = futures[future]
                try:
                    zip_listings[zipcode] = future.result()
                except Exception as exc:
                    log.warning("RentCast error zip=%s: %s", zipcode, exc)
                    zip_listings[zipcode] = []

        # Step 6 — Process listings (single-threaded DB writes)
        for zipcode, listings in zip_listings.items():
            listings_found += len(listings)
            if listings:
                log.info("zip=%s — %d listing(s)", zipcode, len(listings))
            for item in listings:
                try:
                    if process_listing(conn, item, hail_cutoff, dnc_set):
                        new_listings += 1
                except Exception as exc:
                    log.warning("Error processing listing id=%s: %s", item.get("id"), exc)

        log.info("Totals: %d listings found, %d newly inserted.", listings_found, new_listings)

        # Step 7 — Generate and save PDF report
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT l.address, l.zipcode, a.agent_name, a.agent_email,
                       he.hail_date, a.agent_report_date
                FROM affected_listings al
                JOIN listings     l  ON l.id  = al.listing_id
                JOIN agents       a  ON a.id  = l.agent_id
                JOIN hail_events  he ON he.id = al.hail_event_id
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

        # Update agent_report_date for every agent on this report
        if report_rows:
            agent_emails = list({(r[3] or "").lower() for r in report_rows if r[3]})
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE agents SET agent_report_date = NOW()"
                        " WHERE LOWER(agent_email) = ANY(%s)",
                        (agent_emails,),
                    )

        # Step 8 — Email report
        with conn.cursor() as cur:
            cur.execute(
                "SELECT emp_email FROM internal_email WHERE emp_email IS NOT NULL"
            )
            recipients = [r[0] for r in cur.fetchall()]
        conn.commit()

        if recipients:
            send_report(recipients, pdf_bytes, report_date_str)
        else:
            log.warning("No recipients in internal_email — skipping email.")

        # Step 9 — Purge old reports
        _purge_old_reports(date.today() - timedelta(days=REPORT_RETENTION_DAYS))

        # Step 10 — Log success
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
        conn.close()  # session-level advisory lock released here


if __name__ == "__main__":
    main()
