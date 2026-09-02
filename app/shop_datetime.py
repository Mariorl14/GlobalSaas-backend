"""Shop-local naive datetimes (America/Costa_Rica by default)."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from app.models import Business
from app.shop_insights import business_timezone, local_now


def shop_local_now(business: Business | None) -> datetime:
    """Naive wall-clock now in the shop timezone (same convention as appointment times)."""
    try:
        return local_now(business_timezone(business))
    except Exception:
        return datetime.now().replace(microsecond=0)


def shop_local_today(business: Business | None) -> date:
    return shop_local_now(business).date()


def format_shop_naive_iso(dt: datetime | None, business: Business | None = None) -> str | None:
    """Serialize appointment times as naive shop-local `YYYY-MM-DDTHH:mm:ss` (no offset)."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        try:
            tz = business_timezone(business)
            dt = dt.astimezone(tz).replace(tzinfo=None)
        except Exception:
            dt = dt.replace(tzinfo=None)
    dt = dt.replace(microsecond=0)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


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

    if dt.tzinfo is None:
        return dt.replace(microsecond=0)

    business = Business.query.get(business_id) if business_id else None
    try:
        tz = business_timezone(business)
        return dt.astimezone(tz).replace(tzinfo=None).replace(microsecond=0)
    except Exception:
        return dt.replace(tzinfo=None).replace(microsecond=0)
