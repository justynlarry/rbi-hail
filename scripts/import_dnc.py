#!/usr/bin/env python3
import csv
import logging
import sys

from db import get_conn

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def main():
    if len(sys.argv) < 2:
        print("Usage: python import_dnc.py <csv_path>")
        sys.exit(1)

    csv_path = sys.argv[1]
    conn = get_conn()
    inserted = skipped = agents_flagged = 0

    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        with conn:
            with conn.cursor() as cur:
                for row in reader:
                    if not row:
                        continue
                    email = row[0].strip().lower()
                    if not email or "@" not in email:
                        continue

                    cur.execute(
                        "INSERT INTO do_not_contact_list (agent_email) VALUES (%s)"
                        " ON CONFLICT DO NOTHING",
                        (email,),
                    )
                    if cur.rowcount:
                        inserted += 1
                    else:
                        skipped += 1

                    cur.execute(
                        "UPDATE agents SET do_not_contact = TRUE"
                        " WHERE LOWER(agent_email) = %s AND do_not_contact = FALSE",
                        (email,),
                    )
                    agents_flagged += cur.rowcount

    conn.close()
    log.info(
        "DNC import complete: %d added, %d already present, %d agents flagged.",
        inserted,
        skipped,
        agents_flagged,
    )


if __name__ == "__main__":
    main()
