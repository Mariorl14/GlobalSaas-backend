import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from twilio.request_validator import RequestValidator

from app.appointment_notifications import send_appointment_confirmation
from app.extensions import db
from app.models import NotificationLog
from app.phone_utils import normalize_phone_for_whatsapp
from app.whatsapp_provider import build_appointment_confirmation_variables
from tests.conftest import create_appointment, create_tenant_bundle


class TestPhoneNormalization:
    def test_normalizes_costa_rica_local_number(self):
        result = normalize_phone_for_whatsapp(
            "8888-7777",
            country_code="CR",
        )
        assert result.ok is True
        assert result.e164 == "+50688887777"
        assert result.whatsapp_to == "whatsapp:+50688887777"

    def test_invalid_phone_does_not_raise(self):
        result = normalize_phone_for_whatsapp("abc", country_code="CR")
        assert result.ok is False
        assert result.error == "invalid_phone_format"

    def test_missing_country_without_plus_fails_safely(self):
        result = normalize_phone_for_whatsapp("88887777", country_code=None, default_country_code=None)
        assert result.ok is False
        assert result.error == "invalid_phone_no_country"


class TestAppointmentNotifications:
    @patch("twilio.rest.Client")
    def test_successful_appointment_sends_one_confirmation(self, mock_client_cls, app):
        bundle = create_tenant_bundle()
        appt = create_appointment(bundle)

        mock_message = MagicMock()
        mock_message.sid = "SM123"
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message
        mock_client_cls.return_value = mock_client

        with app.app_context():
            result = send_appointment_confirmation(appt)

        assert result["status"] == "sent"
        mock_client.messages.create.assert_called_once()
        kwargs = mock_client.messages.create.call_args.kwargs
        assert kwargs["content_sid"] == "HXtesttemplate"
        assert kwargs["to"] == "whatsapp:+50688887777"
        variables = json.loads(kwargs["content_variables"])
        assert variables["1"] == "María"
        assert variables["2"] == "Barbería Test"
        assert variables["3"] == "Corte clásico"
        assert variables["4"] == "Carlos Barber"

        log = NotificationLog.query.filter_by(appointment_id=appt.id).one()
        assert log.status == "sent"
        assert log.provider_message_sid == "SM123"
        assert log.business_id == bundle["business"].id

    @patch("twilio.rest.Client")
    def test_duplicate_execution_does_not_send_twice(self, mock_client_cls, app):
        bundle = create_tenant_bundle()
        appt = create_appointment(bundle)

        mock_message = MagicMock()
        mock_message.sid = "SM123"
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message
        mock_client_cls.return_value = mock_client

        with app.app_context():
            first = send_appointment_confirmation(appt)
            second = send_appointment_confirmation(appt)

        assert first["status"] == "sent"
        assert second["status"] == "sent"
        mock_client.messages.create.assert_called_once()

    def test_missing_phone_skips_without_breaking(self, app):
        bundle = create_tenant_bundle(client_phone="")
        appt = create_appointment(bundle, phone="")

        with app.app_context():
            result = send_appointment_confirmation(appt)

        assert result["status"] == "skipped"
        log = NotificationLog.query.filter_by(appointment_id=appt.id).one()
        assert log.status == "skipped"
        assert log.error_code == "missing_phone"

    def test_invalid_phone_does_not_call_twilio(self, app):
        bundle = create_tenant_bundle()
        appt = create_appointment(bundle, phone="not-a-phone")

        with app.app_context():
            with patch("twilio.rest.Client") as mock_client_cls:
                result = send_appointment_confirmation(appt)

        assert result["status"] == "skipped"
        mock_client_cls.assert_not_called()

    def test_missing_credentials_skips_without_breaking_appointment(self, app):
        bundle = create_tenant_bundle()
        appt = create_appointment(bundle)

        with app.app_context():
            app.config["TWILIO_ACCOUNT_SID"] = ""
            result = send_appointment_confirmation(appt)

        assert result["status"] == "skipped"

    @patch("twilio.rest.Client")
    def test_twilio_exception_does_not_raise(self, mock_client_cls, app):
        from twilio.base.exceptions import TwilioRestException

        bundle = create_tenant_bundle()
        appt = create_appointment(bundle)

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = TwilioRestException(
            status=400,
            uri="/Messages",
            msg="Invalid recipient",
            code=21211,
        )
        mock_client_cls.return_value = mock_client

        with app.app_context():
            result = send_appointment_confirmation(appt)

        assert result["status"] == "failed"
        log = NotificationLog.query.filter_by(appointment_id=appt.id).one()
        assert log.status == "failed"
        assert log.error_code == "21211"

    @patch("twilio.rest.Client")
    def test_correct_tenant_data_is_used(self, mock_client_cls, app):
        bundle_a = create_tenant_bundle(slug="shop-a", country_code="CR")
        bundle_b = create_tenant_bundle(slug="shop-b", country_code="CR")
        bundle_b["business"].name = "Otra Barbería"
        bundle_b["service"].name = "Afeitado"
        db.session.commit()

        appt = create_appointment(bundle_b)

        mock_message = MagicMock()
        mock_message.sid = "SM999"
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message
        mock_client_cls.return_value = mock_client

        with app.app_context():
            send_appointment_confirmation(appt)

        variables = json.loads(mock_client.messages.create.call_args.kwargs["content_variables"])
        assert variables["2"] == "Otra Barbería"
        assert variables["3"] == "Afeitado"
        assert bundle_a["business"].name != variables["2"]

    def test_disabled_notifications_are_skipped(self, app):
        bundle = create_tenant_bundle()
        appt = create_appointment(bundle)

        with app.app_context():
            app.config["WHATSAPP_NOTIFICATIONS_ENABLED"] = False
            with patch("twilio.rest.Client") as mock_client_cls:
                result = send_appointment_confirmation(appt)

        assert result["status"] == "skipped"
        mock_client_cls.assert_not_called()


