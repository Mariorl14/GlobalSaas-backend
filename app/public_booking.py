"""
Public (unauthenticated) booking API for clients.
All routes scoped by business.public_slug — no cross-tenant leakage.
"""

from __future__ import annotations

import json
import re
import uuid
from calendar import monthrange
from datetime import date, datetime, time, timedelta
from typing import Any

from flask import Blueprint, jsonify, request
from flask_jwt_extended import create_access_token, verify_jwt_in_request
from sqlalchemy import and_, func, text
from werkzeug.security import check_password_hash, generate_password_hash

from app.customer_auth import CUSTOMER_ROLE, get_customer_context
from app.extensions import db
from app.models import Appointment, Business, Client, Employee, ServiceType
from app.appointment_notifications import notify_appointment_created
from app.name_utils import staff_display_label

public_booking = Blueprint("public_booking", __name__, url_prefix="/api/public")

BLOCKING_STATUSES = frozenset({"scheduled", "confirmed", "completed", "pending"})
SLOT_STEP_MINUTES = 15
_MAX_NOTES_LEN = 4000
_PHONE_MIN_LEN = 6
_NAME_MAX = 80
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _json_error(message: str, status_code: int = 400):
    return jsonify({"error": message}), status_code


def _parse_uuid(value):
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        s = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _normalize_booking_datetimes(start: datetime, end: datetime) -> tuple[datetime, datetime] | None:
    """Require naive datetimes (shop-local); strip sub-second noise for grid matching."""
    if start.tzinfo is not None or end.tzinfo is not None:
        return None
    return (start.replace(microsecond=0), end.replace(microsecond=0))


def _is_on_slot_grid(dt: datetime) -> bool:
    return (
        dt.second == 0
        and dt.microsecond == 0
        and dt.minute % SLOT_STEP_MINUTES == 0
    )


def _advisory_lock_employee_booking(employee_id: uuid.UUID) -> None:
    """
    Serialize concurrent public bookings for the same employee (PostgreSQL only).
    SQLite and others skip locking — dev/single-user only; use Postgres in production.
    """
    bind = db.session.get_bind()
    if bind is None or bind.dialect.name != "postgresql":
        return
    u = employee_id.int
    k1 = (u >> 96) & 0x7FFFFFFF
    k2 = (u >> 64) & 0x7FFFFFFF
    if k1 == 0:
        k1 = 1
    if k2 == 0:
        k2 = 1
    db.session.execute(text("SELECT pg_advisory_xact_lock(:a, :b)"), {"a": k1, "b": k2})


def _slot_is_bookable(
    business: Business,
    employee_id: uuid.UUID,
    start: datetime,
    end: datetime,
    duration_min: int,
) -> bool:
    """True iff this exact interval is among currently free slots for that employee."""
    for s, e in _iter_slots_for_employee(business, employee_id, start.date(), duration_min):
        if s == start and e == end:
            return True
    return False


def _validate_public_customer_fields(
    first: str,
    last: str,
    phone: str,
    email: str | None,
    notes: str | None,
) -> str | None:
    if len(first) > _NAME_MAX or len(last) > _NAME_MAX:
        return "Nombre o apellidos demasiado largos."
    if len(phone) > 20:
        return "Teléfono demasiado largo."
    if len(phone) < _PHONE_MIN_LEN:
        return "Teléfono demasiado corto."
    if email is not None and email != "":
        if len(email) > 120 or not _EMAIL_RE.match(email):
            return "Email no válido."
    if notes is not None and len(notes) > _MAX_NOTES_LEN:
        return "Notas demasiado largas."
    return None


def _parse_hhmm(s: str) -> time | None:
    try:
        parts = str(s).strip().split(":")
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        return time(h % 24, m % 60)
    except (ValueError, IndexError):
        return None


def _default_day_intervals(weekday: int) -> list[tuple[time, time]]:
    if weekday < 5:
        return [(time(9, 0), time(18, 0))]
    if weekday == 5:
        return [(time(9, 0), time(14, 0))]
    return []


def _intervals_from_json(raw: str | None, weekday: int) -> list[tuple[time, time]]:
    """
    Resolve open intervals for a weekday (0=Mon … 6=Sun) for the business.

    - Missing / blank / invalid JSON → built-in defaults.
    - Valid JSON object → that schedule is authoritative:
      missing day or empty list means closed (no fallback to defaults).
    """
    if not raw or not str(raw).strip():
        return _default_day_intervals(weekday)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return _default_day_intervals(weekday)

    if not isinstance(data, dict):
        return _default_day_intervals(weekday)

    return _day_blocks_to_intervals(data, weekday)


