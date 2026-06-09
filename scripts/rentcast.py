"""RentCast API client — listing fetch with pagination and retry."""
import logging
import os
import time
from datetime import datetime

import requests

log = logging.getLogger(__name__)

_API_URL = "https://api.rentcast.io/v1/listings/sale"
PAGE_SIZE = 500


def fetch_listings_for_zip(zipcode, since_date, *, retries=3):
    """
    Fetch Active listings for a zip listed on or after since_date.

    Paginates newest-first; stops early when a full page has no qualifying
    listings (avoids runaway pagination on dense zip codes).
    Retries on 429 / 5xx with exponential backoff.
    """
    headers = {"X-Api-Key": os.environ["RENTCAST_API_KEY"]}
    offset = 0
    results = []

    while True:
        page = _get_page(headers, zipcode, offset, retries)
        if not page:
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
                    # Unparseable date — include and let dedup handle it
                    results.append(item)
                    page_had_match = True
            else:
                # No date on listing — include and let dedup handle it
                results.append(item)
                page_had_match = True

        if not page_had_match:
            break
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    return results


def _get_page(headers, zipcode, offset, retries):
    """Single paginated GET with exponential-backoff retry on 429/5xx."""
    delay = 2
    for attempt in range(retries):
        try:
            resp = requests.get(
                _API_URL,
                headers=headers,
                params={
                    "zipCode": zipcode,
                    "status": "Active",
                    "limit": PAGE_SIZE,
                    "offset": offset,
                },
                timeout=30,
            )
            # Treat rate-limit and server errors as retryable
            if resp.status_code == 429 or resp.status_code >= 500:
                raise requests.HTTPError(
                    f"HTTP {resp.status_code}", response=resp
                )
            resp.raise_for_status()
            page = resp.json()
            return page if isinstance(page, list) else []
        except requests.RequestException as exc:
            if attempt < retries - 1:
                log.warning(
                    "RentCast zip=%s offset=%d attempt %d/%d: %s — retry in %ds",
                    zipcode, offset, attempt + 1, retries, exc, delay,
                )
                time.sleep(delay)
                delay *= 2
            else:
                raise
