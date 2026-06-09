#!/usr/bin/env python3
import csv
import logging
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from datetime import datetime

from db import get_conn

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def parse_hail_size(raw):
    """Return the max numeric value from a size string, handling ranges like '.5-1.5'."""
    if not raw or not raw.strip():
        return None
    cleaned = raw.strip().replace("–", "-").replace("—", "-")
    parts = re.findall(r"\d+\.?\d*", cleaned)
    return max(float(p) for p in parts) if parts else None


def normalize_header(name):
    mapping = {
        "date": "hail_date",
        "hail_date": "hail_date",
        "zip": "zipcode",
        "zip code": "zipcode",
        "zipcode": "zipcode",
        "city": "city",
        "state": "state",
        "size": "hail_size",
        "hail_size": "hail_size",
    }
    return mapping.get(name.strip().lower(), name.strip().lower())


def main():
    if len(sys.argv) < 2:
        print("Usage: python import_hail_events.py <csv_path>")
        sys.exit(1)

    csv_path = sys.argv[1]
    conn = get_conn()
    inserted = skipped = errors = 0

    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        header_map = {k: normalize_header(k) for k in (reader.fieldnames or [])}

        with conn:
            with conn.cursor() as cur:
                for lineno, row in enumerate(reader, start=2):
                    norm = {header_map.get(k, k): v for k, v in row.items()}

                    raw_date = norm.get("hail_date", "").strip()
                    zipcode = norm.get("zipcode", "").strip()
                    city = norm.get("city", "").strip() or None
                    state = norm.get("state", "").strip() or None
                    hail_size = parse_hail_size(norm.get("hail_size", ""))

                    if not raw_date or not zipcode:
                        log.warning("Line %d: missing date or zip, skipping", lineno)
                        errors += 1
                        continue

                    try:
                        hail_date = datetime.strptime(raw_date, "%m/%d/%Y").date()
                    except ValueError:
                        try:
                            hail_date = datetime.fromisoformat(raw_date).date()
                        except ValueError:
                            log.warning(
                                "Line %d: cannot parse date '%s', skipping", lineno, raw_date
                            )
                            errors += 1
                            continue

                    cur.execute(
                        "SELECT 1 FROM hail_events WHERE hail_date = %s AND zipcode = %s",
                        (hail_date, zipcode),
                    )
                    if cur.fetchone():
                        skipped += 1
                        continue

                    cur.execute(
                        """
                        INSERT INTO hail_events (hail_date, zipcode, city, state, hail_size)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (hail_date, zipcode, city, state, hail_size),
                    )
                    inserted += 1

    conn.close()
    log.info("Done: %d inserted, %d skipped (duplicate), %d errors.", inserted, skipped, errors)


if __name__ == "__main__":
    main()
