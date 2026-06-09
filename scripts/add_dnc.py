#!/usr/bin/env python3
import logging
import re

from db import get_conn

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def main():
    conn = get_conn()
    try:
        while True:
            email = input("Enter email address: ").strip().lower()

            if not EMAIL_RE.match(email):
                print("Invalid email format. Please try again.")
                continue

            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO do_not_contact_list (agent_email) VALUES (%s)"
                        " ON CONFLICT DO NOTHING",
                        (email,),
                    )
                    if cur.rowcount:
                        print(f"Added to DNC list: {email}")
                    else:
                        print(f"Already on DNC list: {email}")

                    cur.execute(
                        "UPDATE agents SET do_not_contact = TRUE WHERE LOWER(agent_email) = %s",
                        (email,),
                    )
                    if cur.rowcount:
                        print(f"Flagged {cur.rowcount} agent record(s) as do_not_contact.")

            if input("Add another? (y/n): ").strip().lower() != "y":
                break
    finally:
        conn.close()


if __name__ == "__main__":
    main()
