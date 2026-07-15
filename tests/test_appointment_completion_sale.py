"""Completing an appointment registers a POS sale exactly once."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from flask_jwt_extended import create_access_token

from app.extensions import db
from app.models import Appointment, User
from app.models.sale import Sale
from app.shop_insights import build_insights
from app.shop_sales import appointment_sale_idempotency_key
from tests.conftest import create_tenant_bundle


def _auth_header(user: User, business_id) -> dict:
    token = create_access_token(
        identity=str(user.id),
        additional_claims={
            "role": user.role,
            "business_id": str(business_id),
            "employee_id": None,
        },
    )
    return {"Authorization": f"Bearer {token}"}


def test_complete_appointment_creates_sale_once(app, client):
    with app.app_context():
        bundle = create_tenant_bundle(slug=f"appt-sale-{uuid.uuid4().hex[:8]}")
        admin = User(
            business_id=bundle["business"].id,
            email=f"admin-{uuid.uuid4().hex[:6]}@test.com",
            encrypted_password="x",
            role="admin",
            is_active=True,
        )
        start = datetime.utcnow().replace(second=0, microsecond=0)
        appt = Appointment(
            client_id=bundle["client"].id,
            service_type_id=bundle["service"].id,
            business_id=bundle["business"].id,
            employee_id=bundle["employee"].id,
            client_name="María López",
            client_email="maria@test.com",
            client_phone=bundle["client"].phone,
            start_time=start,
            end_time=start + timedelta(minutes=30),
            status="confirmed",
        )
        db.session.add_all([admin, appt])
        db.session.commit()
        appt_id = appt.id
        headers = _auth_header(admin, bundle["business"].id)

        res = client.put(
            f"/api/shop/appointments/{appt_id}",
            json={"status": "completed"},
            headers=headers,
        )
        assert res.status_code == 200
        assert res.get_json()["status"] == "completed"

        key = appointment_sale_idempotency_key(appt_id)
        sales = Sale.query.filter_by(
            business_id=bundle["business"].id, idempotency_key=key
        ).all()
        assert len(sales) == 1
        assert float(sales[0].total) == float(bundle["service"].price)

        insights = build_insights(bundle["business"].id, range_key="month")
        assert insights["snapshot"]["pos_service_revenue"] == float(bundle["service"].price)
        # Not double-counted: combined service revenue equals one service price
        assert insights["snapshot"]["service_revenue"] == float(bundle["service"].price)
        assert insights["snapshot"]["revenue"] == float(bundle["service"].price)
        assert insights["snapshot"]["services_sold"] == 1

        # Mark completed again — still a single sale
        res2 = client.put(
            f"/api/shop/appointments/{appt_id}",
            json={"status": "completed"},
            headers=headers,
        )
        assert res2.status_code == 200
        sales2 = Sale.query.filter_by(
            business_id=bundle["business"].id, idempotency_key=key
        ).all()
        assert len(sales2) == 1

        insights2 = build_insights(bundle["business"].id, range_key="month")
        assert insights2["snapshot"]["service_revenue"] == float(bundle["service"].price)
        assert insights2["snapshot"]["services_sold"] == 1


def test_customers_served_counts_completion_sale_on_today(app, client):
    """Completing an appointment whose start_time is outside 'today' still counts the client."""
    with app.app_context():
        bundle = create_tenant_bundle(slug=f"served-{uuid.uuid4().hex[:8]}")
        admin = User(
            business_id=bundle["business"].id,
            email=f"admin-{uuid.uuid4().hex[:6]}@test.com",
            encrypted_password="x",
            role="admin",
            is_active=True,
        )
        # Scheduled yesterday — completing today creates a sale in the "today" window
        start = datetime.utcnow().replace(second=0, microsecond=0) - timedelta(days=1)
        appt = Appointment(
            client_id=bundle["client"].id,
            service_type_id=bundle["service"].id,
            business_id=bundle["business"].id,
            employee_id=bundle["employee"].id,
            client_name="María López",
            client_email="maria@test.com",
            client_phone=bundle["client"].phone,
            start_time=start,
            end_time=start + timedelta(minutes=30),
            status="confirmed",
        )
        db.session.add_all([admin, appt])
        db.session.commit()
        headers = _auth_header(admin, bundle["business"].id)

        before = build_insights(bundle["business"].id, range_key="today")
        assert before["snapshot"]["customers_served"] == 0

        res = client.put(
            f"/api/shop/appointments/{appt.id}",
            json={"status": "completed"},
            headers=headers,
        )
        assert res.status_code == 200

        after = build_insights(bundle["business"].id, range_key="today")
        assert after["snapshot"]["customers_served"] == 1
        assert after["snapshot"]["pos_service_revenue"] == float(bundle["service"].price)
