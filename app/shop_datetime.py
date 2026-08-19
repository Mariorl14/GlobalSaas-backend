"""Shop-local naive datetimes (America/Costa_Rica by default)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.models import Business
from app.shop_insights import business_timezone


def parse_shop_local_dt(value, business_id: UUID | None = None) -> datetime | None:
    """
    Parse ISO datetimes to naive wall-clock in the shop timezone.

    Naive strings from the UI are stored as-is (already shop local).
    UTC/Z or offset-aware values are converted to the shop zone (CR-first).
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip()
        try:
            normalized = s.replace("Z", "+00:00").replace("z", "+00:00")
            dt = datetime.fromisoformat(normalized)
        except ValueError:
            return None

    business = Business.query.get(business_id) if business_id else None
    tz = business_timezone(business)

    if dt.tzinfo is not None:
        return dt.astimezone(tz).replace(tzinfo=None).replace(microsecond=0)
    return dt.replace(microsecond=0)
