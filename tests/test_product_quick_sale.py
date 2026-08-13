"""Quick product sale: stock, roles, tenant isolation, catalog price."""

from __future__ import annotations

import uuid
from decimal import Decimal

from flask_jwt_extended import create_access_token
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import Employee, InventoryProduct, User
from app.models.sale import Sale
from app.shop_insights import build_insights
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


def _product(business_id, *, name="Uppercut Pomade", stock=8, price=8500, unit_cost=4000, kind="RETAIL_PRODUCT"):
    p = InventoryProduct(
        business_id=business_id,
        name=name,
        item_kind=kind,
        price=price,
        unit_cost=unit_cost,
        stock=stock,
        min_stock=1,
        is_active=True,
    )
    db.session.add(p)
    db.session.commit()
    return p


def _owner(bundle) -> User:
    owner = User(
        business_id=bundle["business"].id,
        email=f"owner-{uuid.uuid4().hex[:6]}@test.com",
        encrypted_password=generate_password_hash("x"),
        role="owner",
        is_active=True,
    )
    db.session.add(owner)
    db.session.commit()
    return owner


def test_owner_registers_sale_uses_db_price_and_reduces_stock(app, client):
    with app.app_context():
        bundle = create_tenant_bundle(slug=f"qs-{uuid.uuid4().hex[:8]}")
        owner = _owner(bundle)
        product = _product(bundle["business"].id, stock=8, price=8500, unit_cost=4000)
        res = client.post(
            f"/api/shop/inventory/{product.id}/sale",
            json={"quantity": 2, "payment_method": "cash"},
            headers=_auth_header(owner, bundle["business"].id),
        )
        assert res.status_code == 201, res.get_data(as_text=True)
        body = res.get_json()
        assert body["product"]["stock"] == 6
        assert float(body["sale"]["total"]) == 17000
        assert float(body["movement"]["total_revenue"]) == 17000
        assert float(body["movement"]["total_cost"] or 0) == 8000
        sale = Sale.query.get(uuid.UUID(body["sale"]["id"]))
        assert sale.created_by_user_id == owner.id
        insights = build_insights(bundle["business"].id, range_key="today")
        assert insights["snapshot"]["product_revenue"] == 17000


def test_staff_can_register_sale_and_list_sellable(app, client):
    with app.app_context():
        bundle = create_tenant_bundle(slug=f"qs-st-{uuid.uuid4().hex[:8]}")
        staff = User(
            business_id=bundle["business"].id,
            email=f"staff-{uuid.uuid4().hex[:6]}@test.com",
            encrypted_password=generate_password_hash("x"),
            role="employee",
            is_active=True,
        )
        db.session.add(staff)
        db.session.flush()
        emp = Employee(
            user_id=staff.id,
            business_id=bundle["business"].id,
            display_name="Barber Staff",
            is_active=True,
        )
        db.session.add(emp)
        product = _product(bundle["business"].id, stock=5, price=1000)
        db.session.commit()
        headers = _auth_header(staff, bundle["business"].id, emp.id)

        denied = client.get("/api/shop/inventory", headers=headers)
        assert denied.status_code == 403

        listed = client.get("/api/shop/inventory?sellable=true", headers=headers)
        assert listed.status_code == 200
        ids = {row["id"] for row in listed.get_json()["items"]}
        assert str(product.id) in ids

        res = client.post(
            f"/api/shop/inventory/{product.id}/sale",
            json={"quantity": 1, "payment_method": "sinpe"},
            headers=headers,
        )
        assert res.status_code == 201, res.get_data(as_text=True)
        sale = Sale.query.get(uuid.UUID(res.get_json()["sale"]["id"]))
        assert sale.employee_id == emp.id
        assert sale.created_by_user_id == staff.id
        assert InventoryProduct.query.get(product.id).stock == 4


def test_other_business_cannot_sell_product(app, client):
    with app.app_context():
        a = create_tenant_bundle(slug=f"qs-a-{uuid.uuid4().hex[:8]}")
        b = create_tenant_bundle(slug=f"qs-b-{uuid.uuid4().hex[:8]}")
        product = _product(a["business"].id, stock=4)
        owner_b = _owner(b)
        res = client.post(
            f"/api/shop/inventory/{product.id}/sale",
            json={"quantity": 1, "payment_method": "card"},
            headers=_auth_header(owner_b, b["business"].id),
        )
        assert res.status_code in {400, 404}
        assert InventoryProduct.query.get(product.id).stock == 4