def _employee_schedule_intervals(
    raw: str | None, weekday: int
) -> list[tuple[time, time]] | None:
    """
    Employee custom schedule for a weekday.

    Returns None when the employee has no custom schedule (follow business hours).
    Returns [] when the employee is explicitly closed that day.
    """
    if not raw or not str(raw).strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return _day_blocks_to_intervals(data, weekday)


def _day_blocks_to_intervals(data: dict, weekday: int) -> list[tuple[time, time]]:
    labels = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    day_blocks = data.get(str(weekday))
    if day_blocks is None:
        day_blocks = data.get(labels[weekday])

    if day_blocks is None:
        return []
    if not isinstance(day_blocks, list) or len(day_blocks) == 0:
        return []

    out: list[tuple[time, time]] = []
    for block in day_blocks:
        if not isinstance(block, dict):
            continue
        o = _parse_hhmm(str(block.get("open", "")))
        c = _parse_hhmm(str(block.get("close", "")))
        if o and c and c > o:
            out.append((o, c))
    return out


def _intersect_time_intervals(
    a: list[tuple[time, time]], b: list[tuple[time, time]]
) -> list[tuple[time, time]]:
    out: list[tuple[time, time]] = []
    for ao, ac in a:
        for bo, bc in b:
            start = max(ao, bo)
            end = min(ac, bc)
            if end > start:
                out.append((start, end))
    return out


def _open_intervals_for_employee(
    business: Business, employee: Employee, weekday: int
) -> list[tuple[time, time]]:
    biz = _intervals_from_json(business.business_hours_json, weekday)
    emp = _employee_schedule_intervals(employee.work_hours_json, weekday)
    if emp is None:
        return biz
    return _intersect_time_intervals(biz, emp)


def _day_window_local(d: date) -> tuple[datetime, datetime]:
    """Naive local day bounds (server-local calendar day)."""
    start = datetime.combine(d, time.min)
    end = start + timedelta(days=1)
    return start, end


def _busy_intervals(
    business_id: uuid.UUID, employee_id: uuid.UUID, day_start: datetime, day_end: datetime
) -> list[tuple[datetime, datetime]]:
    rows = (
        Appointment.query.filter(
            Appointment.business_id == business_id,
            Appointment.employee_id == employee_id,
            Appointment.start_time < day_end,
            Appointment.end_time > day_start,
            Appointment.status.in_(BLOCKING_STATUSES),
        )
        .all()
    )
    return [(a.start_time, a.end_time) for a in rows]


def _overlaps(a_start: datetime, a_end: datetime, blocks: list[tuple[datetime, datetime]]) -> bool:
    for b_start, b_end in blocks:
        if a_start < b_end and a_end > b_start:
            return True
    return False


def _iter_slots_for_employee(
    business: Business,
    employee_id: uuid.UUID,
    d: date,
    duration_min: int,
) -> list[tuple[datetime, datetime]]:
    emp = Employee.query.filter_by(
        id=employee_id, business_id=business.id, is_active=True
    ).first()
    if not emp:
        return []

    wd = d.weekday()
    intervals = _open_intervals_for_employee(business, emp, wd)
    day_start, day_end = _day_window_local(d)
    busy = _busy_intervals(business.id, emp.id, day_start, day_end)
    step = timedelta(minutes=SLOT_STEP_MINUTES)
    duration = timedelta(minutes=duration_min)
    slots: list[tuple[datetime, datetime]] = []
    now = datetime.now()
    today = date.today()

    for open_t, close_t in intervals:
        open_dt = datetime.combine(d, open_t)
        close_dt = datetime.combine(d, close_t)
        if close_dt <= open_dt:
            continue
        t = open_dt
        while t + duration <= close_dt:
            if d == today and t < now:
                t += step
                continue
            end = t + duration
            if not _overlaps(t, end, busy):
                slots.append((t, end))
            t += step
    return slots


def _get_business_by_slug(slug: str) -> Business | None:
    if not slug or not re.match(r"^[a-zA-Z0-9\-]+$", slug):
        return None
    s = slug.strip().lower()
    return Business.query.filter(func.lower(Business.public_slug) == s).first()


