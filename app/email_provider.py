"""Email provider for appointment confirmations.

Uses Resend HTTPS API when RESEND_API_KEY is set (required on Render free tier,
which blocks outbound SMTP ports 25/465/587). Falls back to SMTP otherwise
(Gmail app password works fine locally / on paid hosts).
"""

from __future__ import annotations

import html
import json
import logging
import re
import smtplib
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage

from flask import current_app

logger = logging.getLogger(__name__)

PROVIDER_SMTP = "smtp"
PROVIDER_RESEND = "resend"
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MAX_ERROR_LEN = 500
_RESEND_URL = "https://api.resend.com/emails"


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


def _resend_api_key() -> str | None:
    key = (current_app.config.get("RESEND_API_KEY") or "").strip()
    return key or None


def get_provider_name() -> str:
    return PROVIDER_RESEND if _resend_api_key() else PROVIDER_SMTP


# Back-compat for imports that expect PROVIDER_NAME
PROVIDER_NAME = PROVIDER_SMTP


def email_configured() -> bool:
    cfg = current_app.config
    sender = cfg.get("MAIL_DEFAULT_SENDER")
    if not sender:
        return False
    if _resend_api_key():
        return True
    return bool(cfg.get("MAIL_SERVER") and cfg.get("MAIL_PORT"))


def _from_header() -> str:
    cfg = current_app.config
    sender = cfg["MAIL_DEFAULT_SENDER"]
    sender_name = (cfg.get("MAIL_DEFAULT_SENDER_NAME") or "").strip()
    return f"{sender_name} <{sender}>" if sender_name else sender


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


def build_appointment_staff_alert_email(
    *,
    staff_name: str,
    shop_name: str,
    customer_name: str,
    service_name: str,
    appointment_date: str,
    appointment_time: str,
    customer_phone: str | None = None,
    customer_email: str | None = None,
) -> tuple[str, str, str]:
    """Staff notification when a new booking is assigned to them. Returns (subject, text, html)."""
    subject = f"Nueva cita asignada — {shop_name}"
    lines = [
        f"Hola {staff_name},",
        "",
        f"Tienes una nueva cita en {shop_name}.",
        "",
        f"Cliente: {customer_name}",
        f"Servicio: {service_name}",
        f"Fecha: {appointment_date}",
        f"Hora: {appointment_time}",
    ]
    if customer_phone:
        lines.append(f"Teléfono del cliente: {customer_phone}")
    if customer_email and is_valid_email(customer_email):
        lines.append(f"Correo del cliente: {customer_email}")
    lines.extend(
        [
            "",
            "Revisa el portal del negocio para más detalles.",
            "",
            "— Barber Suite",
        ]
    )
    text_body = "\n".join(lines)

    detail_rows = [
        ("Cliente", customer_name),
        ("Servicio", service_name),
        ("Fecha", appointment_date),
        ("Hora", appointment_time),
    ]
    if customer_phone:
        detail_rows.append(("Teléfono", customer_phone))
    if customer_email and is_valid_email(customer_email):
        detail_rows.append(("Correo", customer_email))

    rows_html = "".join(
        f"<tr><td style='padding:6px 12px 6px 0;color:#64748b;'>{k}</td>"
        f"<td style='padding:6px 0;color:#0f172a;font-weight:600;'>{v}</td></tr>"
        for k, v in detail_rows
    )
    html_body = f"""\
<html>
  <body style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:#f8fafc;padding:24px;">
    <div style="max-width:520px;margin:0 auto;background:#ffffff;border-radius:12px;padding:28px;border:1px solid #e2e8f0;">
      <p style="margin:0 0 8px;color:#0f172a;font-size:18px;font-weight:700;">Hola {staff_name},</p>
      <p style="margin:0 0 20px;color:#334155;font-size:15px;">
        Tienes una nueva cita en <strong>{shop_name}</strong>.
      </p>
      <table style="border-collapse:collapse;width:100%;font-size:14px;margin-bottom:20px;">
        {rows_html}
      </table>
      <p style="margin:0;color:#64748b;font-size:13px;">
        Revisa el portal del negocio para más detalles.
      </p>
    </div>
  </body>
</html>
"""
    return subject, text_body, html_body


