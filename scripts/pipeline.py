"""Per-listing processing: DNC check, agent upsert, listing insert, hail-event linking."""
import logging
from datetime import datetime

log = logging.getLogger(__name__)


def process_listing(conn, item, hail_cutoff, dnc_set):
    """
    Upsert one RentCast listing into the database. Returns True if newly inserted.

    dnc_set:    set of lowercased emails pre-loaded by the caller (no per-listing DB query).
    hail_cutoff: only link hail events on or after this date (rolling 365-day window).

    Transaction scope: one commit per new listing; rollback on duplicate or error.
    The agent counter increment is part of the same transaction, so a duplicate-listing
    rollback also undoes the counter change — keeping the count accurate.
    """
    agent_info   = item.get("listingAgent") or {}
    agent_email  = (agent_info.get("email") or "").strip().lower()
    agent_name   = (agent_info.get("name") or "").strip()
    brokerage    = (agent_info.get("company") or agent_info.get("brokerage") or "").strip()
    rentcast_id  = (item.get("id") or "").strip()
    address      = (item.get("formattedAddress") or item.get("address") or "").strip()
    city_val     = (item.get("city") or "").strip()
    state_val    = (item.get("state") or "").strip()
    zipcode_val  = (item.get("zipCode") or item.get("zipcode") or item.get("zip") or "").strip()

    raw_date = item.get("listedDate") or item.get("listingDate") or ""
    listing_date = None
    if raw_date:
        try:
            listing_date = datetime.fromisoformat(raw_date[:10]).date()
        except ValueError:
            pass

    if not agent_email or not rentcast_id:
        return False

    # In-memory DNC check — covers both do_not_contact_list and agents.do_not_contact
    if agent_email in dnc_set:
        log.debug("DNC skip: %s", agent_email)
        return False

    cur = conn.cursor()
    try:
        # Agent upsert — name/brokerage on INSERT only; counter always incremented.
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

        # Listing insert — ON CONFLICT returns no row for a duplicate, triggering rollback.
        cur.execute(
            """
            INSERT INTO listings
                (rentcast_listing_id, address, city, state, zipcode, listing_date, agent_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (rentcast_listing_id) DO NOTHING
            RETURNING id
            """,
            (rentcast_id, address, city_val, state_val, zipcode_val, listing_date, agent_id),
        )
        row = cur.fetchone()
        if not row:
            # Already imported — roll back the agent counter increment too
            conn.rollback()
            return False
        listing_id = row[0]

        # Link to every hail event in the rolling window for this zip
        cur.execute(
            "SELECT id FROM hail_events"
            " WHERE zipcode = %s AND hail_date >= %s AND hail_date <= CURRENT_DATE",
            (zipcode_val, hail_cutoff),
        )
        for (hail_event_id,) in cur.fetchall():
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
