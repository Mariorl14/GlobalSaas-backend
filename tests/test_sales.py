"""POS sales create inventory movements and feed insights."""

from __future__ import annotations

import uuid

from flask_jwt_extended import create_access_token

from app.extensions import db
from app.models import InventoryProduct, ServiceType, User
from app.models.sale import Sale
from app.shop_insights import build_insights
from app.shop_sales import create_sale
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


def test_create_sale_reduces_stock_and_counts_revenue(app, client):
    with app.app_context():
        bundle = create_tenant_bundle(slug=f"sale-{uuid.uuid4().hex[:8]}")
        admin = User(
            business_id=bundle["business"].id,
            email=f"admin-{uuid.uuid4().hex[:6]}@test.com",
            encrypted_password="x",
            role="admin",
            is_active=True,
        )
        product = InventoryProduct(
            business_id=bundle["business"].id,
            name="Pomada",
            price=5000,
            unit_cost=2000,
            stock=10,
            min_stock=2,
            is_active=True,
        )
        db.session.add_all([admin, product])
        db.session.commit()

        sale, replayed = create_sale(
            business_id=bundle["business"].id,
            created_by_user_id=admin.id,
            client_id=bundle["client"].id,
            employee_id=bundle["employee"].id,
            payment_method="sinpe",
            discount=500,
            items=[
                {
                    "item_type": "service",
                    "service_type_id": str(bundle["service"].id),
                    "quantity": 1,
                },
                {
                    "item_type": "product",
                    "product_id": str(product.id),
                    "quantity": 2,
                    "unit_price": 5000,
                },
            ],
            idempotency_key="sale-key-1",
        )
        db.session.commit()
        assert replayed is False
        assert sale.invoice_number.startswith("INV-")
        # service 5000 + products 10000 - discount 500 = 14500
        assert float(sale.total) == 14500
        assert InventoryProduct.query.get(product.id).stock == 8

        insights = build_insights(bundle["business"].id, range_key="month")
        assert insights["snapshot"]["products_sold"] == 2
        assert insights["inventory"]["product_revenue"] == 10000
        assert insights["snapshot"]["pos_service_revenue"] == 5000
        assert insights["snapshot"]["discount_total"] == 500
        # Net: services 5000 + products 10000 − discount 500
        assert insights["snapshot"]["revenue"] == 14500
        assert insights["snapshot"]["average_ticket"] == 14500
        assert insights["goals_progress"]["monthly_revenue"]["current"] == 14500

        # idempotency
        sale2, replayed2 = create_sale(
            business_id=bundle["business"].id,
            created_by_user_id=admin.id,
            items=[
                {
                    "item_type": "product",
                    "product_id": str(product.id),
                    "quantity": 2,
                }
            ],
            idempotency_key="sale-key-1",
        )
        db.session.commit()
        assert replayed2 is True
        assert sale2.id == sale.id
        assert InventoryProduct.query.get(product.id).stock == 8

        headers = _auth_header(admin, bundle["business"].id)
        res = client.get("/api/shop/sales", headers=headers)
        assert res.status_code == 200
        assert len(res.get_json()["items"]) >= 1

        cres = client.get(
            f"/api/shop/clients/{bundle['client'].id}/sales", headers=headers
        )
        assert cres.status_code == 200
        assert len(cres.get_json()["items"]) >= 1


def test_inventory_sale_appears_in_ventas(app, client):
    with app.app_context():
        bundle = create_tenant_bundle(slug=f"invsale-{uuid.uuid4().hex[:8]}")
        admin = User(
            business_id=bundle["business"].id,
            email=f"admin-{uuid.uuid4().hex[:6]}@test.com",
            encrypted_password="x",
            role="admin",
            is_active=True,
        )
        product = InventoryProduct(
            business_id=bundle["business"].id,
            name="Cera Test",
            price=3000,
            unit_cost=1000,
            stock=20,
            min_stock=2,
            is_active=True,
        )
        db.session.add_all([admin, product])
        db.session.commit()
        headers = _auth_header(admin, bundle["business"].id)

        res = client.post(
            f"/api/shop/inventory/{product.id}/sale",
            json={
                "quantity": 2,
                "unit_sale_price": 3000,
                "idempotency_key": "inv-sale-1",
            },
            headers=headers,
        )
        assert res.status_code == 201
        invoice = res.get_json()["sale"]["invoice_number"]
        assert invoice.startswith("INV-")

        listed = client.get("/api/shop/sales", headers=headers)
        assert listed.status_code == 200
        invoices = [s["invoice_number"] for s in listed.get_json()["items"]]
        assert invoice in invoices


def test_orphan_inventory_sale_backfills_into_ventas(app, client):
    with app.app_context():
        from app.inventory_movements import apply_stock_movement

        bundle = create_tenant_bundle(slug=f"orphan-{uuid.uuid4().hex[:8]}")
        admin = User(
            business_id=bundle["business"].id,
            email=f"admin-{uuid.uuid4().hex[:6]}@test.com",
            encrypted_password="x",
            role="admin",
            is_active=True,
        )
        product = InventoryProduct(
            business_id=bundle["business"].id,
            name="Wax",
            price=2000,
            unit_cost=800,
            stock=10,
            min_stock=1,
            is_active=True,
        )
        db.session.add_all([admin, product])
        db.session.commit()

        apply_stock_movement(
            business_id=bundle["business"].id,
            product_id=product.id,
            movement_type="sale",
            quantity=1,
            unit_sale_price=2000,
            created_by_user_id=admin.id,
        )
        db.session.commit()
        assert Sale.query.filter_by(business_id=bundle["business"].id).count() == 0

        headers = _auth_header(admin, bundle["business"].id)
        listed = client.get("/api/shop/sales", headers=headers)
        assert listed.status_code == 200
        assert len(listed.get_json()["items"]) == 1
        assert Sale.query.filter_by(business_id=bundle["business"].id).count() == 1

        listed2 = client.get("/api/shop/sales", headers=headers)
        assert len(listed2.get_json()["items"]) == 1