def test_quantity_validation_and_last_unit(app, client):
    with app.app_context():
        bundle = create_tenant_bundle(slug=f"qs-qty-{uuid.uuid4().hex[:8]}")
        owner = _owner(bundle)
        headers = _auth_header(owner, bundle["business"].id)
        product = _product(bundle["business"].id, stock=2, price=500)

        zero = client.post(
            f"/api/shop/inventory/{product.id}/sale",
            json={"quantity": 0, "payment_method": "cash"},
            headers=headers,
        )
        assert zero.status_code == 400

        neg = client.post(
            f"/api/shop/inventory/{product.id}/sale",
            json={"quantity": -1, "payment_method": "cash"},
            headers=headers,
        )
        assert neg.status_code == 400

        over = client.post(
            f"/api/shop/inventory/{product.id}/sale",
            json={"quantity": 3, "payment_method": "cash"},
            headers=headers,
        )
        assert over.status_code == 400
        assert "Solo hay 2 unidades disponibles" in (over.get_json() or {}).get("error", "")
        assert InventoryProduct.query.get(product.id).stock == 2

        last = client.post(
            f"/api/shop/inventory/{product.id}/sale",
            json={"quantity": 2, "payment_method": "cash"},
            headers=headers,
        )
        assert last.status_code == 201
        assert InventoryProduct.query.get(product.id).stock == 0

        empty = client.post(
            f"/api/shop/inventory/{product.id}/sale",
            json={"quantity": 1, "payment_method": "cash"},
            headers=headers,
        )
        assert empty.status_code == 400
        assert InventoryProduct.query.get(product.id).stock == 0


def test_supply_cannot_be_sold(app, client):
    with app.app_context():
        bundle = create_tenant_bundle(slug=f"qs-sup-{uuid.uuid4().hex[:8]}")
        owner = _owner(bundle)
        supply = _product(
            bundle["business"].id,
            name="Navajas",
            stock=20,
            price=None,
            kind="OPERATING_SUPPLY",
        )
        res = client.post(
            f"/api/shop/inventory/{supply.id}/sale",
            json={"quantity": 1, "payment_method": "cash"},
            headers=_auth_header(owner, bundle["business"].id),
        )
        assert res.status_code == 400
        assert InventoryProduct.query.get(supply.id).stock == 20


def test_frontend_price_override_is_optional_catalog_used(app, client):
    with app.app_context():
        bundle = create_tenant_bundle(slug=f"qs-price-{uuid.uuid4().hex[:8]}")
        owner = _owner(bundle)
        product = _product(bundle["business"].id, stock=3, price=8500)
        res = client.post(
            f"/api/shop/inventory/{product.id}/sale",
            json={"quantity": 1, "payment_method": "card"},
            headers=_auth_header(owner, bundle["business"].id),
        )
        assert res.status_code == 201
        item = Sale.query.get(uuid.UUID(res.get_json()["sale"]["id"])).items[0]
        assert item.unit_price == Decimal("8500")
        assert item.line_total == Decimal("8500")


def test_idempotent_quick_sale_does_not_double_deduct(app, client):
    with app.app_context():
        bundle = create_tenant_bundle(slug=f"qs-id-{uuid.uuid4().hex[:8]}")
        owner = _owner(bundle)
        product = _product(bundle["business"].id, stock=5, price=100)
        headers = _auth_header(owner, bundle["business"].id)
        payload = {
            "quantity": 1,
            "payment_method": "cash",
            "idempotency_key": "quick-sale-once",
        }
        r1 = client.post(f"/api/shop/inventory/{product.id}/sale", json=payload, headers=headers)
        r2 = client.post(f"/api/shop/inventory/{product.id}/sale", json=payload, headers=headers)
        assert r1.status_code == 201
        assert r2.status_code == 200
        assert r2.get_json()["replayed"] is True
        assert InventoryProduct.query.get(product.id).stock == 4
        assert Sale.query.filter_by(business_id=bundle["business"].id).count() == 1