def _public_business_dict(b: Business) -> dict[str, Any]:
    return {
        "id": str(b.id),
        "name": b.name,
        "address": b.address,
        "phone": b.phone,
        "email": b.email,
        "logo_url": b.logo_url,
        "description": b.public_description or b.booking_notes,
        "allow_any_barber": b.allow_any_barber,
        "slug": b.public_slug,
    }


@public_booking.route("/booking/<slug>", methods=["GET"])
def get_public_business(slug: str):
    b = _get_business_by_slug(slug)
    if not b or not b.is_active:
        return _json_error("Barbería no encontrada.", 404)
    return jsonify(_public_business_dict(b)), 200


@public_booking.route("/booking/<slug>/services", methods=["GET"])
def list_public_services(slug: str):
    b = _get_business_by_slug(slug)
    if not b or not b.is_active:
        return _json_error("Barbería no encontrada.", 404)
    items = (
        ServiceType.query.filter_by(business_id=b.id, is_active=True)
        .order_by(ServiceType.name)
        .all()
    )
    return (
        jsonify(
            {
                "items": [
                    {
                        "id": str(s.id),
                        "name": s.name,
                        "description": s.description,
                        "duration": s.duration,
                        "price": float(s.price),
                    }
                    for s in items
                ]
            }
        ),
        200,
    )


@public_booking.route("/booking/<slug>/barbers", methods=["GET"])
def list_public_barbers(slug: str):
    b = _get_business_by_slug(slug)
    if not b or not b.is_active:
        return _json_error("Barbería no encontrada.", 404)
    emps = (
        Employee.query.filter_by(business_id=b.id, is_active=True)
        .order_by(Employee.id)
        .all()
    )
    items = []
    for e in emps:
        u = e.user
        label = staff_display_label(e, u)
        items.append(
            {
                "employee_id": str(e.id),
                "label": label,
                "email": u.email if u else None,
            }
        )
    return jsonify({"items": items}), 200


@public_booking.route("/booking/<slug>/availability", methods=["GET"])
def get_availability(slug: str):
    b = _get_business_by_slug(slug)
    if not b or not b.is_active:
        return _json_error("Barbería no encontrada.", 404)

    d = _parse_date(request.args.get("date"))
    if not d:
        return _json_error("Query param 'date' (YYYY-MM-DD) is required.", 400)

    sid = _parse_uuid(request.args.get("service_id"))
    if not sid:
        return _json_error("Query param 'service_id' is required.", 400)

    service = ServiceType.query.filter_by(id=sid, business_id=b.id, is_active=True).first()
    if not service:
        return _json_error("Servicio no encontrado.", 404)

    duration = int(service.duration)
    if duration <= 0:
        return _json_error("Servicio con duración inválida.", 400)

    emp_param = request.args.get("employee_id")
    emp_uuid = _parse_uuid(emp_param) if emp_param else None

    employees = (
        Employee.query.filter_by(business_id=b.id, is_active=True).order_by(Employee.id).all()
    )
    if not employees:
        return jsonify({"slots": [], "allow_any_barber": b.allow_any_barber}), 200

    slots_out: list[dict[str, Any]] = []

    if emp_uuid:
        emp = Employee.query.filter_by(id=emp_uuid, business_id=b.id, is_active=True).first()
        if not emp:
            return _json_error("Barbero no encontrado.", 404)
        for start, end in _iter_slots_for_employee(b, emp.id, d, duration):
            slots_out.append(
                {
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "employee_id": str(emp.id),
                }
            )
    elif b.allow_any_barber:
        seen: set[str] = set()
        for emp in employees:
            for start, end in _iter_slots_for_employee(b, emp.id, d, duration):
                key = start.isoformat()
                if key not in seen:
                    seen.add(key)
                    slots_out.append(
                        {
                            "start": start.isoformat(),
                            "end": end.isoformat(),
                            "employee_id": None,
                        }
                    )
        slots_out.sort(key=lambda x: x["start"])
    else:
        return _json_error("Selecciona un barbero para ver horarios.", 400)

    return jsonify({"slots": slots_out, "allow_any_barber": b.allow_any_barber}), 200


