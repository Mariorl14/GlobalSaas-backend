"""Customers may omit phone and email on public booking and shop create."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

from flask_jwt_extended import create_access_token
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import Appointment, Client, User
from tests.conftest import create_tenant_bundle


def _auth_header(user: User, business_id, employee_id=None) -> dict:
    token = create_access_token(
        identity=str(user.id),
        additional_claims={
            "role": user.role,
            "business_id": str(business_id),
            "employee_id": str(employee_id) if employee_id else None,
        },
    )
    return {"Authorization": f"Bearer {token}"}


def _admin(bundle) -> tuple[User, dict]:
    admin = User(
        business_id=bundle["business"].id,
        email=f"admin-{uuid.uuid4().hex[:6]}@test.com",
        encrypted_password=generate_password_hash("x"),
        role="admin",
        is_active=True,
    )
    db.session.add(admin)
    db.session.commit()
    return admin, _auth_header(admin, bundle["business"].id)


@patch("app.public_booking.notify_appointment_created", return_value={"status": "skipped"})
@patch("app.public_booking._slot_is_bookable", return_value=True)
def test_public_booking_without_phone_or_email(_mock_slot, _mock_send, client, app):
    with app.app_context():
        bundle = create_tenant_bundle(slug=f"opt-{uuid.uuid4().hex[:8]}")
        start = datetime.now().replace(second=0, microsecond=0) + timedelta(days=2)
        start = start.replace(minute=(start.minute // 15) * 15)
        end = start + timedelta(minutes=30)

        res = client.post(
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
        assert res.status_code == 201, res.get_data(as_text=True)
        body = res.get_json()
        appt = Appointment.query.get(uuid.UUID(body["appointment_id"]))
        assert appt.client_phone is None
        assert appt.client_email == "—"
        row = Client.query.get(appt.client_id)
        assert row.phone is None
        assert row.email is None


@patch("app.public_booking.notify_appointment_created", return_value={"status": "skipped"})
@patch("app.public_booking._slot_is_bookable", return_value=True)
def test_public_booking_short_phone_still_rejected(_mock_slot, _mock_send, client, app):
    with app.app_context():
        bundle = create_tenant_bundle(slug=f"opt-short-{uuid.uuid4().hex[:8]}")
        start = datetime.now().replace(second=0, microsecond=0) + timedelta(days=2)
        start = start.replace(minute=(start.minute // 15) * 15)
        end = start + timedelta(minutes=30)

        res = client.post(
            f"/api/public/booking/{bundle['business'].public_slug}/bookings",
            json={
                "service_id": str(bundle["service"].id),
                "employee_id": str(bundle["employee"].id),
                "start_time": start.isoformat(),
                "end_time": end.isoformat(),
                "first_name": "Ana",
                "last_name": "Solís",
                "phone": "123",
            },
        )
        assert res.status_code == 400
        assert "corto" in (res.get_json() or {}).get("error", "").lower()


def test_shop_can_create_client_and_appointment_without_phone(client, app):
    with app.app_context():
        bundle = create_tenant_bundle(slug=f"opt-shop-{uuid.uuid4().hex[:8]}")
        _, headers = _admin(bundle)

        created = client.post(
            "/api/shop/clients",
            json={"first_name": "Luis", "last_name": "Mora"},
            headers=headers,
        )
        assert created.status_code == 201, created.get_data(as_text=True)
        body = created.get_json()
        assert body["phone"] is None
        assert body["email"] is None
        cid = body["id"]

        start = datetime(2026, 8, 20, 10, 0, 0)
        end = start + timedelta(minutes=30)
        appt = client.post(
            "/api/shop/appointments",
            json={
                "client_id": cid,
                "service_type_id": str(bundle["service"].id),
                "employee_id": str(bundle["employee"].id),
                "start_time": start.isoformat(),
                "end_time": end.isoformat(),
                "status": "scheduled",
            },
            headers=headers,
        )
        assert appt.status_code == 201, appt.get_data(as_text=True)
        payload = appt.get_json()
        assert payload["client_phone"] is None
        assert payload["client_email"] == "—"
