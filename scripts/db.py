import logging
import os
import time

import psycopg2

log = logging.getLogger(__name__)


def get_conn():
    for attempt in range(3):
        try:
            return psycopg2.connect(
                host=os.environ.get("DB_HOST", "db"),
                port=int(os.environ.get("DB_PORT", 5432)),
                dbname=os.environ["POSTGRES_DB"],
                user=os.environ["POSTGRES_USER"],
                password=os.environ["POSTGRES_PASSWORD"],
            )
        except psycopg2.OperationalError as exc:
            if attempt < 2:
                log.warning("DB connection attempt %d/3 failed: %s", attempt + 1, exc)
                time.sleep(5)
            else:
                raise
