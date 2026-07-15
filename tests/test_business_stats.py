"""Super Admin business stats for Estadísticas y actividad."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from app.extensions import db
from app.models import Appointment, Client, InventoryProduct, User
from app.shop_sales import create_sale
from tests.conftest import create_tenant_bundle


def test_business_stats_endpoint(app, client):
    with app.app_context():
        bundle = create_tenant_bundle(slug=f"biz-stats-{uuid.uuid4().hex[:8]}")
        bid = bundle["business"].id

        # Extra client + appointment this month
        c2 = Client(
            business_id=bid,
            first_name="Ana",
            last_name="Ruiz",
            phone="+50688880001",
            appointments_amount=0,
        )
        db.session.add(c2)
        db.session.flush()
        start = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
        appt = Appointment(
            client_id=c2.id,
            service_type_id=bundle["service"].id,
            business_id=bid,
            employee_id=bundle["employee"].id,
            client_name="Ana Ruiz",
            client_email="ana@test.com",
            client_phone=c2.phone,
            start_time=start,
            end_time=start + timedelta(minutes=30),
            status="confirmed",
        )
        admin = User(
            business_id=bid,
            email=f"sa-{uuid.uuid4().hex[:6]}@test.com",
            encrypted_password="x",
            role="admin",
            is_active=True,
        )
        product = InventoryProduct(
            business_id=bid,
            name="Gel",
            price=1000,
            unit_cost=400,
            stock=20,
            min_stock=2,
            is_active=True,
        )
        db.session.add_all([appt, admin, product])
        db.session.commit()

        sale, _ = create_sale(
            business_id=bid,
            created_by_user_id=admin.id,
            client_id=bundle["client"].id,
            employee_id=bundle["employee"].id,
            items=[
                {
                    "item_type": "product",
                    "product_id": str(product.id),
                    "quantity": 2,
                    "unit_price": 1000,
                }
            ],
            idempotency_key=f"stats-sale-{uuid.uuid4().hex[:8]}",
        )
        db.session.commit()
        assert float(sale.total) == 2000

        res = client.get(f"/api/business/{bid}/stats")
        assert res.status_code == 200
        data = res.get_json()
        assert data["employees_count"] >= 1
        assert data["customers_count"] >= 2
        assert data["appointments_month"] >= 1
        assert data["monthly_revenue"] == 2000.0

        listed = client.get("/api/business?page=1&per_page=100")
        assert listed.status_code == 200
        row = next(b for b in listed.get_json()["items"] if b["id"] == str(bid))
        assert row["employees_count"] >= 1
        assert row["monthly_revenue"] == 2000.0
