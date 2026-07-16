"""
Appointment confirmations — WhatsApp (optional) + email when customer email is present.
Call after the appointment transaction commits.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from flask import current_app
from sqlalchemy.exc import IntegrityError

from app.email_provider import (
    PROVIDER_NAME as EMAIL_PROVIDER_NAME,
    build_appointment_confirmation_email,
    email_configured,
    is_valid_email,
    send_email,
)
from app.extensions import db
from app.models import Appointment, NotificationLog
from app.phone_utils import normalize_phone_for_whatsapp
from app.whatsapp_provider import (
    _PROVIDER_NAME as WHATSAPP_PROVIDER_NAME,
    build_appointment_confirmation_variables,
    send_whatsapp_template,
    twilio_configured,
)

logger = logging.getLogger(__name__)

CHANNEL_WHATSAPP = "whatsapp"
CHANNEL_EMAIL = "email"
TYPE_APPOINTMENT_CONFIRMATION = "appointment_confirmation"
_RECIPIENT_MAX = 255

_TERMINAL_SKIP_STATUSES = frozenset({"sent", "delivered", "read", "skipped"})
_ACTIVE_SKIP_STATUSES = frozenset(_TERMINAL_SKIP_STATUSES | {"pending", "failed"})


def _whatsapp_enabled() -> bool:
    return bool(current_app.config.get("WHATSAPP_NOTIFICATIONS_ENABLED"))


def _email_enabled() -> bool:
    return bool(current_app.config.get("EMAIL_NOTIFICATIONS_ENABLED"))


def _employee_display_name(appointment: Appointment) -> str:
    emp = appointment.employee
    if emp is None:
        return "—"
    if emp.display_name:
        return emp.display_name.strip()
    user = getattr(emp, "user", None)
    if user and user.email:
        return user.email.split("@")[0]
    return "—"


def _customer_first_name(appointment: Appointment) -> str:
    name = (appointment.client_name or "").strip()
    if not name:
        client = appointment.client
        if client:
            return (client.first_name or "Cliente").strip()
        return "Cliente"
    return name.split()[0]


def _format_appointment_date(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    return dt.strftime("%d/%m/%Y")


def _format_appointment_time(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    return dt.strftime("%H:%M")


def _resolve_country_code(appointment: Appointment) -> str | None:
    business = appointment.business
    if business and business.country_code:
        code = str(business.country_code).strip().upper()
        if len(code) == 2:
            return code
    default = current_app.config.get("DEFAULT_PHONE_COUNTRY_CODE") or ""
    default = str(default).strip().upper()
    return default if len(default) == 2 else None


def _resolve_customer_email(appointment: Appointment) -> str | None:
    candidates = [
        (appointment.client_email or "").strip(),
        (appointment.client.email if appointment.client else "") or "",
    ]
    for raw in candidates:
        text = str(raw).strip()
        if is_valid_email(text):
            return text
    return None


def _get_or_create_log(
    appointment: Appointment,
    *,
    channel: str,
    provider: str | None = None,
) -> tuple[NotificationLog | None, bool]:
    """
    Returns (log, should_send).
    should_send is False when an existing record already consumed the idempotency slot.
    """
    existing = (
        NotificationLog.query.filter_by(
            appointment_id=appointment.id,
            channel=channel,
            notification_type=TYPE_APPOINTMENT_CONFIRMATION,
        ).first()
    )
    if existing:
        return existing, existing.status not in _ACTIVE_SKIP_STATUSES

    log = NotificationLog(
        business_id=appointment.business_id,
        appointment_id=appointment.id,
        client_id=appointment.client_id,
        channel=channel,
        notification_type=TYPE_APPOINTMENT_CONFIRMATION,
        provider=provider,
        status="pending",
        attempt_count=0,
    )
    db.session.add(log)
    try:
        db.session.commit()
        return log, True
    except IntegrityError:
        db.session.rollback()
        existing = (
            NotificationLog.query.filter_by(
                appointment_id=appointment.id,
                channel=channel,
                notification_type=TYPE_APPOINTMENT_CONFIRMATION,
            ).first()
        )
        if existing:
            return existing, False
        logger.exception("Failed to create notification log after integrity error")
        return None, False


def _clip_recipient(value: str | None) -> str | None:
    if not value:
        return None
    return value[:_RECIPIENT_MAX]


def _mark_skipped(
    log: NotificationLog,
    *,
    error_code: str,
    error_message: str,
    recipient: str | None = None,
) -> str:
    log.status = "skipped"
    log.attempt_count = (log.attempt_count or 0) + 1
    log.error_code = error_code[:40]
    log.error_message = error_message[:500]
    if recipient:
        log.recipient = _clip_recipient(recipient)
    log.updated_at = datetime.utcnow()
    db.session.commit()
    return "skipped"


def _mark_failed(
    log: NotificationLog,
    *,
    error_code: str,
    error_message: str,
    recipient: str | None = None,
) -> str:
    log.status = "failed"
    log.attempt_count = (log.attempt_count or 0) + 1
    log.error_code = error_code[:40]
    log.error_message = error_message[:500]
    if recipient:
        log.recipient = _clip_recipient(recipient)
    log.updated_at = datetime.utcnow()
    db.session.commit()
    return "failed"


def _mark_sent(
    log: NotificationLog,
    *,
    recipient: str,
    message_sid: str | None,
    template_identifier: str,
) -> str:
    log.status = "sent"
    log.attempt_count = (log.attempt_count or 0) + 1
    log.recipient = _clip_recipient(recipient)
    log.provider_message_sid = (message_sid or "")[:64] or None
    log.template_identifier = template_identifier[:120]
    log.sent_at = datetime.utcnow()
    log.error_code = None
    log.error_message = None
    log.updated_at = datetime.utcnow()
    db.session.commit()
    return "sent"


def send_appointment_email_confirmation(appointment: Appointment) -> dict[str, Any]:
    """
    Send an email confirmation when the appointment has a valid customer email.
    Never raises. Returns {"status": "sent"|"failed"|"skipped"}.
    """
    try:
        if appointment.status in {"canceled", "cancelled"}:
            return {"status": "skipped"}

        log, should_send = _get_or_create_log(
            appointment,
            channel=CHANNEL_EMAIL,
            provider=EMAIL_PROVIDER_NAME,
        )
        if log is None:
            return {"status": "failed"}
        if not should_send:
            return {
                "status": log.status
                if log.status in _TERMINAL_SKIP_STATUSES | {"failed"}
                else "skipped"
            }

        to_email = _resolve_customer_email(appointment)
        if not to_email:
            status = _mark_skipped(
                log,
                error_code="missing_email",
                error_message="No customer email on appointment or client.",
            )
            return {"status": status}

        if not _email_enabled():
            if not getattr(send_appointment_email_confirmation, "_warned_disabled", False):
                logger.info("Email notifications are disabled (EMAIL_NOTIFICATIONS_ENABLED).")
                send_appointment_email_confirmation._warned_disabled = True  # type: ignore[attr-defined]
            status = _mark_skipped(
                log,
                error_code="disabled",
                error_message="Email notifications are disabled.",
                recipient=to_email,
            )
            return {"status": status}

        if not email_configured():
            if not getattr(send_appointment_email_confirmation, "_warned_config", False):
                logger.warning("Email notifications enabled but SMTP is incomplete.")
                send_appointment_email_confirmation._warned_config = True  # type: ignore[attr-defined]
            status = _mark_skipped(
                log,
                error_code="not_configured",
                error_message="Email SMTP is not fully configured.",
                recipient=to_email,
            )
            return {"status": status}

        business = appointment.business
        service = appointment.service_type
        if business is None or service is None:
            status = _mark_failed(
                log,
                error_code="missing_tenant_data",
                error_message="Appointment is missing business or service data.",
                recipient=to_email,
            )
            return {"status": status}

        subject, text_body, html_body = build_appointment_confirmation_email(
            customer_name=_customer_first_name(appointment),
            shop_name=business.name,
            service_name=service.name,
            barber_name=_employee_display_name(appointment),
            appointment_date=_format_appointment_date(appointment.start_time),
            appointment_time=_format_appointment_time(appointment.start_time),
            shop_phone=business.phone,
            shop_address=business.address,
        )

        send_result = send_email(
            to_email=to_email,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
        )

        if send_result.ok:
            status = _mark_sent(
                log,
                recipient=to_email,
                message_sid=send_result.message_id,
                template_identifier="appointment_confirmation_email",
            )
            return {"status": status}

        status = _mark_failed(
            log,
            error_code=send_result.error_code or "send_failed",
            error_message=send_result.error_message or "Email send failed.",
            recipient=to_email,
        )
        return {"status": status}
    except Exception:
        logger.exception(
            "Unexpected error sending appointment email confirmation",
            extra={"appointment_id": str(getattr(appointment, "id", ""))},
        )
        try:
            db.session.rollback()
        except Exception:
            pass
        return {"status": "failed"}


def send_appointment_confirmation(appointment: Appointment) -> dict[str, Any]:
    """
    Send a WhatsApp appointment confirmation after successful commit.
    Never raises. Returns {"status": "sent"|"failed"|"skipped"|"pending"}.
    """
    try:
        if appointment.status in {"canceled", "cancelled"}:
            return {"status": "skipped"}

        log, should_send = _get_or_create_log(
            appointment,
            channel=CHANNEL_WHATSAPP,
            provider=WHATSAPP_PROVIDER_NAME,
        )
        if log is None:
            return {"status": "failed"}
        if not should_send:
            return {
                "status": log.status
                if log.status in _TERMINAL_SKIP_STATUSES | {"failed"}
                else "skipped"
            }

        if not _whatsapp_enabled():
            if not getattr(send_appointment_confirmation, "_warned_disabled", False):
                logger.info("WhatsApp notifications are disabled (WHATSAPP_NOTIFICATIONS_ENABLED).")
                send_appointment_confirmation._warned_disabled = True  # type: ignore[attr-defined]
            status = _mark_skipped(
                log,
                error_code="disabled",
                error_message="WhatsApp notifications are disabled.",
            )
            return {"status": status}

        if not twilio_configured():
            if not getattr(send_appointment_confirmation, "_warned_config", False):
                logger.warning("WhatsApp notifications enabled but Twilio credentials are incomplete.")
                send_appointment_confirmation._warned_config = True  # type: ignore[attr-defined]
            status = _mark_skipped(
                log,
                error_code="not_configured",
                error_message="Twilio WhatsApp is not fully configured.",
            )
            return {"status": status}

        business = appointment.business
        service = appointment.service_type
        if business is None or service is None:
            status = _mark_failed(
                log,
                error_code="missing_tenant_data",
                error_message="Appointment is missing business or service data.",
            )
            return {"status": status}

        phone_result = normalize_phone_for_whatsapp(
            appointment.client_phone,
            country_code=_resolve_country_code(appointment),
            default_country_code=current_app.config.get("DEFAULT_PHONE_COUNTRY_CODE"),
        )
        if not phone_result.ok or not phone_result.whatsapp_to:
            status = _mark_skipped(
                log,
                error_code=phone_result.error or "invalid_phone",
                error_message="Customer phone number is missing or invalid.",
            )
            return {"status": status}

        content_sid = current_app.config.get("TWILIO_WHATSAPP_CONTENT_SID") or ""
        variables = build_appointment_confirmation_variables(
            customer_name=_customer_first_name(appointment),
            shop_name=business.name,
            service_name=service.name,
            barber_name=_employee_display_name(appointment),
            appointment_date=_format_appointment_date(appointment.start_time),
            appointment_time=_format_appointment_time(appointment.start_time),
        )

        send_result = send_whatsapp_template(
            to_whatsapp=phone_result.whatsapp_to,
            content_sid=content_sid,
            content_variables=variables,
        )

        if send_result.ok:
            status = _mark_sent(
                log,
                recipient=phone_result.e164 or phone_result.whatsapp_to,
                message_sid=send_result.message_sid,
                template_identifier=content_sid,
            )
            return {"status": status}

        status = _mark_failed(
            log,
            error_code=send_result.error_code or "send_failed",
            error_message=send_result.error_message or "WhatsApp send failed.",
            recipient=phone_result.e164,
        )
        return {"status": status}
    except Exception:
        logger.exception(
            "Unexpected error sending appointment confirmation",
            extra={"appointment_id": str(getattr(appointment, "id", ""))},
        )
        try:
            db.session.rollback()
        except Exception:
            pass
        return {"status": "failed"}


def notify_appointment_created(appointment: Appointment) -> dict[str, Any]:
    """
    Fan-out notifications after appointment create.
    Email runs when a valid email is present; WhatsApp is opt-in via env.
    """
    email_result = send_appointment_email_confirmation(appointment)
    if _whatsapp_enabled():
        whatsapp_result = send_appointment_confirmation(appointment)
    else:
        whatsapp_result = {"status": "skipped"}
    return {
        "email": email_result.get("status"),
        "whatsapp": whatsapp_result.get("status"),
        # Backward compatible single status (prefer email when it ran).
        "status": email_result.get("status")
        if email_result.get("status") != "skipped"
        else whatsapp_result.get("status"),
    }
