"""Public booking slots must use shop-local time, not the server clock."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

from flask_jwt_extended import create_access_token
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import User
from tests.conftest import create_tenant_bundle

# Friday 28 Aug 2026, 15:00 in Costa Rica. On a UTC host that is 21:00 —
# the old datetime.now() / date.today() filter treated every remaining
# afternoon slot as already past and returned an empty list.
_SHOP_AFTERNOON = datetime(2026, 8, 28, 15, 0, 0)


def _admin_headers(bundle) -> dict:
    admin = User(
        business_id=bundle["business"].id,
        email=f"admin-{uuid.uuid4().hex[:6]}@test.com",
        encrypted_password=generate_password_hash("x"),
        role="admin",
        is_active=True,
    )
    db.session.add(admin)
    db.session.commit()
    token = create_access_token(
        identity=str(admin.id),
        additional_claims={
            "role": admin.role,
            "business_id": str(bundle["business"].id),
            "employee_id": None,
        },
    )
    return {"Authorization": f"Bearer {token}"}


def test_today_availability_keeps_remaining_shop_local_slots(client, app):
    with app.app_context():
        bundle = create_tenant_bundle(slug=f"slots-{uuid.uuid4().hex[:8]}", country_code="CR")
        slug = bundle["business"].public_slug
        service_id = str(bundle["service"].id)
        employee_id = str(bundle["employee"].id)

        with patch("app.public_booking.shop_local_now", return_value=_SHOP_AFTERNOON):
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

        with patch("app.public_booking.shop_local_now", return_value=_SHOP_AFTERNOON):
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


def test_past_dates_have_no_public_slots(client, app):
    with app.app_context():
        bundle = create_tenant_bundle(slug=f"past-{uuid.uuid4().hex[:8]}", country_code="CR")
        with patch("app.public_booking.shop_local_now", return_value=_SHOP_AFTERNOON):
            res = client.get(
                f"/api/public/booking/{bundle['business'].public_slug}/availability",
                query_string={
                    "date": "2026-08-27",
                    "service_id": str(bundle["service"].id),
                    "employee_id": str(bundle["employee"].id),
                },
            )
        assert res.status_code == 200, res.get_data(as_text=True)
        assert res.get_json()["slots"] == []


@patch("app.public_booking.notify_appointment_created", return_value={"status": "skipped"})
@patch("app.public_booking._slot_is_bookable", return_value=True)
def test_public_booking_shows_on_shop_calendar_day(_mock_slot, _mock_send, client, app):
    with app.app_context():
        bundle = create_tenant_bundle(slug=f"cal-{uuid.uuid4().hex[:8]}", country_code="CR")
        start = datetime.now().replace(second=0, microsecond=0, minute=0) + timedelta(days=2)
        end = start + timedelta(minutes=30)

        booked = client.post(
            f"/api/public/booking/{bundle['business'].public_slug}/bookings",
            json={
                "service_id": str(bundle["service"].id),
                "employee_id": str(bundle["employee"].id),
                "start_time": start.isoformat(),
                "end_time": end.isoformat(),
                "first_name": "Ana",
                "last_name": "Solís",
            },
        )
        assert booked.status_code == 201, booked.get_data(as_text=True)
        appointment_id = booked.get_json()["appointment_id"]

        listed = client.get(
            "/api/shop/appointments",
            query_string={
                "from": start.strftime("%Y-%m-%dT00:00:00"),
                "to": start.strftime("%Y-%m-%dT23:59:59"),
            },
            headers=_admin_headers(bundle),
        )
        assert listed.status_code == 200, listed.get_data(as_text=True)
        items = listed.get_json()["items"]
        match = next((i for i in items if i["id"] == appointment_id), None)
        assert match is not None, "public booking must appear on the shop calendar for that day"
        assert match["start_time"] == start.strftime("%Y-%m-%dT%H:%M:%S")
        assert "+" not in match["start_time"]
        assert match["status"] == "confirmed"
