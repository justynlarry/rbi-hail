#!/usr/bin/env python3
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db import get_conn

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# Tables created in dependency order: agents before listings.
# Indexes and idempotent migrations follow the table definitions.
DDL = [
    # ── Tables ────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS hail_events (
        id       SERIAL PRIMARY KEY,
        hail_date DATE NOT NULL,
        zipcode  VARCHAR(10) NOT NULL,
        city     TEXT,
        state    VARCHAR(2),
        hail_size NUMERIC(4,2),
        UNIQUE (hail_date, zipcode)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agents (
        id                             SERIAL PRIMARY KEY,
        agent_name                     TEXT,
        agent_email                    TEXT UNIQUE,
        brokerage                      TEXT,
        agent_report_date              TIMESTAMP,
        agent_import_date              TIMESTAMP DEFAULT NOW(),
        agent_number_properties_listed INT DEFAULT 0,
        do_not_contact                 BOOLEAN DEFAULT FALSE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS listings (
        id                   SERIAL PRIMARY KEY,
        rentcast_listing_id  VARCHAR(100) UNIQUE,
        address              TEXT,
        city                 TEXT,
        state                VARCHAR(2),
        zipcode              VARCHAR(10),
        listing_date         DATE,
        agent_id             INT REFERENCES agents(id),
        imported_at          TIMESTAMP DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS affected_listings (
        hail_event_id INT REFERENCES hail_events(id),
        listing_id    INT REFERENCES listings(id),
        PRIMARY KEY (hail_event_id, listing_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS do_not_contact_list (
        id          SERIAL PRIMARY KEY,
        agent_email TEXT UNIQUE,
        date_added  TIMESTAMP DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS internal_email (
        emp_id    SERIAL PRIMARY KEY,
        emp_fname TEXT,
        emp_lname TEXT,
        emp_email TEXT UNIQUE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS job_runs (
        id             SERIAL PRIMARY KEY,
        run_at         TIMESTAMP DEFAULT NOW(),
        listings_found INT DEFAULT 0,
        new_listings   INT DEFAULT 0,
        status         TEXT
    )
    """,

    # ── Idempotent migrations for pre-existing installs ───────────────────────
    # Drop the dead `processed` column that was defined but never used.
    "ALTER TABLE hail_events DROP COLUMN IF EXISTS processed",

    # ── Performance indexes ───────────────────────────────────────────────────
    # Zip+date composite: used in the hail-window query and listing→event linking.
    "CREATE INDEX IF NOT EXISTS idx_hail_events_zip_date   ON hail_events (zipcode, hail_date)",
    # imported_at: used to filter listings belonging to the current run's report.
    "CREATE INDEX IF NOT EXISTS idx_listings_imported_at   ON listings (imported_at)",
    # Functional email indexes: WHERE LOWER(agent_email) = %s can use these.
    "CREATE INDEX IF NOT EXISTS idx_dnc_email_lower        ON do_not_contact_list (LOWER(agent_email))",
    "CREATE INDEX IF NOT EXISTS idx_agents_email_lower     ON agents (LOWER(agent_email))",
]


def main():
    conn = get_conn()
    with conn:
        with conn.cursor() as cur:
            for stmt in DDL:
                cur.execute(stmt)
    conn.close()
    log.info("Schema initialized — all tables, indexes, and migrations applied.")


if __name__ == "__main__":
    main()