@public_booking.route("/booking/<slug>/calendar-hints", methods=["GET"])
def calendar_hints(slug: str):
    """Lightweight month view: which days have ≥1 slot (for service + optional barber)."""
    b = _get_business_by_slug(slug)
    if not b or not b.is_active:
        return _json_error("Barbería no encontrada.", 404)

    year_s = request.args.get("year")
    month_s = request.args.get("month")
    sid = _parse_uuid(request.args.get("service_id"))
    if not sid:
        return _json_error("service_id required.", 400)
    try:
        year = int(year_s)
        month = int(month_s)
    except (TypeError, ValueError):
        return _json_error("year and month must be integers.", 400)

    service = ServiceType.query.filter_by(id=sid, business_id=b.id, is_active=True).first()
    if not service:
        return _json_error("Servicio no encontrado.", 404)

    duration = int(service.duration)
    if duration <= 0:
        return _json_error("Servicio con duración inválida.", 400)

    emp_param = request.args.get("employee_id")
    emp_uuid = _parse_uuid(emp_param) if emp_param else None

    employees = (
        Employee.query.filter_by(business_id=b.id, is_active=True).order_by(Employee.id).all()
    )
    if not employees:
        return jsonify({"days": {}}), 200

    selected_emp: Employee | None = None
    if emp_uuid:
        selected_emp = Employee.query.filter_by(
            id=emp_uuid, business_id=b.id, is_active=True
        ).first()
        if not selected_emp:
            return _json_error("Barbero no encontrado.", 404)

    if selected_emp is None and not b.allow_any_barber:
        return _json_error("Selecciona un barbero para ver el calendario.", 400)

    _, last_day = monthrange(year, month)
    days: dict[str, bool] = {}

    for day_n in range(1, last_day + 1):
        d = date(year, month, day_n)
        if d < date.today():
            days[d.isoformat()] = False
            continue
        count = 0
        if selected_emp is not None:
            count = len(_iter_slots_for_employee(b, selected_emp.id, d, duration))
        elif b.allow_any_barber:
            seen = set()
            for emp in employees:
                for start, _ in _iter_slots_for_employee(b, emp.id, d, duration):
                    seen.add(start.isoformat())
            count = len(seen)
        days[d.isoformat()] = count > 0

    return jsonify({"days": days}), 200


