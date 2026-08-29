"""Public booking slots must use shop-local time, not the server clock."""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import patch

from tests.conftest import create_tenant_bundle

# Friday 28 Aug 2026, 15:00 in Costa Rica. On a UTC host that is 21:00 —
# the old datetime.now() / date.today() filter treated every remaining
# afternoon slot as already past and returned an empty list.
_SHOP_AFTERNOON = datetime(2026, 8, 28, 15, 0, 0)


def test_today_availability_keeps_remaining_shop_local_slots(client, app):
    with app.app_context():
        bundle = create_tenant_bundle(slug=f"slots-{uuid.uuid4().hex[:8]}", country_code="CR")
        slug = bundle["business"].public_slug
        service_id = str(bundle["service"].id)
        employee_id = str(bundle["employee"].id)

        with patch("app.public_booking.shop_local_now", return_value=_SHOP_AFTERNOON), patch(
            "app.public_booking.shop_local_today", return_value=_SHOP_AFTERNOON.date()
        ):
            res = client.get(
                f"/api/public/booking/{slug}/availability",
                query_string={
                    "date": "2026-08-28",
                    "service_id": service_id,
                    "employee_id": employee_id,
                },
            )

        assert res.status_code == 200, res.get_data(as_text=True)
        starts = [s["start"] for s in res.get_json()["slots"]]
        assert starts, "remaining afternoon slots must still be bookable"
        assert all(s >= "2026-08-28T15:00:00" for s in starts)
        assert "2026-08-28T09:00:00" not in starts
        assert "2026-08-28T16:00:00" in starts


def test_calendar_hints_do_not_mark_shop_today_as_past(client, app):
    with app.app_context():
        bundle = create_tenant_bundle(slug=f"hints-{uuid.uuid4().hex[:8]}", country_code="CR")
        slug = bundle["business"].public_slug
        service_id = str(bundle["service"].id)
        employee_id = str(bundle["employee"].id)

        with patch("app.public_booking.shop_local_now", return_value=_SHOP_AFTERNOON), patch(
            "app.public_booking.shop_local_today", return_value=_SHOP_AFTERNOON.date()
        ):
            res = client.get(
                f"/api/public/booking/{slug}/calendar-hints",
                query_string={
                    "year": 2026,
                    "month": 8,
                    "service_id": service_id,
                    "employee_id": employee_id,
                },
            )

        assert res.status_code == 200, res.get_data(as_text=True)
        days = res.get_json()["days"]
        assert days["2026-08-28"] is True
        assert days["2026-08-27"] is False
