import os

# Set test env before importing app/config so Config picks up sqlite.
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["WHATSAPP_NOTIFICATIONS_ENABLED"] = "true"
os.environ["TWILIO_ACCOUNT_SID"] = "ACtest"
os.environ["TWILIO_AUTH_TOKEN"] = "test_auth_token"
os.environ["TWILIO_WHATSAPP_FROM"] = "whatsapp:+14155238886"
os.environ["TWILIO_WHATSAPP_CONTENT_SID"] = "HXtesttemplate"
os.environ["DEFAULT_PHONE_COUNTRY_CODE"] = "CR"

import uuid
from datetime import datetime, timedelta

import pytest

from app import create_app
from app.extensions import db
from app.models import (
    Appointment,
    Business,
    Client,
    Employee,
    NotificationLog,
    ServiceType,
    User,
)


@pytest.fixture()
def app():
    application = create_app()
    application.config.update(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "WHATSAPP_NOTIFICATIONS_ENABLED": True,
            "TWILIO_ACCOUNT_SID": "ACtest",
            "TWILIO_AUTH_TOKEN": "test_auth_token",
            "TWILIO_WHATSAPP_FROM": "whatsapp:+14155238886",
            "TWILIO_WHATSAPP_CONTENT_SID": "HXtesttemplate",
            "DEFAULT_PHONE_COUNTRY_CODE": "CR",
            "TWILIO_REQUEST_TIMEOUT": 5,
        }
    )

    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def create_tenant_bundle(
    *,
    slug: str = "test-shop",
    country_code: str | None = "CR",
    client_phone: str = "88887777",
):
    business = Business(
        name="Barbería Test",
        address="San José, Costa Rica",
        email="shop@test.com",
        phone="22223333",
        public_slug=slug,
        country_code=country_code,
    )
    db.session.add(business)
    db.session.flush()

    service = ServiceType(
        business_id=business.id,
        name="Corte clásico",
        duration=30,
        price=5000,
        is_active=True,
    )
    user = User(
        business_id=business.id,
        email="barber@test.com",
        encrypted_password="x",
        role="employee",
        is_active=True,
    )
    db.session.add_all([service, user])
    db.session.flush()

    employee = Employee(
        user_id=user.id,
        business_id=business.id,
        display_name="Carlos Barber",
        is_active=True,
    )
    client_row = Client(
        business_id=business.id,
        first_name="María",
        last_name="López",
        phone=client_phone,
        email="maria@test.com",
        appointments_amount=0,
    )
    db.session.add_all([employee, client_row])
    db.session.commit()

    return {
        "business": business,
        "service": service,
        "employee": employee,
        "client": client_row,
    }


def create_appointment(bundle, *, phone: str | None = None) -> Appointment:
    start = datetime.now().replace(second=0, microsecond=0) + timedelta(days=1)
    end = start + timedelta(minutes=30)
    phone_value = phone if phone is not None else bundle["client"].phone
    appt = Appointment(
        client_id=bundle["client"].id,
        service_type_id=bundle["service"].id,
        business_id=bundle["business"].id,
        employee_id=bundle["employee"].id,
        client_name="María López",
        client_email="maria@test.com",
        client_phone=phone_value,
        start_time=start,
        end_time=end,
        status="confirmed",
    )
    db.session.add(appt)
    db.session.commit()
    return appt
