"""Reschedule proposals and business-initiated cancellation.

Keeps original start/end until the customer accepts. Notifications are sent
by the caller after commit so a mail failure never rolls back the appointment.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Any

from app.models import Appointment, ServiceType
from app.public_booking import employee_slot_conflicts

RESCHEDULE_PENDING = "reschedule_pending"
TOKEN_TTL = timedelta(days=7)
TOKEN_BYTES = 32

CANCEL_REASONS = frozenset(
    {
        "barber_unavailable",
        "business_closed",
        "scheduling_conflict",
        "customer_requested",
        "other",
    }
)

_BLOCK_RESCHEDULE = frozenset({"completed", "canceled", "cancelled", "no_show"})
_BLOCK_CANCEL = frozenset({"completed", "canceled", "cancelled"})

SLOT_UNAVAILABLE_MSG = (
    "Ese horario ya no está disponible. Elige otro o contacta el negocio."
)
PUBLIC_SLOT_UNAVAILABLE_MSG = (
    "Lamentablemente este horario ya no está disponible. "
    "Contacta el negocio para elegir otro."
)
TOKEN_INVALID_MSG = "Este enlace ya no es válido. Contacta el negocio si necesitas ayuda."


class LifecycleError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)[:64]


def clear_reschedule_fields(appointment: Appointment) -> None:
    appointment.proposed_start_time = None
    appointment.proposed_end_time = None
    appointment.reschedule_token = None
    appointment.reschedule_token_expires_at = None
    appointment.reschedule_message = None


def _service_duration_minutes(appointment: Appointment) -> int:
    st = appointment.service_type
    if st is None:
        st = ServiceType.query.get(appointment.service_type_id)
    if st is None or int(st.duration or 0) <= 0:
        raise LifecycleError("Servicio con duración inválida.", 400)
    return int(st.duration)


def _assert_slot_free(
    appointment: Appointment,
    start: datetime,
    end: datetime,
    *,
    public: bool = False,
) -> None:
    if employee_slot_conflicts(
        appointment.business_id,
        appointment.employee_id,
        start,
        end,
        exclude_id=appointment.id,
    ):
        raise LifecycleError(
            PUBLIC_SLOT_UNAVAILABLE_MSG if public else SLOT_UNAVAILABLE_MSG,
            409,
        )


def propose_reschedule(
    appointment: Appointment,
    *,
    new_start: datetime,
    new_end: datetime | None = None,
    message: str | None = None,
) -> Appointment:
    status = (appointment.status or "").strip().lower()
    if status in _BLOCK_RESCHEDULE:
        raise LifecycleError("No se puede reprogramar esta cita.", 400)

    if new_end is None:
        new_end = new_start + timedelta(minutes=_service_duration_minutes(appointment))
    if new_end <= new_start:
        raise LifecycleError("La hora de fin debe ser posterior al inicio.", 400)
    if appointment.start_time == new_start and appointment.end_time == new_end:
        raise LifecycleError("El nuevo horario es igual al actual.", 400)

    _assert_slot_free(appointment, new_start, new_end)

    appointment.previous_start_time = appointment.start_time
    appointment.previous_end_time = appointment.end_time
    appointment.proposed_start_time = new_start
    appointment.proposed_end_time = new_end
    appointment.reschedule_message = (message or "").strip()[:2000] or None
    appointment.reschedule_token = _new_token()
    appointment.reschedule_token_expires_at = datetime.utcnow() + TOKEN_TTL
    appointment.status = RESCHEDULE_PENDING
    return appointment


def find_reschedule_by_token(token: str) -> Appointment | None:
    raw = (token or "").strip()
    if not raw or len(raw) < 16:
        return None
    return Appointment.query.filter_by(reschedule_token=raw).first()


def reschedule_preview(appointment: Appointment) -> dict[str, Any]:
    return {
        "status": "pending",
        "business_name": appointment.business.name if appointment.business else "",
        "customer_name": (appointment.client_name or "").split()[0] or "Cliente",
        "service_name": appointment.service_type.name if appointment.service_type else "",
        "barber_name": _barber_label(appointment),
        "original_start_time": (
            appointment.previous_start_time or appointment.start_time
        ).isoformat()
        if (appointment.previous_start_time or appointment.start_time)
        else None,
        "proposed_start_time": (
            appointment.proposed_start_time.isoformat()
            if appointment.proposed_start_time
            else None
        ),
        "message": appointment.reschedule_message,
    }


def _barber_label(appointment: Appointment) -> str:
    from app.name_utils import staff_display_label

    emp = appointment.employee
    return staff_display_label(emp) if emp else "—"


def reschedule_token_error(appointment: Appointment) -> str | None:
    status = (appointment.status or "").strip().lower()
    if status in {"canceled", "cancelled"}:
        return TOKEN_INVALID_MSG
    if status != RESCHEDULE_PENDING:
        return TOKEN_INVALID_MSG
    if not appointment.proposed_start_time or not appointment.proposed_end_time:
        return TOKEN_INVALID_MSG
    expires = appointment.reschedule_token_expires_at
    if expires and expires < datetime.utcnow():
        return TOKEN_INVALID_MSG
    return None


def accept_reschedule(appointment: Appointment) -> Appointment:
    err = reschedule_token_error(appointment)
    if err:
        raise LifecycleError(err, 400)

    new_start = appointment.proposed_start_time
    new_end = appointment.proposed_end_time
    assert new_start is not None and new_end is not None
    _assert_slot_free(appointment, new_start, new_end, public=True)

    appointment.start_time = new_start
    appointment.end_time = new_end
    appointment.status = "confirmed"
    clear_reschedule_fields(appointment)
    return appointment


def cancel_appointment(
    appointment: Appointment,
    *,
    reason: str | None = None,
    message: str | None = None,
) -> Appointment:
    status = (appointment.status or "").strip().lower()
    if status == "completed":
        raise LifecycleError("No se puede cancelar una cita completada.", 400)
    if status in _BLOCK_CANCEL:
        raise LifecycleError("Esta cita ya está cancelada.", 400)

    code = (reason or "").strip().lower() or None
    if code and code not in CANCEL_REASONS:
        raise LifecycleError("Motivo de cancelación inválido.", 400)

    appointment.status = "canceled"
    appointment.cancel_reason = code
    appointment.cancel_message = (message or "").strip()[:2000] or None
    clear_reschedule_fields(appointment)
    return appointment