def _esc(value: str) -> str:
    return html.escape(value or "", quote=True)


def _detail_rows_html(rows: list[tuple[str, str]]) -> str:
    return "".join(
        f"<tr><td style='padding:6px 12px 6px 0;color:#64748b;vertical-align:top;'>{_esc(k)}</td>"
        f"<td style='padding:6px 0;color:#0f172a;font-weight:600;'>{_esc(v)}</td></tr>"
        for k, v in rows
        if v
    )


def _email_shell(*, greeting: str, intro_html: str, rows_html: str, extra_html: str = "") -> str:
    return f"""\
<html>
  <body style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:#f8fafc;padding:24px;">
    <div style="max-width:520px;margin:0 auto;background:#ffffff;border-radius:12px;padding:28px;border:1px solid #e2e8f0;">
      <p style="margin:0 0 8px;color:#0f172a;font-size:18px;font-weight:700;">{greeting}</p>
      <p style="margin:0 0 20px;color:#334155;font-size:15px;">{intro_html}</p>
      <table style="border-collapse:collapse;width:100%;font-size:14px;margin-bottom:20px;">
        {rows_html}
      </table>
      {extra_html}
    </div>
  </body>
</html>
"""


def build_appointment_reschedule_email(
    *,
    customer_name: str,
    shop_name: str,
    service_name: str,
    barber_name: str,
    original_when: str,
    proposed_when: str,
    accept_url: str,
    business_message: str | None = None,
) -> tuple[str, str, str]:
    subject = f"Cambio de horario — {shop_name}"
    lines = [
        f"Hola {customer_name},",
        "",
        f"Necesitamos hacer un pequeño ajuste a tu cita en {shop_name}.",
        "",
        f"Cita original: {original_when}",
        f"Nuevo horario propuesto: {proposed_when}",
        f"Servicio: {service_name}",
        f"Barbero: {barber_name}",
    ]
    if business_message:
        lines.extend(["", "Mensaje del negocio:", f'"{business_message}"'])
    lines.extend(
        [
            "",
            "Si el nuevo horario te funciona, confirma aquí:",
            accept_url,
            "",
            "Si no te funciona, contacta el negocio para elegir otro horario.",
            "",
            "— Barber Suite",
        ]
    )
    text_body = "\n".join(lines)
    rows_html = _detail_rows_html(
        [
            ("Cita original", original_when),
            ("Nuevo horario", proposed_when),
            ("Servicio", service_name),
            ("Barbero", barber_name),
        ]
    )
    message_html = ""
    if business_message:
        message_html = (
            "<p style='margin:0 0 16px;color:#334155;font-size:14px;'>"
            "Mensaje del negocio:<br>"
            f"<em>“{_esc(business_message)}”</em></p>"
        )
    cta = (
        f"<p style='margin:0 0 16px;'>"
        f"<a href='{_esc(accept_url)}' style='display:inline-block;background:#0f172a;"
        f"color:#ffffff;text-decoration:none;padding:12px 18px;border-radius:8px;"
        f"font-weight:600;font-size:14px;'>Aceptar nuevo horario</a></p>"
        "<p style='margin:0;color:#64748b;font-size:13px;'>"
        "Si el horario propuesto no te funciona, contacta el negocio.</p>"
    )
    html_body = _email_shell(
        greeting=f"Hola {_esc(customer_name)},",
        intro_html=f"Necesitamos hacer un pequeño ajuste a tu cita en <strong>{_esc(shop_name)}</strong>.",
        rows_html=rows_html,
        extra_html=message_html + cta,
    )
    return subject, text_body, html_body


