"""Twilio delivery-status webhooks for notification_log updates."""

from __future__ import annotations

import logging
from datetime import datetime

from flask import Blueprint, current_app, request
from twilio.request_validator import RequestValidator

from app.extensions import db
from app.models import NotificationLog
from app.whatsapp_provider import map_twilio_status_to_notification_status

logger = logging.getLogger(__name__)

notification_webhooks = Blueprint(
    "notification_webhooks",
    __name__,
    url_prefix="/api/webhooks/twilio",
)

_STATUS_RANK = {
    "pending": 0,
    "sent": 1,
    "delivered": 2,
    "read": 3,
    "failed": 4,
    "skipped": 5,
}


def _validate_twilio_request() -> bool:
    auth_token = current_app.config.get("TWILIO_AUTH_TOKEN")
    if not auth_token:
        logger.warning("Twilio webhook rejected: TWILIO_AUTH_TOKEN is not configured.")
        return False
    validator = RequestValidator(auth_token)
    signature = request.headers.get("X-Twilio-Signature", "")
    url = request.url
    # Behind reverse proxies, configure PUBLIC_WEBHOOK_URL if needed in future.
    params = request.form.to_dict(flat=True)
    return validator.validate(url, params, signature)


@notification_webhooks.route("/whatsapp-status", methods=["POST"])
def twilio_whatsapp_status():
    if not _validate_twilio_request():
        return {"error": "Unauthorized"}, 403

    message_sid = (request.form.get("MessageSid") or "").strip()
    message_status = (request.form.get("MessageStatus") or "").strip()
    if not message_sid:
        return {"error": "MessageSid required"}, 400

    mapped = map_twilio_status_to_notification_status(message_status)
    if mapped is None:
        return {"status": "ignored"}, 200

    log = NotificationLog.query.filter_by(provider_message_sid=message_sid).first()
    if not log:
        logger.info("Twilio webhook for unknown MessageSid", extra={"message_sid": message_sid[:20]})
        return {"status": "not_found"}, 200

    current_rank = _STATUS_RANK.get(log.status, 0)
    new_rank = _STATUS_RANK.get(mapped, 0)
    if mapped == "failed" or new_rank >= current_rank:
        log.status = mapped
        if mapped == "failed":
            log.error_code = (request.form.get("ErrorCode") or "delivery_failed")[:40]
            error_msg = request.form.get("ErrorMessage") or "Message delivery failed."
            log.error_message = str(error_msg)[:500]
        if mapped in {"sent", "delivered", "read"} and log.sent_at is None:
            log.sent_at = datetime.utcnow()
        log.updated_at = datetime.utcnow()
        db.session.commit()

    return {"status": "ok"}, 200