def _pick_employee_for_slot(
    business: Business, start: datetime, end: datetime, preferred: uuid.UUID | None
) -> Employee | None:
    """Pick an active employee who is free AND working during this slot."""
    duration_min = max(1, int((end - start).total_seconds() // 60))
    employees = (
        Employee.query.filter_by(business_id=business.id, is_active=True)
        .order_by(Employee.id)
        .all()
    )

    def _ok(emp: Employee) -> bool:
        return _slot_is_bookable(business, emp.id, start, end, duration_min)

    if preferred:
        emp = Employee.query.filter_by(
            id=preferred, business_id=business.id, is_active=True
        ).first()
        if emp and _ok(emp):
            return emp
    for emp in employees:
        if _ok(emp):
            return emp
    return None


def _find_or_create_client(
    business_id: uuid.UUID,
    first_name: str,
    last_name: str,
    phone: str,
    email: str | None,
    notes: str | None,
) -> Client:
    phone = phone.strip()
    q = Client.query.filter(
        and_(Client.business_id == business_id, Client.phone == phone)
    ).first()
    if q:
        if email and not q.email:
            q.email = email[:120]
        if notes:
            q.notes = (q.notes or "") + ("\n" if q.notes else "") + notes
        return q
    c = Client(
        business_id=business_id,
        first_name=first_name.strip()[:80],
        last_name=last_name.strip()[:80],
        phone=phone[:20],
        email=(email or "").strip()[:120] or None,
        notes=notes,
        appointments_amount=0,
    )
    db.session.add(c)
    db.session.flush()
    return c


def _client_to_public_dict(c: Client) -> dict:
    return {
        "id": str(c.id),
        "first_name": c.first_name,
        "last_name": c.last_name,
        "phone": c.phone,
        "email": c.email,
        "username": c.username,
        "has_account": bool(c.username and c.encrypted_password),
    }


def _optional_logged_in_client(business_id: uuid.UUID) -> Client | None:
    """If Authorization Bearer is a valid customer JWT for this business, return the Client."""
    try:
        verify_jwt_in_request(optional=True)
        from flask_jwt_extended import get_jwt

        claims = get_jwt()
    except Exception:
        return None
    if not claims or claims.get("role") != CUSTOMER_ROLE:
        return None
    ctx, err = get_customer_context()
    if err is not None or ctx is None:
        return None
    if ctx.business_id != business_id:
        return None
    return Client.query.filter_by(id=ctx.client_id, business_id=business_id).first()


@public_booking.route("/booking/<slug>/auth/register", methods=["POST"])
def customer_register(slug: str):
    """Create a customer account (username + password) for returning bookings."""
    b = _get_business_by_slug(slug)
    if not b or not b.is_active:
        return _json_error("Barbería no encontrada.", 404)

    payload = request.get_json(silent=True) or {}
    username = (payload.get("username") or "").strip().lower()
    password = payload.get("password") or ""
    first = (payload.get("first_name") or "").strip()
    last = (payload.get("last_name") or "").strip()
    phone = (payload.get("phone") or "").strip()
    email = (payload.get("email") or "").strip() or None

    if not username or len(username) < 3:
        return _json_error("El usuario debe tener al menos 3 caracteres.", 400)
    if not password or len(password) < 6:
        return _json_error("La contraseña debe tener al menos 6 caracteres.", 400)
    if not all([first, last, phone]):
        return _json_error("Faltan nombre, apellido o teléfono.", 400)

    cust_err = _validate_public_customer_fields(first, last, phone, email, None)
    if cust_err:
        return _json_error(cust_err, 400)

    existing_user = Client.query.filter(
        and_(
            Client.business_id == b.id,
            func.lower(Client.username) == username,
        )
    ).first()
    if existing_user:
        return _json_error("Ese nombre de usuario ya está en uso.", 409)

    # Reuse existing client by phone when possible; otherwise create new.
    client = Client.query.filter(
        and_(Client.business_id == b.id, Client.phone == phone)
    ).first()
    if client:
        if client.username and client.encrypted_password:
            return _json_error(
                "Ya hay una cuenta con este teléfono. Inicia sesión.", 409
            )
        client.first_name = first[:80]
        client.last_name = last[:80]
        if email:
            client.email = email[:120]
        client.username = username[:80]
        client.encrypted_password = generate_password_hash(password)
    else:
        client = Client(
            business_id=b.id,
            first_name=first[:80],
            last_name=last[:80],
            phone=phone[:20],
            email=(email or "")[:120] or None,
            username=username[:80],
            encrypted_password=generate_password_hash(password),
            appointments_amount=0,
        )
        db.session.add(client)

    db.session.commit()

    token = create_access_token(
        identity=str(client.id),
        additional_claims={
            "role": CUSTOMER_ROLE,
            "client_id": str(client.id),
            "business_id": str(b.id),
        },
    )
    return (
        jsonify({"access_token": token, "client": _client_to_public_dict(client)}),
        201,
    )


@public_booking.route("/booking/<slug>/auth/signin", methods=["POST"])
def customer_signin(slug: str):
    b = _get_business_by_slug(slug)
    if not b or not b.is_active:
        return _json_error("Barbería no encontrada.", 404)

    payload = request.get_json(silent=True) or {}
    username = (payload.get("username") or "").strip().lower()
    password = payload.get("password") or ""
    if not username or not password:
        return _json_error("Usuario y contraseña requeridos.", 400)

    client = Client.query.filter(
        and_(
            Client.business_id == b.id,
            func.lower(Client.username) == username,
        )
    ).first()
    if (
        not client
        or not client.encrypted_password
        or not check_password_hash(client.encrypted_password, password)
    ):
        return _json_error("Usuario o contraseña incorrectos.", 401)

    token = create_access_token(
        identity=str(client.id),
        additional_claims={
            "role": CUSTOMER_ROLE,
            "client_id": str(client.id),
            "business_id": str(b.id),
        },
    )
    return jsonify({"access_token": token, "client": _client_to_public_dict(client)}), 200


@public_booking.route("/booking/<slug>/auth/me", methods=["GET"])
def customer_me(slug: str):
    b = _get_business_by_slug(slug)
    if not b or not b.is_active:
        return _json_error("Barbería no encontrada.", 404)

    try:
        verify_jwt_in_request()
    except Exception:
        return _json_error("No autenticado.", 401)

    ctx, err = get_customer_context()
    if err is not None or ctx is None:
        return err[0], err[1]  # type: ignore[index]
    if ctx.business_id != b.id:
        return _json_error("Token no válido para esta barbería.", 403)

    client = Client.query.filter_by(id=ctx.client_id, business_id=b.id).first()
    if not client:
        return _json_error("Cliente no encontrado.", 404)
    return jsonify({"client": _client_to_public_dict(client)}), 200


@public_booking.route("/booking/<slug>/bookings", methods=["POST"])
def create_public_booking(slug: str):
    b = _get_business_by_slug(slug)
    if not b or not b.is_active:
        return _json_error("Barbería no encontrada.", 404)

    payload = request.get_json(silent=True) or {}
    sid = _parse_uuid(payload.get("service_id"))
    start_raw = _parse_dt(payload.get("start_time"))
    end_raw = _parse_dt(payload.get("end_time"))

    logged_client = _optional_logged_in_client(b.id)

    first = (payload.get("first_name") or "").strip()
    last = (payload.get("last_name") or "").strip()
    phone = (payload.get("phone") or "").strip()
    email = (payload.get("email") or "").strip() or None
    if logged_client:
        first = first or (logged_client.first_name or "")
        last = last or (logged_client.last_name or "")
        phone = phone or (logged_client.phone or "")
        email = email or logged_client.email

    notes_raw = payload.get("notes")
    if notes_raw is None:
        notes: str | None = None
    elif isinstance(notes_raw, str):
        notes = notes_raw.strip() or None
    else:
        return _json_error("El campo notes debe ser texto.", 400)

    emp_req = _parse_uuid(payload.get("employee_id")) if payload.get("employee_id") else None

    if not all([sid, start_raw, end_raw, first, last, phone]):
        return _json_error(
            "Faltan service_id, start_time, end_time, first_name, last_name o phone.", 400
        )

    norm = _normalize_booking_datetimes(start_raw, end_raw)
    if norm is None:
        return _json_error(
            "Usa fecha y hora en horario local sin zona horaria (como en la agenda pública).",
            400,
        )
    start, end = norm

    if end <= start:
        return _json_error("Horario inválido.", 400)

    if not _is_on_slot_grid(start):
        return _json_error("El inicio debe alinearse a franjas de 15 minutos.", 400)

    cust_err = _validate_public_customer_fields(first, last, phone, email, notes)
    if cust_err:
        return _json_error(cust_err, 400)

    service = ServiceType.query.filter_by(id=sid, business_id=b.id, is_active=True).first()
    if not service:
        return _json_error("Servicio no encontrado.", 404)

    dur_min = int(service.duration)
    if dur_min <= 0:
        return _json_error("Servicio con duración inválida.", 400)

    expected_delta = timedelta(minutes=dur_min)
    if abs((end - start) - expected_delta) > timedelta(seconds=1):
        return _json_error("La duración no coincide con el servicio.", 400)

    if emp_req:
        emp = Employee.query.filter_by(id=emp_req, business_id=b.id, is_active=True).first()
        if not emp:
            return _json_error("Barbero no encontrado.", 404)
        chosen = emp
    else:
        if not b.allow_any_barber:
            return _json_error("Debes elegir un barbero.", 400)
        chosen = _pick_employee_for_slot(b, start, end, None)
        if not chosen:
            return _json_error("Ese horario ya no está disponible.", 409)

    _advisory_lock_employee_booking(chosen.id)

    day_start, day_end = _day_window_local(start.date())
    busy = _busy_intervals(b.id, chosen.id, day_start, day_end)
    if _overlaps(start, end, busy):
        return _json_error("Ese horario ya no está disponible.", 409)

    if not _slot_is_bookable(b, chosen.id, start, end, dur_min):
        return _json_error(
            "Ese horario no está disponible en la agenda (fuera de horario o ya ocupado).",
            409,
        )

    if logged_client:
        client = logged_client
        # Keep profile fresh from form when they edit while logged in.
        client.first_name = first[:80]
        client.last_name = last[:80]
        client.phone = phone[:20]
        if email:
            client.email = email[:120]
        if notes:
            client.notes = (client.notes or "") + ("\n" if client.notes else "") + notes
    else:
        client = _find_or_create_client(b.id, first, last, phone, email, notes)

    full_name = f"{first} {last}"[:120]
    appt = Appointment(
        client_id=client.id,
        service_type_id=service.id,
        business_id=b.id,
        employee_id=chosen.id,
        client_name=full_name,
        client_email=(email or client.email or "")[:120] or "—",
        client_phone=phone[:20],
        start_time=start,
        end_time=end,
        status="confirmed",
        notes=notes,
    )
    db.session.add(appt)
    client.appointments_amount = (client.appointments_amount or 0) + 1
    db.session.commit()

    notification_result = notify_appointment_created(appt)

    return (
        jsonify(
            {
                "appointment_id": str(appt.id),
                "message": "Reserva confirmada.",
                "employee_id": str(chosen.id),
                "notification_status": notification_result.get("status"),
                "email_notification_status": notification_result.get("email"),
                "whatsapp_notification_status": notification_result.get("whatsapp"),
            }
        ),
        201,
    )
