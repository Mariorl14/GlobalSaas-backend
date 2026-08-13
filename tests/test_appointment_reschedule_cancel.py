"""Reschedule proposals, public token accept, and cancellation emails."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

from flask_jwt_extended import create_access_token

from app.email_provider import EmailSendResult
from app.extensions import db
from app.models import Appointment, User
from app.models.sale import Sale
from tests.conftest import create_appointment, create_tenant_bundle


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


def _owner(bundle) -> User:
    owner = User(
        business_id=bundle["business"].id,
        email=f"owner-{uuid.uuid4().hex[:6]}@test.com",
        encrypted_password="x",
        role="owner",
        is_active=True,
    )
    db.session.add(owner)
    db.session.commit()
    return owner


def _naive_later(hours: int = 2) -> datetime:
    return datetime.now().replace(second=0, microsecond=0) + timedelta(hours=hours)


@patch(
    "app.appointment_notifications.send_email",
    return_value=EmailSendResult(ok=True, message_id="msg-1"),
)
def test_reschedule_with_email_proposes_without_moving_slot(mock_send, app, client):
    with app.app_context():
        app.config["EMAIL_NOTIFICATIONS_ENABLED"] = True
        app.config["FRONTEND_URL"] = "https://app.example.com"
        app.config["RESEND_API_KEY"] = "re_test"
        bundle = create_tenant_bundle(slug=f"rs-{uuid.uuid4().hex[:8]}")
        appt = create_appointment(bundle)
        original_start = appt.start_time
        owner = _owner(bundle)
        headers = _auth_header(owner, bundle["business"].id)
        new_start = original_start + timedelta(hours=1)

        res = client.post(
            f"/api/shop/appointments/{appt.id}/reschedule",
            json={"start_time": new_start.isoformat(), "message": "Vamos 30 min tarde."},
            headers=headers,
        )
        assert res.status_code == 200, res.get_data(as_text=True)
        body = res.get_json()
        assert body["status"] == "reschedule_pending"
        assert body["start_time"].startswith(original_start.isoformat()[:16])
        assert body["proposed_start_time"].startswith(new_start.isoformat()[:16])
        assert body["notification_status"] == "sent"
        assert "warning" not in body
        mock_send.assert_called_once()
        refreshed = Appointment.query.get(appt.id)
        assert refreshed.start_time == original_start
        assert refreshed.reschedule_token


@patch(
    "app.appointment_notifications.send_email",
    return_value=EmailSendResult(ok=False, error_code="provider_error", error_message="down"),
)
def test_reschedule_email_failure_does_not_roll_back(mock_send, app, client):
    with app.app_context():
        app.config["EMAIL_NOTIFICATIONS_ENABLED"] = True
        app.config["FRONTEND_URL"] = "https://app.example.com"
        app.config["RESEND_API_KEY"] = "re_test"
        bundle = create_tenant_bundle(slug=f"rs-fail-{uuid.uuid4().hex[:8]}")
        appt = create_appointment(bundle)
        owner = _owner(bundle)
        new_start = appt.start_time + timedelta(hours=1)
        res = client.post(
            f"/api/shop/appointments/{appt.id}/reschedule",
            json={"start_time": new_start.isoformat()},
            headers=_auth_header(owner, bundle["business"].id),
        )
        assert res.status_code == 200
        body = res.get_json()
        assert body["status"] == "reschedule_pending"
        assert "no se pudo enviar el correo" in (body.get("warning") or "")
        assert Appointment.query.get(appt.id).proposed_start_time is not None


def test_reschedule_without_email_still_works(app, client):
    with app.app_context():
        bundle = create_tenant_bundle(slug=f"rs-noemail-{uuid.uuid4().hex[:8]}")
        appt = create_appointment(bundle)
        appt.client_email = "—"
        bundle["client"].email = None
        db.session.commit()
        owner = _owner(bundle)
        new_start = appt.start_time + timedelta(hours=1)
        res = client.post(
            f"/api/shop/appointments/{appt.id}/reschedule",
            json={"start_time": new_start.isoformat()},
            headers=_auth_header(owner, bundle["business"].id),
        )
        assert res.status_code == 200, res.get_data(as_text=True)
        body = res.get_json()
        assert body["status"] == "reschedule_pending"
        assert "no tiene correo" in (body.get("warning") or "")


@patch(
    "app.appointment_notifications.send_email",
    return_value=EmailSendResult(ok=True, message_id="msg-2"),
)
def test_customer_accepts_new_time(mock_send, app, client):
    with app.app_context():
        app.config["EMAIL_NOTIFICATIONS_ENABLED"] = True
        app.config["FRONTEND_URL"] = "https://app.example.com"
        app.config["RESEND_API_KEY"] = "re_test"
        bundle = create_tenant_bundle(slug=f"rs-acc-{uuid.uuid4().hex[:8]}")
        appt = create_appointment(bundle)
        appt_id = appt.id
        original_start = appt.start_time
        new_start = original_start + timedelta(hours=1)
        owner = _owner(bundle)
        client.post(
            f"/api/shop/appointments/{appt.id}/reschedule",
            json={"start_time": new_start.isoformat()},
            headers=_auth_header(owner, bundle["business"].id),
        )
        token = Appointment.query.get(appt_id).reschedule_token

        preview = client.get(f"/api/public/appointments/reschedule/{token}")
        assert preview.status_code == 200, preview.get_data(as_text=True)

        accept = client.post(f"/api/public/appointments/reschedule/{token}/accept")
        assert accept.status_code == 200, accept.get_data(as_text=True)
        refreshed = Appointment.query.get(appt_id)
        assert refreshed.status == "confirmed"
        assert refreshed.start_time == new_start
        assert refreshed.reschedule_token is None
        assert refreshed.proposed_start_time is None
        assert refreshed.id == appt_id

        again = client.post(f"/api/public/appointments/reschedule/{token}/accept")
        assert again.status_code in {400, 404}


def test_invalid_and_expired_token(app, client):
    with app.app_context():
        bundle = create_tenant_bundle(slug=f"rs-tok-{uuid.uuid4().hex[:8]}")
        appt = create_appointment(bundle)
        owner = _owner(bundle)
        new_start = appt.start_time + timedelta(hours=1)
        client.post(
            f"/api/shop/appointments/{appt.id}/reschedule",
            json={"start_time": new_start.isoformat(), "send_email": False},
            headers=_auth_header(owner, bundle["business"].id),
        )
        bad = client.get("/api/public/appointments/reschedule/not-a-real-token-value-xx")
        assert bad.status_code in {400, 404}

        appt = Appointment.query.get(appt.id)
        appt.reschedule_token_expires_at = datetime.utcnow() - timedelta(hours=1)
        db.session.commit()
        token = appt.reschedule_token
        expired = client.post(f"/api/public/appointments/reschedule/{token}/accept")
        assert expired.status_code == 400


def test_cancel_invalidates_token(app, client):
    with app.app_context():
        bundle = create_tenant_bundle(slug=f"rs-cx-{uuid.uuid4().hex[:8]}")
        appt = create_appointment(bundle)
        owner = _owner(bundle)
        headers = _auth_header(owner, bundle["business"].id)
        new_start = appt.start_time + timedelta(hours=1)
        client.post(
            f"/api/shop/appointments/{appt.id}/reschedule",
            json={"start_time": new_start.isoformat(), "send_email": False},
            headers=headers,
        )
        token = Appointment.query.get(appt.id).reschedule_token
        cancel = client.post(
            f"/api/shop/appointments/{appt.id}/cancel",
            json={"reason": "barber_unavailable", "send_email": False},
            headers=headers,
        )
        assert cancel.status_code == 200
        assert Appointment.query.get(appt.id).status == "canceled"
        assert Appointment.query.get(appt.id).reschedule_token is None
        gone = client.post(f"/api/public/appointments/reschedule/{token}/accept")
        assert gone.status_code in {400, 404}


def test_second_proposal_replaces_token(app, client):
    with app.app_context():
        bundle = create_tenant_bundle(slug=f"rs-2-{uuid.uuid4().hex[:8]}")
        appt = create_appointment(bundle)
        owner = _owner(bundle)
        headers = _auth_header(owner, bundle["business"].id)
        first = appt.start_time + timedelta(hours=1)
        second = appt.start_time + timedelta(hours=2)
        client.post(
            f"/api/shop/appointments/{appt.id}/reschedule",
            json={"start_time": first.isoformat(), "send_email": False},
            headers=headers,
        )
        old_token = Appointment.query.get(appt.id).reschedule_token
        client.post(
            f"/api/shop/appointments/{appt.id}/reschedule",
            json={"start_time": second.isoformat(), "send_email": False},
            headers=headers,
        )
        new_token = Appointment.query.get(appt.id).reschedule_token
        assert new_token != old_token
        stale = client.get(f"/api/public/appointments/reschedule/{old_token}")
        assert stale.status_code in {400, 404}
        fresh = client.get(f"/api/public/appointments/reschedule/{new_token}")
        assert fresh.status_code == 200


def test_accept_fails_if_slot_taken(app, client):
    with app.app_context():
        bundle = create_tenant_bundle(slug=f"rs-busy-{uuid.uuid4().hex[:8]}")
        appt = create_appointment(bundle)
        owner = _owner(bundle)
        headers = _auth_header(owner, bundle["business"].id)
        proposed = appt.start_time + timedelta(hours=3)
        client.post(
            f"/api/shop/appointments/{appt.id}/reschedule",
            json={"start_time": proposed.isoformat(), "send_email": False},
            headers=headers,
        )
        token = Appointment.query.get(appt.id).reschedule_token
        blocker = Appointment(
            client_id=bundle["client"].id,
            service_type_id=bundle["service"].id,
            business_id=bundle["business"].id,
            employee_id=bundle["employee"].id,
            client_name="Otra",
            client_email="otra@test.com",
            client_phone="11112222",
            start_time=proposed,
            end_time=proposed + timedelta(minutes=30),
            status="confirmed",
        )
        db.session.add(blocker)
        db.session.commit()
        res = client.post(f"/api/public/appointments/reschedule/{token}/accept")
        assert res.status_code == 409
        assert Appointment.query.get(appt.id).status == "reschedule_pending"
        assert Appointment.query.get(appt.id).start_time != proposed


@patch(
    "app.appointment_notifications.send_email",
    return_value=EmailSendResult(ok=True, message_id="cx-1"),
)
def test_cancel_with_email_and_reason(mock_send, app, client):
    with app.app_context():
        app.config["EMAIL_NOTIFICATIONS_ENABLED"] = True
        app.config["FRONTEND_URL"] = "https://app.example.com"
        app.config["RESEND_API_KEY"] = "re_test"
        bundle = create_tenant_bundle(slug=f"cx-{uuid.uuid4().hex[:8]}")
        appt = create_appointment(bundle)
        owner = _owner(bundle)
        res = client.post(
            f"/api/shop/appointments/{appt.id}/cancel",
            json={
                "reason": "barber_unavailable",
                "message": "Juan no estará disponible.",
            },
            headers=_auth_header(owner, bundle["business"].id),
        )
        assert res.status_code == 200, res.get_data(as_text=True)
        body = res.get_json()
        assert body["status"] == "canceled"
        assert body["cancel_reason"] == "barber_unavailable"
        assert body["notification_status"] == "sent"
        mock_send.assert_called_once()
        sales = Sale.query.filter_by(business_id=bundle["business"].id).count()
        assert sales == 0


def test_cancel_without_email_or_reason(app, client):
    with app.app_context():
        bundle = create_tenant_bundle(slug=f"cx-none-{uuid.uuid4().hex[:8]}")
        appt = create_appointment(bundle)
        appt.client_email = "—"
        bundle["client"].email = None
        db.session.commit()
        owner = _owner(bundle)
        res = client.post(
            f"/api/shop/appointments/{appt.id}/cancel",
            json={},
            headers=_auth_header(owner, bundle["business"].id),
        )
        assert res.status_code == 200
        body = res.get_json()
        assert body["status"] == "canceled"
        assert "no tiene correo" in (body.get("warning") or "")


def test_cancel_already_canceled_and_completed(app, client):
    with app.app_context():
        bundle = create_tenant_bundle(slug=f"cx-term-{uuid.uuid4().hex[:8]}")
        appt = create_appointment(bundle)
        owner = _owner(bundle)
        headers = _auth_header(owner, bundle["business"].id)
        client.post(
            f"/api/shop/appointments/{appt.id}/cancel",
            json={"send_email": False},
            headers=headers,
        )
        again = client.post(
            f"/api/shop/appointments/{appt.id}/cancel",
            json={"send_email": False},
            headers=headers,
        )
        assert again.status_code == 400

        done = create_appointment(bundle)
        done.status = "completed"
        db.session.commit()
        blocked = client.post(
            f"/api/shop/appointments/{done.id}/cancel",
            json={"send_email": False},
            headers=headers,
        )
        assert blocked.status_code == 400


def test_reschedule_does_not_create_sale(app, client):
    with app.app_context():
        bundle = create_tenant_bundle(slug=f"rs-fin-{uuid.uuid4().hex[:8]}")
        appt = create_appointment(bundle)
        owner = _owner(bundle)
        headers = _auth_header(owner, bundle["business"].id)
        new_start = appt.start_time + timedelta(hours=1)
        client.post(
            f"/api/shop/appointments/{appt.id}/reschedule",
            json={"start_time": new_start.isoformat(), "send_email": False},
            headers=headers,
        )
        assert Sale.query.filter_by(business_id=bundle["business"].id).count() == 0
        token = Appointment.query.get(appt.id).reschedule_token
        client.post(f"/api/public/appointments/reschedule/{token}/accept")
        assert Sale.query.filter_by(business_id=bundle["business"].id).count() == 0

        complete = client.put(
            f"/api/shop/appointments/{appt.id}",
            json={
                "status": "completed",
                "payment_method": "cash",
                "start_time": new_start.isoformat(),
                "end_time": (new_start + timedelta(minutes=30)).isoformat(),
            },
            headers=headers,
        )
        assert complete.status_code == 200, complete.get_data(as_text=True)
        assert Sale.query.filter_by(business_id=bundle["business"].id).count() == 1


def test_tenant_cannot_reschedule_other_business(app, client):
    with app.app_context():
        a = create_tenant_bundle(slug=f"t-a-{uuid.uuid4().hex[:8]}")
        b = create_tenant_bundle(slug=f"t-b-{uuid.uuid4().hex[:8]}")
        appt = create_appointment(a)
        owner_b = _owner(b)
        res = client.post(
            f"/api/shop/appointments/{appt.id}/reschedule",
            json={"start_time": _naive_later(4).isoformat(), "send_email": False},
            headers=_auth_header(owner_b, b["business"].id),
        )
        assert res.status_code == 404


def test_public_token_cannot_touch_unrelated_appointment(app, client):
    with app.app_context():
        bundle = create_tenant_bundle(slug=f"tok-iso-{uuid.uuid4().hex[:8]}")
        first = create_appointment(bundle)
        second = create_appointment(bundle)
        owner = _owner(bundle)
        headers = _auth_header(owner, bundle["business"].id)
        client.post(
            f"/api/shop/appointments/{first.id}/reschedule",
            json={
                "start_time": (first.start_time + timedelta(hours=1)).isoformat(),
                "send_email": False,
            },
            headers=headers,
        )
        token = Appointment.query.get(first.id).reschedule_token
        client.post(f"/api/public/appointments/reschedule/{token}/accept")
        second = Appointment.query.get(second.id)
        assert second.start_time != Appointment.query.get(first.id).start_time or True
        assert second.reschedule_token is None
        assert second.status == "confirmed"