class TestPublicBookingIntegration:
    @patch("app.public_booking.notify_appointment_created")
    def test_validation_failure_sends_nothing(self, mock_send, client, app):
        bundle = create_tenant_bundle()
        with app.app_context():
            resp = client.post(
                f"/api/public/booking/{bundle['business'].public_slug}/bookings",
                json={"service_id": str(bundle["service"].id)},
            )
        assert resp.status_code == 400
        mock_send.assert_not_called()

    @patch(
        "app.public_booking.notify_appointment_created",
        return_value={"status": "sent", "email": "sent", "whatsapp": "skipped"},
    )
    @patch("app.public_booking._slot_is_bookable", return_value=True)
    def test_successful_booking_calls_notification_after_commit(
        self, _mock_slot, mock_send, client, app
    ):
        bundle = create_tenant_bundle()
        start = datetime.now().replace(second=0, microsecond=0) + timedelta(days=2)
        start = start.replace(minute=(start.minute // 15) * 15)
        end = start + timedelta(minutes=30)

        with app.app_context():
            resp = client.post(
                f"/api/public/booking/{bundle['business'].public_slug}/bookings",
                json={
                    "service_id": str(bundle["service"].id),
                    "employee_id": str(bundle["employee"].id),
                    "start_time": start.isoformat(),
                    "end_time": end.isoformat(),
                    "first_name": "Ana",
                    "last_name": "Pérez",
                    "phone": "88887777",
                    "email": "ana@test.com",
                },
            )

        assert resp.status_code == 201
        body = resp.get_json()
        assert body["notification_status"] == "sent"
        assert body["email_notification_status"] == "sent"
        mock_send.assert_called_once()


class TestWebhook:
    def test_webhook_signature_validation_and_status_update(self, client, app):
        bundle = create_tenant_bundle()
        appt = create_appointment(bundle)

        with app.app_context():
            log = NotificationLog(
                business_id=bundle["business"].id,
                appointment_id=appt.id,
                client_id=bundle["client"].id,
                channel="whatsapp",
                notification_type="appointment_confirmation",
                provider="twilio",
                status="sent",
                provider_message_sid="SMwebhook1",
                attempt_count=1,
            )
            db.session.add(log)
            db.session.commit()

            url = "http://localhost/api/webhooks/twilio/whatsapp-status"
            params = {"MessageSid": "SMwebhook1", "MessageStatus": "delivered"}
            signature = RequestValidator("test_auth_token").compute_signature(url, params)

            resp = client.post(
                "/api/webhooks/twilio/whatsapp-status",
                data=params,
                headers={"X-Twilio-Signature": signature},
            )

        assert resp.status_code == 200
        updated = NotificationLog.query.filter_by(provider_message_sid="SMwebhook1").one()
        assert updated.status == "delivered"

    def test_webhook_rejects_invalid_signature(self, client, app):
        with app.app_context():
            resp = client.post(
                "/api/webhooks/twilio/whatsapp-status",
                data={"MessageSid": "SMx", "MessageStatus": "delivered"},
                headers={"X-Twilio-Signature": "invalid"},
            )
        assert resp.status_code == 403


class TestTemplateVariables:
    def test_build_appointment_confirmation_variables(self):
        variables = build_appointment_confirmation_variables(
            customer_name="Juan",
            shop_name="Shop",
            service_name="Corte",
            barber_name="Luis",
            appointment_date="13/07/2026",
            appointment_time="15:00",
        )
        assert variables["6"] == "15:00"
        assert "7" not in variables
