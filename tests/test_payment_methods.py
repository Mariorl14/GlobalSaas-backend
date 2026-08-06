"""Payment method required on service completion and product sales."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from flask_jwt_extended import create_access_token

from app.extensions import db
from app.models import Appointment, InventoryProduct, User
from app.models.sale import Sale
from app.shop_insights import build_insights
from app.shop_sales import SaleError, appointment_sale_idempotency_key, create_sale
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


def _admin_and_appt(slug_prefix: str):
    bundle = create_tenant_bundle(slug=f"{slug_prefix}-{uuid.uuid4().hex[:8]}")
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
    return bundle, admin, appt


def test_complete_appointment_requires_payment_method(app, client):
    with app.app_context():
        bundle, admin, appt = _admin_and_appt("pay-req")
        headers = _auth_header(admin, bundle["business"].id)

        res = client.put(
            f"/api/shop/appointments/{appt.id}",
            json={"status": "completed"},
            headers=headers,
        )
        assert res.status_code == 400
        assert "método de pago" in res.get_json()["error"].lower()
        assert Sale.query.filter_by(business_id=bundle["business"].id).count() == 0


def test_complete_appointment_rejects_invalid_payment_method(app, client):
    with app.app_context():
        bundle, admin, appt = _admin_and_appt("pay-bad")
        headers = _auth_header(admin, bundle["business"].id)

        res = client.put(
            f"/api/shop/appointments/{appt.id}",
            json={"status": "completed", "payment_method": "bitcoin"},
            headers=headers,
        )
        assert res.status_code == 400
        assert "inválido" in res.get_json()["error"].lower()


def test_complete_appointment_with_each_payment_method(app, client):
    with app.app_context():
        for method in ("cash", "sinpe", "card"):
            bundle, admin, appt = _admin_and_appt(f"pay-{method}")
            headers = _auth_header(admin, bundle["business"].id)

            res = client.put(
                f"/api/shop/appointments/{appt.id}",
                json={"status": "completed", "payment_method": method},
                headers=headers,
            )
            assert res.status_code == 200, method
            key = appointment_sale_idempotency_key(appt.id)
            sale = Sale.query.filter_by(
                business_id=bundle["business"].id, idempotency_key=key
            ).one()
            assert sale.payment_method == method

            # Re-complete does not duplicate and does not require a new method
            res2 = client.put(
                f"/api/shop/appointments/{appt.id}",
                json={"status": "completed", "payment_method": "card"},
                headers=headers,
            )
            assert res2.status_code == 200
            assert (
                Sale.query.filter_by(
                    business_id=bundle["business"].id, idempotency_key=key
                ).count()
                == 1
            )
            assert Sale.query.filter_by(idempotency_key=key).one().payment_method == method


def test_product_sale_each_payment_method(app, client):
    with app.app_context():
        for method in ("cash", "sinpe", "card"):
            bundle = create_tenant_bundle(slug=f"psale-{method}-{uuid.uuid4().hex[:6]}")
            admin = User(
                business_id=bundle["business"].id,
                email=f"a-{uuid.uuid4().hex[:6]}@test.com",
                encrypted_password="x",
                role="admin",
                is_active=True,
            )
            product = InventoryProduct(
                business_id=bundle["business"].id,
                name=f"Prod {method}",
                item_kind="RETAIL_PRODUCT",
                price=2500,
                unit_cost=1000,
                stock=10,
                min_stock=1,
                is_active=True,
            )
            db.session.add_all([admin, product])
            db.session.commit()
            headers = _auth_header(admin, bundle["business"].id)

            res = client.post(
                f"/api/shop/inventory/{product.id}/sale",
                json={"quantity": 1, "payment_method": method},
                headers=headers,
            )
            assert res.status_code == 201, method
            body = res.get_json()
            assert body["sale"]["payment_method"] == method
            assert InventoryProduct.query.get(product.id).stock == 9

            # Same idempotency key → no second stock reduction
            res2 = client.post(
                f"/api/shop/inventory/{product.id}/sale",
                json={
                    "quantity": 1,
                    "payment_method": method,
                    "idempotency_key": f"idem-{method}-{product.id}",
                },
                headers=headers,
            )
            # First call had no key — send with key for first time then replay
            assert res2.status_code in (200, 201)
            stock_after = InventoryProduct.query.get(product.id).stock
            res3 = client.post(
                f"/api/shop/inventory/{product.id}/sale",
                json={
                    "quantity": 1,
                    "payment_method": method,
                    "idempotency_key": f"idem-{method}-{product.id}",
                },
                headers=headers,
            )
            assert res3.status_code == 200
            assert res3.get_json().get("replayed") is True
            assert InventoryProduct.query.get(product.id).stock == stock_after


def test_product_sale_missing_payment_method(app, client):
    with app.app_context():
        bundle = create_tenant_bundle(slug=f"psale-miss-{uuid.uuid4().hex[:6]}")
        admin = User(
            business_id=bundle["business"].id,
            email=f"a-{uuid.uuid4().hex[:6]}@test.com",
            encrypted_password="x",
            role="admin",
            is_active=True,
        )
        product = InventoryProduct(
            business_id=bundle["business"].id,
            name="Gel",
            item_kind="RETAIL_PRODUCT",
            price=1000,
            stock=5,
            min_stock=1,
            is_active=True,
        )
        db.session.add_all([admin, product])
        db.session.commit()
        headers = _auth_header(admin, bundle["business"].id)

        res = client.post(
            f"/api/shop/inventory/{product.id}/sale",
            json={"quantity": 1},
            headers=headers,
        )
        assert res.status_code == 400
        assert InventoryProduct.query.get(product.id).stock == 5


def test_create_sale_rejects_legacy_methods(app):
    with app.app_context():
        bundle = create_tenant_bundle(slug=f"legacy-{uuid.uuid4().hex[:6]}")
        try:
            create_sale(
                business_id=bundle["business"].id,
                created_by_user_id=None,
                payment_method="other",
                items=[
                    {
                        "item_type": "service",
                        "service_type_id": str(bundle["service"].id),
                        "quantity": 1,
                    }
                ],
            )
            assert False, "expected SaleError"
        except SaleError as exc:
            assert exc.status_code == 400


def test_insights_payment_methods_breakdown(app, client):
    with app.app_context():
        bundle = create_tenant_bundle(slug=f"ins-pay-{uuid.uuid4().hex[:6]}")
        admin = User(
            business_id=bundle["business"].id,
            email=f"a-{uuid.uuid4().hex[:6]}@test.com",
            encrypted_password="x",
            role="admin",
            is_active=True,
        )
        db.session.add(admin)
        db.session.commit()

        create_sale(
            business_id=bundle["business"].id,
            created_by_user_id=admin.id,
            payment_method="cash",
            items=[
                {
                    "item_type": "service",
                    "service_type_id": str(bundle["service"].id),
                    "quantity": 1,
                }
            ],
        )
        create_sale(
            business_id=bundle["business"].id,
            created_by_user_id=admin.id,
            payment_method="sinpe",
            items=[
                {
                    "item_type": "service",
                    "service_type_id": str(bundle["service"].id),
                    "quantity": 1,
                }
            ],
        )
        # Historical unclassified
        sale = Sale(
            business_id=bundle["business"].id,
            invoice_number="INV-LEGACY",
            invoice_seq=99,
            subtotal=100,
            discount=0,
            tax=0,
            total=100,
            payment_method=None,
            status="completed",
        )
        db.session.add(sale)
        db.session.commit()

        insights = build_insights(bundle["business"].id, range_key="month")
        pm = insights["payment_methods"]
        price = float(bundle["service"].price)
        assert pm["cash"]["revenue"] == price
        assert pm["cash"]["count"] == 1
        assert pm["sinpe"]["revenue"] == price
        assert pm["sinpe"]["count"] == 1
        assert pm["card"]["count"] == 0
        assert pm["unclassified"]["revenue"] == 100
        assert pm["unclassified"]["count"] == 1


def test_tenant_isolation_sales_list(app, client):
    with app.app_context():
        a = create_tenant_bundle(slug=f"iso-a-{uuid.uuid4().hex[:6]}")
        b = create_tenant_bundle(slug=f"iso-b-{uuid.uuid4().hex[:6]}")
        admin_a = User(
            business_id=a["business"].id,
            email=f"a-{uuid.uuid4().hex[:6]}@test.com",
            encrypted_password="x",
            role="admin",
            is_active=True,
        )
        admin_b = User(
            business_id=b["business"].id,
            email=f"b-{uuid.uuid4().hex[:6]}@test.com",
            encrypted_password="x",
            role="admin",
            is_active=True,
        )
        db.session.add_all([admin_a, admin_b])
        db.session.commit()

        create_sale(
            business_id=a["business"].id,
            created_by_user_id=admin_a.id,
            payment_method="card",
            items=[
                {
                    "item_type": "service",
                    "service_type_id": str(a["service"].id),
                    "quantity": 1,
                }
            ],
        )
        db.session.commit()

        headers_b = _auth_header(admin_b, b["business"].id)
        res = client.get("/api/shop/sales", headers=headers_b)
        assert res.status_code == 200
        assert res.get_json()["items"] == []


def test_void_preserves_payment_method(app, client):
    with app.app_context():
        bundle = create_tenant_bundle(slug=f"void-pay-{uuid.uuid4().hex[:6]}")
        admin = User(
            business_id=bundle["business"].id,
            email=f"a-{uuid.uuid4().hex[:6]}@test.com",
            encrypted_password="x",
            role="admin",
            is_active=True,
        )
        db.session.add(admin)
        db.session.commit()
        sale, _ = create_sale(
            business_id=bundle["business"].id,
            created_by_user_id=admin.id,
            payment_method="sinpe",
            items=[
                {
                    "item_type": "service",
                    "service_type_id": str(bundle["service"].id),
                    "quantity": 1,
                }
            ],
        )
        db.session.commit()
        headers = _auth_header(admin, bundle["business"].id)

        res = client.post(f"/api/shop/sales/{sale.id}/void", headers=headers)
        assert res.status_code == 200
        assert res.get_json()["payment_method"] == "sinpe"
        assert res.get_json()["status"] == "void"

        insights = build_insights(bundle["business"].id, range_key="month")
        assert insights["payment_methods"]["sinpe"]["count"] == 0
        assert insights["payment_methods"]["sinpe"]["revenue"] == 0
