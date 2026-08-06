"""Resend HTTPS path is preferred when RESEND_API_KEY is set."""

from unittest.mock import MagicMock, patch

from app.email_provider import EmailSendResult, email_configured, get_provider_name, send_email


def test_resend_preferred_when_api_key_set(app):
    with app.app_context():
        app.config["RESEND_API_KEY"] = "re_test_key"
        app.config["MAIL_DEFAULT_SENDER"] = "onboarding@resend.dev"
        app.config["MAIL_DEFAULT_SENDER_NAME"] = "Barber Suite"
        assert email_configured() is True
        assert get_provider_name() == "resend"

        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"id":"msg_123"}'
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None

        with patch("app.email_provider.urllib.request.urlopen", return_value=mock_resp) as mock_open:
            result = send_email(
                to_email="ana@test.com",
                subject="Test",
                text_body="Hello",
                html_body="<p>Hello</p>",
            )
        assert result.ok is True
        assert result.message_id == "msg_123"
        mock_open.assert_called_once()
        req = mock_open.call_args[0][0]
        assert req.full_url == "https://api.resend.com/emails"
        assert "Bearer re_test_key" in req.get_header("Authorization")


def test_smtp_used_without_resend_key(app):
    with app.app_context():
        app.config["RESEND_API_KEY"] = ""
        app.config["MAIL_SERVER"] = "smtp.test"
        app.config["MAIL_PORT"] = 587
        app.config["MAIL_DEFAULT_SENDER"] = "noreply@test.com"
        assert get_provider_name() == "smtp"

        with patch(
            "app.email_provider._send_via_smtp",
            return_value=EmailSendResult(ok=True, message_id="<id>"),
        ) as mock_smtp:
            result = send_email(
                to_email="ana@test.com",
                subject="Test",
                text_body="Hello",
            )
        assert result.ok is True
        mock_smtp.assert_called_once()