def build_appointment_cancellation_email(
    *,
    customer_name: str,
    shop_name: str,
    service_name: str,
    barber_name: str,
    appointment_when: str,
    reason_text: str | None = None,
    booking_url: str | None = None,
) -> tuple[str, str, str]:
    subject = f"Cita cancelada — {shop_name}"
    lines = [
        f"Hola {customer_name},",
        "",
        f"Lamentablemente tu cita en {shop_name} ha sido cancelada.",
        "",
        f"Cita: {appointment_when}",
        f"Servicio: {service_name}",
        f"Barbero: {barber_name}",
    ]
    if reason_text:
        lines.extend(["", f"Motivo: {reason_text}"])
    lines.extend(["", "Pedimos disculpas por las molestias."])
    if booking_url:
        lines.extend(["", "Puedes reservar otra cita aquí:", booking_url])
    lines.extend(["", "— Barber Suite"])
    text_body = "\n".join(lines)
    rows_html = _detail_rows_html(
        [
            ("Cita", appointment_when),
            ("Servicio", service_name),
            ("Barbero", barber_name),
        ]
    )
    extra = ""
    if reason_text:
        extra += (
            "<p style='margin:0 0 16px;color:#334155;font-size:14px;'>"
            f"Motivo: {_esc(reason_text)}</p>"
        )
    extra += (
        "<p style='margin:0 0 16px;color:#64748b;font-size:13px;'>"
        "Pedimos disculpas por las molestias.</p>"
    )
    if booking_url:
        extra += (
            f"<p style='margin:0;'>"
            f"<a href='{_esc(booking_url)}' style='display:inline-block;background:#0f172a;"
            f"color:#ffffff;text-decoration:none;padding:12px 18px;border-radius:8px;"
            f"font-weight:600;font-size:14px;'>Reservar otra cita</a></p>"
        )
    html_body = _email_shell(
        greeting=f"Hola {_esc(customer_name)},",
        intro_html=(
            f"Lamentablemente tu cita en <strong>{_esc(shop_name)}</strong> ha sido cancelada."
        ),
        rows_html=rows_html,
        extra_html=extra,
    )
    return subject, text_body, html_body


def _send_via_resend(
    *,
    to_email: str,
    subject: str,
    text_body: str,
    html_body: str | None,
) -> EmailSendResult:
    api_key = _resend_api_key()
    if not api_key:
        return EmailSendResult(
            ok=False,
            error_code="not_configured",
            error_message="RESEND_API_KEY is not set.",
        )

    cfg = current_app.config
    timeout = int(cfg.get("MAIL_TIMEOUT", 15))
    payload: dict = {
        "from": _from_header(),
        "to": [to_email.strip()],
        "subject": subject,
        "text": text_body,
    }
    if html_body:
        payload["html"] = html_body

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        _RESEND_URL,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "BarberSuite/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8") or "{}")
            return EmailSendResult(ok=True, message_id=body.get("id"))
    except urllib.error.HTTPError as exc:
        raw = ""
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        logger.warning(
            "Resend API failed",
            extra={"status": exc.code, "error": raw[:300]},
        )
        return EmailSendResult(
            ok=False,
            error_code="resend_http",
            error_message=_sanitize_error_message(f"HTTP {exc.code}: {raw}"),
        )
    except Exception as exc:
        logger.exception("Unexpected Resend send error")
        return EmailSendResult(
            ok=False,
            error_code="provider_error",
            error_message=_sanitize_error_message(str(exc)),
        )


def _send_via_smtp(
    *,
    to_email: str,
    subject: str,
    text_body: str,
    html_body: str | None,
) -> EmailSendResult:
    cfg = current_app.config
    from_header = _from_header()

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


def send_email(
    *,
    to_email: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
) -> EmailSendResult:
    """Send an email via Resend (HTTPS) or SMTP. Never raises."""
    if not email_configured():
        return EmailSendResult(
            ok=False,
            error_code="not_configured",
            error_message="Email is not configured (set RESEND_API_KEY or SMTP).",
        )
    if not is_valid_email(to_email):
        return EmailSendResult(
            ok=False,
            error_code="invalid_email",
            error_message="Recipient email is invalid.",
        )

    if _resend_api_key():
        return _send_via_resend(
            to_email=to_email,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
        )
    return _send_via_smtp(
        to_email=to_email,
        subject=subject,
        text_body=text_body,
        html_body=html_body,
    )
