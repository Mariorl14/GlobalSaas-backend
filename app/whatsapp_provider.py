"""Twilio WhatsApp provider — isolated from routes and appointment logic."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from flask import current_app

logger = logging.getLogger(__name__)

_PROVIDER_NAME = "twilio"
_MAX_ERROR_LEN = 500


@dataclass(frozen=True)
class WhatsAppSendResult:
    ok: bool
    message_sid: str | None = None
    error_code: str | None = None
    error_message: str | None = None


def _sanitize_error_message(message: str | None) -> str | None:
    if not message:
        return None
    text = " ".join(str(message).split())
    if len(text) > _MAX_ERROR_LEN:
        return text[:_MAX_ERROR_LEN] + "…"
    return text


def twilio_configured() -> bool:
    cfg = current_app.config
    return bool(
        cfg.get("TWILIO_ACCOUNT_SID")
        and cfg.get("TWILIO_AUTH_TOKEN")
        and cfg.get("TWILIO_WHATSAPP_FROM")
        and cfg.get("TWILIO_WHATSAPP_CONTENT_SID")
    )


def build_appointment_confirmation_variables(
    *,
    customer_name: str,
    shop_name: str,
    service_name: str,
    barber_name: str,
    appointment_date: str,
    appointment_time: str,
) -> dict[str, str]:
    return {
        "1": customer_name[:80],
        "2": shop_name[:80],
        "3": service_name[:80],
        "4": barber_name[:80],
        "5": appointment_date[:40],
        "6": appointment_time[:20],
    }


def send_whatsapp_template(
    *,
    to_whatsapp: str,
    content_sid: str,
    content_variables: dict[str, str],
) -> WhatsAppSendResult:
    """Send a WhatsApp template message via Twilio. Never raises."""
    if not twilio_configured():
        return WhatsAppSendResult(
            ok=False,
            error_code="not_configured",
            error_message="Twilio WhatsApp is not configured.",
        )

    try:
        from twilio.base.exceptions import TwilioException, TwilioRestException
        from twilio.rest import Client
    except ImportError:
        return WhatsAppSendResult(
            ok=False,
            error_code="provider_unavailable",
            error_message="Twilio SDK is not installed.",
        )

    cfg = current_app.config
    timeout = int(cfg.get("TWILIO_REQUEST_TIMEOUT", 10))

    try:
        client = Client(cfg["TWILIO_ACCOUNT_SID"], cfg["TWILIO_AUTH_TOKEN"])
        client.http_client.timeout = timeout
        message = client.messages.create(
            from_=cfg["TWILIO_WHATSAPP_FROM"],
            to=to_whatsapp,
            content_sid=content_sid,
            content_variables=json.dumps(content_variables),
        )
        sid = getattr(message, "sid", None)
        return WhatsAppSendResult(ok=True, message_sid=str(sid) if sid else None)
    except TwilioRestException as exc:
        code = str(getattr(exc, "code", None) or "twilio_rest_error")
        logger.warning(
            "Twilio WhatsApp send failed",
            extra={"error_code": code, "status": getattr(exc, "status", None)},
        )
        return WhatsAppSendResult(
            ok=False,
            error_code=code[:40],
            error_message=_sanitize_error_message(str(exc.msg or exc)),
        )
    except TwilioException as exc:
        logger.warning("Twilio WhatsApp send failed", extra={"error_code": "twilio_error"})
        return WhatsAppSendResult(
            ok=False,
            error_code="twilio_error",
            error_message=_sanitize_error_message(str(exc)),
        )
    except Exception as exc:
        logger.exception("Unexpected Twilio WhatsApp error")
        return WhatsAppSendResult(
            ok=False,
            error_code="provider_error",
            error_message=_sanitize_error_message(str(exc)),
        )


def map_twilio_status_to_notification_status(twilio_status: str) -> str | None:
    """Map Twilio message status webhook values to notification_log.status."""
    normalized = (twilio_status or "").strip().lower()
    mapping: dict[str, str] = {
        "queued": "pending",
        "sending": "pending",
        "sent": "sent",
        "delivered": "delivered",
        "read": "read",
        "failed": "failed",
        "undelivered": "failed",
    }
    return mapping.get(normalized)
