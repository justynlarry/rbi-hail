#!/usr/bin/env python3
"""Interactive script to add internal email recipients for the weekly report."""
import logging
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db import get_conn

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def main():
    conn = get_conn()
    try:
        while True:
            fname = input("First name : ").strip()
            lname = input("Last name  : ").strip()
            email = input("Email      : ").strip().lower()

            if not EMAIL_RE.match(email):
                print("Invalid email format. Please try again.")
                continue

            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO internal_email (emp_fname, emp_lname, emp_email)"
                        " VALUES (%s, %s, %s) ON CONFLICT (emp_email) DO NOTHING",
                        (fname, lname, email),
                    )
                    if cur.rowcount:
                        print(f"Added recipient: {fname} {lname} <{email}>")
                    else:
                        print(f"Already a recipient: {email}")

            if input("Add another? (y/n): ").strip().lower() != "y":
                break
    finally:
        conn.close()


if __name__ == "__main__":
    main()
