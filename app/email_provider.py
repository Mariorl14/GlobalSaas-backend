"""SMTP email provider for appointment confirmations."""

from __future__ import annotations

import logging
import re
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage

from flask import current_app

logger = logging.getLogger(__name__)

PROVIDER_NAME = "smtp"
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MAX_ERROR_LEN = 500


@dataclass(frozen=True)
class EmailSendResult:
    ok: bool
    message_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None


def _sanitize_error_message(message: str | None) -> str | None:
    if not message:
        return None
    text = " ".join(str(message).split())
    if len(text) > _MAX_ERROR_LEN:
        return text[:_MAX_ERROR_LEN] + "…"
    return text


def is_valid_email(value: str | None) -> bool:
    if not value:
        return False
    text = value.strip()
    return bool(text) and text != "—" and len(text) <= 254 and bool(_EMAIL_RE.match(text))


def email_configured() -> bool:
    cfg = current_app.config
    return bool(
        cfg.get("MAIL_SERVER")
        and cfg.get("MAIL_DEFAULT_SENDER")
        and cfg.get("MAIL_PORT")
    )


def build_appointment_confirmation_email(
    *,
    customer_name: str,
    shop_name: str,
    service_name: str,
    barber_name: str,
    appointment_date: str,
    appointment_time: str,
    shop_phone: str | None = None,
    shop_address: str | None = None,
) -> tuple[str, str, str]:
    """Returns (subject, text_body, html_body)."""
    subject = f"Confirmación de cita — {shop_name}"
    lines = [
        f"Hola {customer_name},",
        "",
        f"Tu cita en {shop_name} quedó confirmada.",
        "",
        f"Servicio: {service_name}",
        f"Con: {barber_name}",
        f"Fecha: {appointment_date}",
        f"Hora: {appointment_time}",
    ]
    if shop_address:
        lines.extend(["", f"Dirección: {shop_address}"])
    if shop_phone:
        lines.append(f"Teléfono: {shop_phone}")
    lines.extend(
        [
            "",
            "Si necesitas cambiar o cancelar, responde a este correo o contacta el negocio.",
            "",
            "¡Te esperamos!",
        ]
    )
    text_body = "\n".join(lines)

    detail_rows = [
        ("Servicio", service_name),
        ("Con", barber_name),
        ("Fecha", appointment_date),
        ("Hora", appointment_time),
    ]
    if shop_address:
        detail_rows.append(("Dirección", shop_address))
    if shop_phone:
        detail_rows.append(("Teléfono", shop_phone))

    rows_html = "".join(
        f"<tr><td style='padding:6px 12px 6px 0;color:#64748b;'>{k}</td>"
        f"<td style='padding:6px 0;color:#0f172a;font-weight:600;'>{v}</td></tr>"
        for k, v in detail_rows
    )
    html_body = f"""\
<html>
  <body style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:#f8fafc;padding:24px;">
    <div style="max-width:520px;margin:0 auto;background:#ffffff;border-radius:12px;padding:28px;border:1px solid #e2e8f0;">
      <p style="margin:0 0 8px;color:#0f172a;font-size:18px;font-weight:700;">Hola {customer_name},</p>
      <p style="margin:0 0 20px;color:#334155;font-size:15px;">
        Tu cita en <strong>{shop_name}</strong> quedó confirmada.
      </p>
      <table style="border-collapse:collapse;width:100%;font-size:14px;margin-bottom:20px;">
        {rows_html}
      </table>
      <p style="margin:0;color:#64748b;font-size:13px;">
        Si necesitas cambiar o cancelar, responde a este correo o contacta el negocio.
      </p>
    </div>
  </body>
</html>
"""
    return subject, text_body, html_body


def send_email(
    *,
    to_email: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
) -> EmailSendResult:
    """Send an email via SMTP. Never raises."""
    if not email_configured():
        return EmailSendResult(
            ok=False,
            error_code="not_configured",
            error_message="Email SMTP is not configured.",
        )
    if not is_valid_email(to_email):
        return EmailSendResult(
            ok=False,
            error_code="invalid_email",
            error_message="Recipient email is invalid.",
        )

    cfg = current_app.config
    sender = cfg["MAIL_DEFAULT_SENDER"]
    sender_name = (cfg.get("MAIL_DEFAULT_SENDER_NAME") or "").strip()
    from_header = f"{sender_name} <{sender}>" if sender_name else sender

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_header
    msg["To"] = to_email.strip()
    msg.set_content(text_body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    host = cfg["MAIL_SERVER"]
    port = int(cfg.get("MAIL_PORT") or 587)
    use_tls = bool(cfg.get("MAIL_USE_TLS", True))
    use_ssl = bool(cfg.get("MAIL_USE_SSL", False))
    username = cfg.get("MAIL_USERNAME") or None
    password = cfg.get("MAIL_PASSWORD") or None
    timeout = int(cfg.get("MAIL_TIMEOUT", 15))

    try:
        if use_ssl:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, timeout=timeout, context=context) as smtp:
                if username:
                    smtp.login(username, password or "")
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=timeout) as smtp:
                smtp.ehlo()
                if use_tls:
                    context = ssl.create_default_context()
                    smtp.starttls(context=context)
                    smtp.ehlo()
                if username:
                    smtp.login(username, password or "")
                smtp.send_message(msg)
        return EmailSendResult(ok=True, message_id=msg.get("Message-ID"))
    except smtplib.SMTPAuthenticationError as exc:
        logger.warning("SMTP auth failed", extra={"error": str(exc)})
        return EmailSendResult(
            ok=False,
            error_code="smtp_auth",
            error_message=_sanitize_error_message(str(exc)),
        )
    except smtplib.SMTPException as exc:
        logger.warning("SMTP send failed", extra={"error": str(exc)})
        return EmailSendResult(
            ok=False,
            error_code="smtp_error",
            error_message=_sanitize_error_message(str(exc)),
        )
    except Exception as exc:
        logger.exception("Unexpected email send error")
        return EmailSendResult(
            ok=False,
            error_code="provider_error",
            error_message=_sanitize_error_message(str(exc)),
        )
