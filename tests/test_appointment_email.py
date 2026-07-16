"""Tests for appointment email confirmation helpers."""

from unittest.mock import patch

from app.appointment_notifications import (
    notify_appointment_created,
    send_appointment_email_confirmation,
)
from app.email_provider import is_valid_email
from tests.conftest import create_appointment, create_tenant_bundle


def test_is_valid_email():
    assert is_valid_email("ana@test.com")
    assert not is_valid_email("")
    assert not is_valid_email("—")
    assert not is_valid_email("not-an-email")


def test_email_skipped_when_missing(app):
    with app.app_context():
        bundle = create_tenant_bundle()
        appt = create_appointment(bundle, phone="88887777")
        appt.client_email = "—"
        if appt.client:
            appt.client.email = None
        result = send_appointment_email_confirmation(appt)
        assert result["status"] == "skipped"


def test_email_skipped_when_disabled(app):
    with app.app_context():
        app.config["EMAIL_NOTIFICATIONS_ENABLED"] = False
        bundle = create_tenant_bundle()
        appt = create_appointment(bundle)
        appt.client_email = "ana@test.com"
        result = send_appointment_email_confirmation(appt)
        assert result["status"] == "skipped"


@patch("app.appointment_notifications.send_email")
def test_email_sent_when_configured(mock_send, app):
    from app.email_provider import EmailSendResult

    mock_send.return_value = EmailSendResult(ok=True, message_id="<id@test>")
    with app.app_context():
        app.config["EMAIL_NOTIFICATIONS_ENABLED"] = True
        app.config["MAIL_SERVER"] = "smtp.test"
        app.config["MAIL_PORT"] = 587
        app.config["MAIL_DEFAULT_SENDER"] = "noreply@test.com"
        bundle = create_tenant_bundle()
        appt = create_appointment(bundle)
        appt.client_email = "ana@test.com"
        result = send_appointment_email_confirmation(appt)
        assert result["status"] == "sent"
        mock_send.assert_called_once()


@patch("app.appointment_notifications.send_appointment_confirmation", return_value={"status": "skipped"})
@patch("app.appointment_notifications.send_appointment_email_confirmation", return_value={"status": "sent"})
def test_notify_fanout(mock_email, mock_wa, app):
    with app.app_context():
        app.config["WHATSAPP_NOTIFICATIONS_ENABLED"] = True
        bundle = create_tenant_bundle()
        appt = create_appointment(bundle)
        result = notify_appointment_created(appt)
        assert result["email"] == "sent"
        assert result["whatsapp"] == "skipped"
        assert result["status"] == "sent"
        mock_email.assert_called_once()
        mock_wa.assert_called_once()


@patch("app.appointment_notifications.send_appointment_confirmation")
@patch("app.appointment_notifications.send_appointment_email_confirmation", return_value={"status": "sent"})
def test_notify_skips_whatsapp_when_disabled(mock_email, mock_wa, app):
    with app.app_context():
        app.config["WHATSAPP_NOTIFICATIONS_ENABLED"] = False
        bundle = create_tenant_bundle()
        appt = create_appointment(bundle)
        result = notify_appointment_created(appt)
        assert result["email"] == "sent"
        assert result["whatsapp"] == "skipped"
        mock_email.assert_called_once()
        mock_wa.assert_not_called()
