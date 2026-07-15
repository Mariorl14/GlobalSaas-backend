"""Inventory stock movements + sale analytics."""

from __future__ import annotations

import uuid

import pytest
from flask_jwt_extended import create_access_token

from app.extensions import db
from app.inventory_movements import InventoryMovementError, apply_stock_movement
from app.models import InventoryProduct, User
from app.models.inventory_movement import InventoryMovement
from app.shop_insights import build_insights
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


def _make_product(business_id, *, stock=10, price=100, unit_cost=40, name="Cera"):
    p = InventoryProduct(
        business_id=business_id,
        name=name,
        category="Styling",
        price=price,
        unit_cost=unit_cost,
        stock=stock,
        min_stock=2,
        is_active=True,
    )
    db.session.add(p)
    db.session.commit()
    return p


@pytest.fixture()
def shop(app):
    with app.app_context():
        bundle = create_tenant_bundle(slug=f"inv-{uuid.uuid4().hex[:8]}")
        admin = User(
            business_id=bundle["business"].id,
            email=f"admin-{uuid.uuid4().hex[:6]}@test.com",
            encrypted_password="x",
            role="admin",
            is_active=True,
        )
        db.session.add(admin)
        db.session.commit()
        bundle["admin"] = admin
        yield bundle


class TestInventoryMovements:
    def test_add_stock_increases_quantity(self, app, shop):
        with app.app_context():
            p = _make_product(shop["business"].id, stock=5)
            m, product, replayed = apply_stock_movement(
                business_id=shop["business"].id,
                product_id=p.id,
                movement_type="restock",
                quantity=3,
                created_by_user_id=shop["admin"].id,
            )
            db.session.commit()
            assert replayed is False
            assert product.stock == 8
            assert m.quantity_before == 5
            assert m.quantity_after == 8
            assert m.total_revenue is None

    def test_reduce_stock_decreases_quantity(self, app, shop):
        with app.app_context():
            p = _make_product(shop["business"].id, stock=5)
            apply_stock_movement(
                business_id=shop["business"].id,
                product_id=p.id,
                movement_type="damaged",
                quantity=2,
            )
            db.session.commit()
            assert InventoryProduct.query.get(p.id).stock == 3

    def test_sale_decreases_and_stores_revenue_snapshot(self, app, shop):
        with app.app_context():
            p = _make_product(shop["business"].id, stock=10, price=100, unit_cost=40)
            m, product, _ = apply_stock_movement(
                business_id=shop["business"].id,
                product_id=p.id,
                movement_type="sale",
                quantity=2,
                unit_sale_price=95,
            )
            db.session.commit()
            assert product.stock == 8
            assert float(m.unit_sale_price) == 95
            assert float(m.total_revenue) == 190
            assert float(m.total_cost) == 80
            assert float(m.unit_cost) == 40

    def test_damaged_does_not_count_as_sales(self, app, shop):
        with app.app_context():
            p = _make_product(shop["business"].id, stock=10, price=100, unit_cost=40)
            apply_stock_movement(
                business_id=shop["business"].id,
                product_id=p.id,
                movement_type="damaged",
                quantity=2,
            )
            db.session.commit()
            insights = build_insights(shop["business"].id, range_key="month")
            assert insights["inventory"]["products_sold"] == 0
            assert insights["inventory"]["product_revenue"] == 0
            assert InventoryProduct.query.get(p.id).stock == 8

    def test_lost_and_internal_use_not_sales(self, app, shop):
        with app.app_context():
            p = _make_product(shop["business"].id, stock=10)
            apply_stock_movement(
                business_id=shop["business"].id,
                product_id=p.id,
                movement_type="lost",
                quantity=1,
            )
            apply_stock_movement(
                business_id=shop["business"].id,
                product_id=p.id,
                movement_type="internal_use",
                quantity=1,
            )
            db.session.commit()
            insights = build_insights(shop["business"].id, range_key="month")
            assert insights["snapshot"]["products_sold"] == 0
            assert InventoryProduct.query.get(p.id).stock == 8

    def test_sale_updates_insights_revenue(self, app, shop):
        with app.app_context():
            p = _make_product(shop["business"].id, stock=20, price=100, unit_cost=40)
            apply_stock_movement(
                business_id=shop["business"].id,
                product_id=p.id,
                movement_type="sale",
                quantity=3,
            )
            db.session.commit()
            insights = build_insights(shop["business"].id, range_key="month")
            assert insights["snapshot"]["products_sold"] == 3
            assert insights["inventory"]["product_revenue"] == 300
            assert insights["inventory"]["product_cogs"] == 120
            assert insights["inventory"]["product_gross_profit"] == 180
            assert insights["inventory"]["best_selling_product"]["name"] == "Cera"
            products_row = next(
                r for r in insights["revenue_breakdown"] if r["key"] == "products"
            )
            assert products_row["available"] is True
            assert products_row["amount"] == 300

    def test_insufficient_stock_rejects(self, app, shop):
        with app.app_context():
            p = _make_product(shop["business"].id, stock=2)
            with pytest.raises(InventoryMovementError) as exc:
                apply_stock_movement(
                    business_id=shop["business"].id,
                    product_id=p.id,
                    movement_type="sale",
                    quantity=5,
                )
            assert exc.value.status_code == 400
            assert InventoryProduct.query.get(p.id).stock == 2
            assert InventoryMovement.query.count() == 0

    def test_idempotency_prevents_double_sale(self, app, shop):
        with app.app_context():
            p = _make_product(shop["business"].id, stock=10, price=50, unit_cost=20)
            key = "sale-once-abc"
            m1, _, r1 = apply_stock_movement(
                business_id=shop["business"].id,
                product_id=p.id,
                movement_type="sale",
                quantity=2,
                idempotency_key=key,
            )
            db.session.commit()
            m2, product, r2 = apply_stock_movement(
                business_id=shop["business"].id,
                product_id=p.id,
                movement_type="sale",
                quantity=2,
                idempotency_key=key,
            )
            db.session.commit()
            assert r1 is False
            assert r2 is True
            assert m1.id == m2.id
            assert product.stock == 8
            assert InventoryMovement.query.filter_by(movement_type="sale").count() == 1

    def test_correction_creates_audit(self, app, shop, client):
        with app.app_context():
            p = _make_product(shop["business"].id, stock=5)
            headers = _auth_header(shop["admin"], shop["business"].id)
            res = client.put(
                f"/api/shop/inventory/{p.id}",
                json={"stock": 9},
                headers=headers,
            )
            assert res.status_code == 200
            assert res.get_json()["stock"] == 9
            mov = InventoryMovement.query.filter_by(product_id=p.id).one()
            assert mov.movement_type == "correction_increase"
            assert mov.quantity == 4

    def test_tenant_isolation(self, app, shop):
        with app.app_context():
            other = create_tenant_bundle(slug=f"other-{uuid.uuid4().hex[:8]}")
            p = _make_product(shop["business"].id, stock=5)
            with pytest.raises(InventoryMovementError) as exc:
                apply_stock_movement(
                    business_id=other["business"].id,
                    product_id=p.id,
                    movement_type="sale",
                    quantity=1,
                )
            assert exc.value.status_code == 404

    def test_inventory_value_formulas(self, app, shop):
        with app.app_context():
            _make_product(
                shop["business"].id, stock=10, price=100, unit_cost=40, name="A"
            )
            _make_product(
                shop["business"].id, stock=5, price=200, unit_cost=50, name="B"
            )
            insights = build_insights(shop["business"].id, range_key="today")
            # cost = 10*40 + 5*50 = 650; retail = 10*100 + 5*200 = 2000
            assert insights["inventory"]["inventory_cost"] == 650
            assert insights["inventory"]["potential_revenue"] == 2000
            assert insights["inventory"]["projected_gross_profit"] == 1350

    def test_api_sale_endpoint(self, app, shop, client):
        with app.app_context():
            p = _make_product(shop["business"].id, stock=10, price=80, unit_cost=30)
            headers = _auth_header(shop["admin"], shop["business"].id)
            res = client.post(
                f"/api/shop/inventory/{p.id}/sale",
                json={"quantity": 2, "idempotency_key": "api-sale-1"},
                headers=headers,
            )
            assert res.status_code == 201
            body = res.get_json()
            assert body["product"]["stock"] == 8
            assert body["movement"]["total_revenue"] == 160
            # replay
            res2 = client.post(
                f"/api/shop/inventory/{p.id}/sale",
                json={"quantity": 2, "idempotency_key": "api-sale-1"},
                headers=headers,
            )
            assert res2.status_code == 200
            assert res2.get_json()["replayed"] is True
            assert InventoryProduct.query.get(p.id).stock == 8
