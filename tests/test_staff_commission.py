"""Per-staff service commission: defaults, permissions, snapshots, finance."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from flask_jwt_extended import create_access_token
from werkzeug.security import generate_password_hash

from app.commissions import default_commission_for_role
from app.extensions import db
from app.models import Appointment, Employee, InventoryProduct, User
from app.models.sale import Sale
from app.shop_insights import build_insights
from tests.conftest import create_tenant_bundle


def _auth(user: User, business_id, employee_id=None) -> dict:
    token = create_access_token(
        identity=str(user.id),
        additional_claims={
            "role": user.role,
            "business_id": str(business_id),
            "employee_id": str(employee_id) if employee_id else None,
        },
    )
    return {"Authorization": f"Bearer {token}"}


def _owner(bundle) -> tuple[User, Employee]:
    owner = User(
        business_id=bundle["business"].id,
        email=f"owner-{uuid.uuid4().hex[:6]}@test.com",
        encrypted_password=generate_password_hash("x"),
        role="owner",
        is_active=True,
    )
    db.session.add(owner)
    db.session.flush()
    emp = Employee(
        user_id=owner.id,
        business_id=bundle["business"].id,
        display_name="Owner",
        is_active=True,
        commission_percentage=default_commission_for_role("owner"),
    )
    db.session.add(emp)
    db.session.commit()
    return owner, emp


def test_default_commission_rates():
    assert default_commission_for_role("employee") == Decimal("50")
    assert default_commission_for_role("owner") == Decimal("0")
    assert default_commission_for_role("admin") == Decimal("0")


def test_existing_barber_defaults_to_50(app):
    with app.app_context():
        bundle = create_tenant_bundle(slug=f"comm-def-{uuid.uuid4().hex[:8]}")
        emp = bundle["employee"]
        assert Decimal(str(emp.commission_percentage)) == Decimal("50")


def test_owner_changes_commission_staff_cannot(app, client):
    with app.app_context():
        bundle = create_tenant_bundle(slug=f"comm-put-{uuid.uuid4().hex[:8]}")
        owner, owner_emp = _owner(bundle)
        barber = bundle["employee"]
        staff_user = User.query.get(barber.user_id)
        headers_owner = _auth(owner, bundle["business"].id, owner_emp.id)
        headers_staff = _auth(staff_user, bundle["business"].id, barber.id)

        listed = client.get("/api/shop/staff", headers=headers_staff)
        assert listed.status_code == 200
        row = next(i for i in listed.get_json()["items"] if i["employee_id"] == str(barber.id))
        assert float(row["commission_percentage"]) == 50

        denied = client.put(
            f"/api/shop/staff/{barber.id}",
            json={"commission_percentage": 80},
            headers=headers_staff,
        )
        assert denied.status_code == 403

        other = bundle["employee"]
        denied_other = client.put(
            f"/api/shop/staff/{other.id}",
            json={"commission_percentage": 90},
            headers=headers_staff,
        )
        assert denied_other.status_code == 403

        ok = client.put(
            f"/api/shop/staff/{barber.id}",
            json={"commission_percentage": 60},
            headers=headers_owner,
        )
        assert ok.status_code == 200, ok.get_data(as_text=True)
        assert float(ok.get_json()["commission_percentage"]) == 60
        assert Decimal(str(Employee.query.get(barber.id).commission_percentage)) == Decimal("60")

        bad = client.put(
            f"/api/shop/staff/{barber.id}",
            json={"commission_percentage": 140},
            headers=headers_owner,
        )
        assert bad.status_code == 400


def test_other_business_cannot_edit_commission(app, client):
    with app.app_context():
        a = create_tenant_bundle(slug=f"comm-a-{uuid.uuid4().hex[:8]}")
        b = create_tenant_bundle(slug=f"comm-b-{uuid.uuid4().hex[:8]}")
        owner_b, emp_b = _owner(b)
        res = client.put(
            f"/api/shop/staff/{a['employee'].id}",
            json={"commission_percentage": 70},
            headers=_auth(owner_b, b["business"].id, emp_b.id),
        )
        assert res.status_code == 404
        assert Decimal(str(Employee.query.get(a["employee"].id).commission_percentage)) == Decimal(
            "50"
        )


def _complete(client, headers, bundle, employee_id, payment="cash"):
    start = datetime.utcnow().replace(second=0, microsecond=0) + timedelta(hours=1)
    appt = Appointment(
        client_id=bundle["client"].id,
        service_type_id=bundle["service"].id,
        business_id=bundle["business"].id,
        employee_id=employee_id,
        client_name="María López",
        client_email="maria@test.com",
        client_phone=bundle["client"].phone,
        start_time=start,
        end_time=start + timedelta(minutes=30),
        status="confirmed",
    )
    db.session.add(appt)
    db.session.commit()
    res = client.put(
        f"/api/shop/appointments/{appt.id}",
        json={"status": "completed", "payment_method": payment},
        headers=headers,
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    return appt


def test_completed_service_snapshots_commission(app, client):
    with app.app_context():
        bundle = create_tenant_bundle(slug=f"comm-snap-{uuid.uuid4().hex[:8]}")
        owner, owner_emp = _owner(bundle)
        barber = bundle["employee"]
        barber.commission_percentage = Decimal("50")
        db.session.commit()
        headers = _auth(owner, bundle["business"].id, owner_emp.id)
        price = float(bundle["service"].price)  # 5000
        appt = _complete(client, headers, bundle, barber.id, payment="sinpe")

        sale = Sale.query.filter_by(
            business_id=bundle["business"].id,
            idempotency_key=f"appointment:{appt.id}",
        ).one()
        item = sale.items[0]
        assert float(item.line_total) == price
        assert float(item.commission_percentage) == 50
        assert float(item.staff_earnings) == 2500
        assert float(item.business_earnings) == 2500

        barber.commission_percentage = Decimal("60")
        db.session.commit()
        item2 = Sale.query.get(sale.id).items[0]
        assert float(item2.commission_percentage) == 50
        assert float(item2.staff_earnings) == 2500

        insights = build_insights(bundle["business"].id, range_key="month")
        assert insights["snapshot"]["service_revenue"] == price
        assert insights["snapshot"]["staff_commissions"] == 2500
        assert insights["snapshot"]["business_service_revenue"] == 2500
        assert insights["snapshot"]["revenue"] == price


def test_different_rates_and_owner_keeps_100(app, client):
    with app.app_context():
        bundle = create_tenant_bundle(slug=f"comm-mix-{uuid.uuid4().hex[:8]}")
        owner, owner_emp = _owner(bundle)
        barber = bundle["employee"]
        barber.commission_percentage = Decimal("70")
        db.session.commit()
        headers = _auth(owner, bundle["business"].id, owner_emp.id)
        price = float(bundle["service"].price)

        _complete(client, headers, bundle, barber.id, payment="card")
        owner_appt = _complete(client, headers, bundle, owner_emp.id, payment="cash")

        owner_sale = Sale.query.filter_by(
            idempotency_key=f"appointment:{owner_appt.id}"
        ).one()
        assert float(owner_sale.items[0].commission_percentage) == 0
        assert float(owner_sale.items[0].staff_earnings) == 0
        assert float(owner_sale.items[0].business_earnings) == price

        barber_item = next(
            i
            for s in Sale.query.filter_by(business_id=bundle["business"].id)
            for i in s.items
            if s.employee_id == barber.id
        )
        assert float(barber_item.staff_earnings) == round(price * 0.7, 2)


def test_cancel_and_double_complete_do_not_duplicate(app, client):
    with app.app_context():
        bundle = create_tenant_bundle(slug=f"comm-dup-{uuid.uuid4().hex[:8]}")
        owner, owner_emp = _owner(bundle)
        headers = _auth(owner, bundle["business"].id, owner_emp.id)
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
            status="scheduled",
        )
        db.session.add(appt)
        db.session.commit()
        client.post(
            f"/api/shop/appointments/{appt.id}/cancel",
            json={"send_email": False},
            headers=headers,
        )
        assert Sale.query.filter_by(business_id=bundle["business"].id).count() == 0

        other = _complete(client, headers, bundle, bundle["employee"].id)
        client.put(
            f"/api/shop/appointments/{other.id}",
            json={"status": "completed", "payment_method": "cash"},
            headers=headers,
        )
        assert Sale.query.filter_by(business_id=bundle["business"].id).count() == 1


def test_product_sale_has_no_service_commission(app, client):
    with app.app_context():
        bundle = create_tenant_bundle(slug=f"comm-prod-{uuid.uuid4().hex[:8]}")
        owner, owner_emp = _owner(bundle)
        product = InventoryProduct(
            business_id=bundle["business"].id,
            name="Pomada",
            item_kind="RETAIL_PRODUCT",
            price=8500,
            unit_cost=4000,
            stock=5,
            min_stock=1,
            is_active=True,
        )
        db.session.add(product)
        db.session.commit()
        res = client.post(
            f"/api/shop/inventory/{product.id}/sale",
            json={"quantity": 1, "payment_method": "cash"},
            headers=_auth(owner, bundle["business"].id, owner_emp.id),
        )
        assert res.status_code == 201
        item = Sale.query.get(uuid.UUID(res.get_json()["sale"]["id"])).items[0]
        assert item.item_type == "product"
        assert item.commission_percentage is None
        assert item.staff_earnings is None
        insights = build_insights(bundle["business"].id, range_key="today")
        assert insights["snapshot"]["staff_commissions"] == 0
        assert insights["snapshot"]["product_revenue"] == 8500


def test_adjusted_pos_service_price_uses_line_total(app):
    with app.app_context():
        from app.shop_sales import create_sale

        bundle = create_tenant_bundle(slug=f"comm-adj-{uuid.uuid4().hex[:8]}")
        bundle["employee"].commission_percentage = Decimal("50")
        db.session.commit()
        sale, _ = create_sale(
            business_id=bundle["business"].id,
            created_by_user_id=bundle["employee"].user_id,
            employee_id=bundle["employee"].id,
            payment_method="cash",
            items=[
                {
                    "item_type": "service",
                    "service_type_id": str(bundle["service"].id),
                    "quantity": 1,
                    "unit_price": 8000,
                }
            ],
        )
        db.session.commit()
        item = sale.items[0]
        assert float(item.line_total) == 8000
        assert float(item.staff_earnings) == 4000
        assert float(item.business_earnings) == 4000
