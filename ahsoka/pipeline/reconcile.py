"""Optional reconciliation of estimated costs against Anthropic's Admin API.

Notes (researched 2026-11-05 from Anthropic's docs):
- Uses the legacy `GET /v1/organizations/cost_report` endpoint (daily UTC buckets,
  `amount` is a decimal string in cents, paginated via `has_more`/`next_page`).
- Requires an Admin API key (org accounts only); the figure is organization-wide,
  not specific to this bot.
- Unconfirmed whether the legacy amounts include the 50% batch discount, so the
  billed figure is approximate and drift should be read as a rough signal.
- Any failure is logged as a warning (never the key or response body) and ignored.
"""
import logging
import math
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

import aiosqlite
import httpx

from ahsoka import database as db
from ahsoka.config import settings

logger = logging.getLogger(__name__)

COST_REPORT_URL = "https://api.anthropic.com/v1/organizations/cost_report"
_MAX_PAGES = 50


async def _fetch(client: httpx.AsyncClient, day: str) -> float | None:
    start = datetime.strptime(day, "%Y-%m-%d")
    params: dict[str, str] = {
        "starting_at": start.strftime("%Y-%m-%dT00:00:00Z"),
        "ending_at": (start + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z"),
        "bucket_width": "1d",
        "limit": "31",
    }
    headers = {
        "x-api-key": settings.anthropic_admin_key,
        "anthropic-version": "2023-06-01",
    }
    total = Decimal(0)
    for _ in range(_MAX_PAGES):
        resp = await client.get(COST_REPORT_URL, params=params, headers=headers)
        if resp.status_code != 200:
            logger.warning("Admin API cost report returned HTTP %s", resp.status_code)
            return None
        body = resp.json()
        for bucket in body["data"]:
            for item in bucket["results"]:
                total += Decimal(item["amount"])
        if body.get("has_more"):
            params["page"] = body["next_page"]
        else:
            if not total.is_finite():
                logger.warning("Admin API cost report returned a non-finite total")
                return None
            result = float(total / 100)
            if not math.isfinite(result):
                logger.warning("Admin API cost report total overflowed float range")
                return None
            return result
    logger.warning("Admin API cost report exceeded %d pages", _MAX_PAGES)
    return None


async def fetch_billed_usd(day: str, *, client: httpx.AsyncClient | None = None) -> float | None:
    """Billed USD for one UTC day (YYYY-MM-DD), or None on any failure."""
    try:
        if client is not None:
            return await _fetch(client, day)
        async with httpx.AsyncClient(timeout=30.0) as own:
            return await _fetch(own, day)
    except (httpx.HTTPError, ValueError, KeyError, TypeError, InvalidOperation) as exc:
        logger.warning("Admin API cost report failed: %s", type(exc).__name__)
        return None


async def reconcile_day(conn: aiosqlite.Connection, day: str) -> None:
    """Store billed vs estimated cost for `day`. No-op without an admin key; never raises."""
    if not settings.anthropic_admin_key:
        return
    try:
        billed = await fetch_billed_usd(day)
        if billed is None:
            return
        buckets = await db.get_cost_buckets(conn, "day", 14)
        estimated = 0.0
        for b in buckets:
            if b["bucket"] == day:
                estimated = b["scoring_usd"] + b["embedding_usd"]
                break
        await db.save_reconciliation(conn, day, billed, estimated)
    except Exception as exc:
        logger.warning("Cost reconciliation failed: %s", type(exc).__name__)
