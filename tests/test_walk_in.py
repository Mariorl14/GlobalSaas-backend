"""Walk-in endpoint completes a sale via existing appointment completion logic."""

from __future__ import annotations

import uuid
from datetime import datetime

from flask_jwt_extended import create_access_token
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import Client, User
from app.models.sale import Sale
from app.shop_insights import build_insights
from app.shop_sales import appointment_sale_idempotency_key
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


def test_walk_in_creates_completed_sale_and_reuses_customer(app, client):
    with app.app_context():
        bundle = create_tenant_bundle(slug=f"walkin-{uuid.uuid4().hex[:8]}")
        admin = User(
            business_id=bundle["business"].id,
            email=f"admin-{uuid.uuid4().hex[:6]}@test.com",
            encrypted_password=generate_password_hash("x"),
            role="admin",
            is_active=True,
        )
        db.session.add(admin)
        db.session.commit()
        headers = _auth_header(admin, bundle["business"].id)
        start = datetime.utcnow().replace(second=0, microsecond=0)

        res = client.post(
            "/api/shop/appointments/walk-in",
            json={
                "name": "Carlos",
                "phone": "8888-8888",
                "service_type_id": str(bundle["service"].id),
                "employee_id": str(bundle["employee"].id),
                "payment_method": "sinpe",
                "start_time": start.isoformat(),
            },
            headers=headers,
        )
        assert res.status_code == 201, res.get_data(as_text=True)
        body = res.get_json()
        assert body["status"] == "completed"
        assert body["source"] == "walk_in"
        assert body["client_name"].startswith("Carlos")
        assert start.strftime("%Y-%m-%dT%H:%M") in (body["start_time"] or "")

        appt_id = body["id"]
        key = appointment_sale_idempotency_key(uuid.UUID(appt_id))
        sales = Sale.query.filter_by(
            business_id=bundle["business"].id, idempotency_key=key
        ).all()
        assert len(sales) == 1
        assert sales[0].payment_method == "sinpe"
        assert float(sales[0].total) == float(bundle["service"].price)

        insights = build_insights(bundle["business"].id, range_key="month")
        assert insights["snapshot"]["service_revenue"] == float(bundle["service"].price)
        assert insights["snapshot"]["revenue"] == float(bundle["service"].price)

        # Returning customer with same phone is reused.
        res2 = client.post(
            "/api/shop/appointments/walk-in",
            json={
                "name": "Carlos Pérez",
                "phone": "88888888",
                "email": "carlos@test.com",
                "service_type_id": str(bundle["service"].id),
                "employee_id": str(bundle["employee"].id),
                "payment_method": "cash",
                "start_time": "2026-08-12T16:10:00",
            },
            headers=headers,
        )
        assert res2.status_code == 201, res2.get_data(as_text=True)
        body2 = res2.get_json()
        assert body2["client_id"] == body["client_id"]
        row = Client.query.get(uuid.UUID(body["client_id"]))
        assert row.email == "carlos@test.com"
        assert "Pérez" in (row.last_name or "")
