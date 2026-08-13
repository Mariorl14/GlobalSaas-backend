"""Owners/admins can delete any shop appointment; staff only their own."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from flask_jwt_extended import create_access_token
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import Appointment, Employee, User
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


def test_owner_can_delete_another_staff_appointment(app, client):
    with app.app_context():
        bundle = create_tenant_bundle(slug=f"del-{uuid.uuid4().hex[:8]}")
        owner = User(
            business_id=bundle["business"].id,
            email=f"owner-{uuid.uuid4().hex[:6]}@test.com",
            encrypted_password=generate_password_hash("x"),
            role="owner",
            is_active=True,
        )
        db.session.add(owner)
        db.session.flush()
        owner_emp = Employee(
            user_id=owner.id,
            business_id=bundle["business"].id,
            display_name="Owner Barber",
            is_active=True,
        )
        db.session.add(owner_emp)
        start = datetime.now().replace(second=0, microsecond=0) + timedelta(hours=2)
        appt = Appointment(
            client_id=bundle["client"].id,
            service_type_id=bundle["service"].id,
            business_id=bundle["business"].id,
            employee_id=bundle["employee"].id,
            client_name="María López",
            client_email="maria@test.com",
            client_phone="88887777",
            start_time=start,
            end_time=start + timedelta(minutes=30),
            status="scheduled",
        )
        db.session.add(appt)
        db.session.commit()
        appt_id = str(appt.id)
        headers = _auth_header(owner, bundle["business"].id, owner_emp.id)

        res = client.delete(f"/api/shop/appointments/{appt_id}", headers=headers)
        assert res.status_code == 204, res.get_data(as_text=True)
        assert Appointment.query.get(uuid.UUID(appt_id)) is None
