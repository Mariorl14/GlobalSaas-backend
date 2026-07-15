"""
Multi-tenant shop (barber) API — all routes under /api/shop/*.
Data is scoped to JWT claim business_id. Roles: admin (shop admin), employee (staff).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from flask import Blueprint, jsonify, request
from sqlalchemy import func, or_

from app.extensions import db
from app.models import (
    Appointment,
    Business,
    Client,
    Employee,
    InventoryProduct,
    ServiceType,
    User,
)
from app.tenant_auth import ShopContext, shop_admin_required, shop_jwt_required
from app.appointment_notifications import send_appointment_confirmation
from app.shop_insights import build_insights, parse_goals, serialize_goals
from app.inventory_movements import (
    InventoryMovementError,
    apply_stock_correction_if_needed,
    apply_stock_movement,
    movement_to_dict,
)
from app.models.inventory_movement import InventoryMovement
from app.shop_sales import (
    SaleError,
    create_sale,
    ensure_sale_for_completed_appointment,
    link_orphan_inventory_sales,
    sale_to_dict,
)
from app.models.sale import Sale

shop_api = Blueprint("shop_api", __name__, url_prefix="/api/shop")

APPOINTMENT_STATUSES = frozenset(
    {"scheduled", "confirmed", "completed", "canceled", "cancelled", "no_show", "pending"}
)


def _json_error(message: str, status_code: int = 400):
    return jsonify({"error": message}), status_code


def _parse_uuid(value):
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _parse_dt(value):
    """Parse ISO datetimes to naive UTC (matches columns filled with utcnow())."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    try:
        s = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(s)
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except ValueError:
        return None


def _parse_decimal(value):
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _normalize_appointment_status(status: str) -> str:
    s = (status or "scheduled").strip().lower()
    if s == "cancelled":
        s = "canceled"
    if s not in APPOINTMENT_STATUSES:
        return "scheduled"
    if s == "pending":
        return "scheduled"
    return s


def _business_to_public(b: Business) -> dict:
    return {
        "id": str(b.id),
        "name": b.name,
        "address": b.address,
        "email": b.email,
        "phone": b.phone,
        "is_active": b.is_active,
        "logo_url": b.logo_url,
        "business_hours_json": b.business_hours_json,
        "booking_notes": b.booking_notes,
    }


def _client_to_dict(c: Client) -> dict:
    return {
        "id": str(c.id),
        "business_id": str(c.business_id) if c.business_id else None,
        "first_name": c.first_name,
        "last_name": c.last_name,
        "phone": c.phone,
        "email": c.email,
        "address": c.address,
        "notes": c.notes,
        "preferred_employee_id": (
            str(c.preferred_employee_id) if c.preferred_employee_id else None
        ),
        "appointments_amount": c.appointments_amount,
    }


def _service_to_dict(s: ServiceType) -> dict:
    return {
        "id": str(s.id),
        "business_id": str(s.business_id),
        "name": s.name,
        "description": s.description,
        "duration": s.duration,
        "price": float(s.price),
        "is_active": s.is_active,
    }


def _inventory_to_dict(p: InventoryProduct) -> dict:
    return {
        "id": str(p.id),
        "business_id": str(p.business_id),
        "name": p.name,
        "category": p.category,
        "price": float(p.price),
        "unit_cost": float(p.unit_cost) if p.unit_cost is not None else None,
        "supplier": p.supplier,
        "stock": p.stock,
        "min_stock": p.min_stock,
        "is_active": p.is_active,
        "low_stock": p.stock <= p.min_stock,
    }


def _appointment_to_dict(a: Appointment) -> dict:
    return {
        "id": str(a.id),
        "business_id": str(a.business_id),
        "client_id": str(a.client_id),
        "service_type_id": str(a.service_type_id),
        "employee_id": str(a.employee_id),
        "client_name": a.client_name,
        "client_email": a.client_email,
        "client_phone": a.client_phone,
        "start_time": a.start_time.isoformat() if a.start_time else None,
        "end_time": a.end_time.isoformat() if a.end_time else None,
        "status": a.status,
        "notes": a.notes,
    }


def _client_scope_ids(business_id: uuid.UUID):
    """Clients tied to tenant or seen in this tenant's appointments (legacy rows)."""
    appt_subq = (
        db.session.query(Appointment.client_id)
        .filter(Appointment.business_id == business_id)
        .distinct()
    )
    return or_(Client.business_id == business_id, Client.id.in_(appt_subq))


def _get_appointment_for_tenant(
    ctx: ShopContext, appointment_id: uuid.UUID
) -> Appointment | None:
    return (
        Appointment.query.filter_by(id=appointment_id, business_id=ctx.business_id)
        .first()
    )


# --- Me & dashboard ---


@shop_api.route("/me", methods=["GET"])
@shop_jwt_required
def shop_me(ctx: ShopContext):
    user = User.query.get(ctx.user_id)
    if not user:
        return _json_error("Usuario no encontrado.", 404)
    business = Business.query.get(ctx.business_id)
    if not business:
        return _json_error("Negocio no encontrado.", 404)
    return (
        jsonify(
            {
                "user": {
                    "id": str(user.id),
                    "email": user.email,
                    "role": user.role,
                    "business_id": str(user.business_id),
                    "is_active": user.is_active,
                },
                "business": _business_to_public(business),
                "employee_id": str(ctx.employee_id) if ctx.employee_id else None,
            }
        ),
        200,
    )


@shop_api.route("/dashboard", methods=["GET"])
@shop_jwt_required
def shop_dashboard(ctx: ShopContext):
    bid = ctx.business_id
    now = datetime.utcnow()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    week_end = day_start + timedelta(days=7)
    thirty_ago = now - timedelta(days=30)

    appt_base = Appointment.query.filter(Appointment.business_id == bid)

    today_count = appt_base.filter(
        Appointment.start_time >= day_start,
        Appointment.start_time < day_end,
    ).count()

    week_count = appt_base.filter(
        Appointment.start_time >= day_start,
        Appointment.start_time < week_end,
    ).count()

    upcoming = (
        appt_base.filter(Appointment.start_time >= now)
        .order_by(Appointment.start_time.asc())
        .limit(8)
        .all()
    )

    active_customers = Client.query.filter(_client_scope_ids(bid)).count()

    low_stock = (
        InventoryProduct.query.filter(
            InventoryProduct.business_id == bid,
            InventoryProduct.is_active.is_(True),
            InventoryProduct.stock <= InventoryProduct.min_stock,
        )
        .order_by(InventoryProduct.stock.asc())
        .limit(10)
        .all()
    )

    top_rows = (
        db.session.query(
            Appointment.service_type_id,
            ServiceType.name,
            func.count(Appointment.id).label("cnt"),
        )
        .join(ServiceType, ServiceType.id == Appointment.service_type_id)
        .filter(
            Appointment.business_id == bid,
            Appointment.start_time >= thirty_ago,
        )
        .group_by(Appointment.service_type_id, ServiceType.name)
        .order_by(func.count(Appointment.id).desc())
        .limit(5)
        .all()
    )
    top_services = [
        {"service_type_id": str(r[0]), "name": r[1], "count": int(r[2])}
        for r in top_rows
    ]

    return (
        jsonify(
            {
                "appointments_today": today_count,
                "appointments_this_week": week_count,
                "upcoming_appointments": [_appointment_to_dict(a) for a in upcoming],
                "active_customers_count": active_customers,
                "low_stock_items": [_inventory_to_dict(p) for p in low_stock],
                "top_services": top_services,
                "revenue_month_placeholder": None,
            }
        ),
        200,
    )


@shop_api.route("/insights", methods=["GET"])
@shop_jwt_required
def shop_insights(ctx: ShopContext):
    """Business Intelligence snapshot for the tenant portal Insights page."""
    range_key = (request.args.get("range") or "today").strip().lower()
    from_dt = _parse_dt(request.args.get("from"))
    to_dt = _parse_dt(request.args.get("to"))
    payload = build_insights(
        ctx.business_id,
        range_key=range_key,
        from_dt=from_dt,
        to_dt=to_dt,
    )
    return jsonify(payload), 200


@shop_api.route("/insights/goals", methods=["GET", "PUT"])
@shop_jwt_required
def shop_insights_goals(ctx: ShopContext):
    business = Business.query.get(ctx.business_id)
    if not business:
        return _json_error("Negocio no encontrado.", 404)

    if request.method == "GET":
        return jsonify({"goals": parse_goals(business.insights_goals_json)}), 200

    if ctx.role != "admin":
        return _json_error("Solo el administrador puede editar metas.", 403)

    payload = request.get_json(silent=True) or {}
    current = parse_goals(business.insights_goals_json)
    for key in current:
        if key in payload and payload[key] is not None:
            try:
                current[key] = max(0, float(payload[key]))
            except (TypeError, ValueError):
                return _json_error(f"Valor inválido para {key}.", 400)
    business.insights_goals_json = serialize_goals(current)
    db.session.commit()
    return jsonify({"goals": current}), 200


# --- Appointments ---


@shop_api.route("/appointments", methods=["GET"])
@shop_jwt_required
def list_appointments(ctx: ShopContext):
    q = Appointment.query.filter_by(business_id=ctx.business_id)

    df = _parse_dt(request.args.get("from"))
    dt = _parse_dt(request.args.get("to"))
    if df:
        q = q.filter(Appointment.start_time >= df)
    if dt:
        q = q.filter(Appointment.start_time <= dt)

    emp = _parse_uuid(request.args.get("employee_id"))
    if emp:
        q = q.filter(Appointment.employee_id == emp)

    st = request.args.get("status")
    if st:
        q = q.filter(Appointment.status == _normalize_appointment_status(st))

    items = q.order_by(Appointment.start_time.desc()).limit(500).all()
    return jsonify({"items": [_appointment_to_dict(a) for a in items]}), 200


@shop_api.route("/appointments/<appointment_id>", methods=["GET"])
@shop_jwt_required
def get_appointment(ctx: ShopContext, appointment_id: str):
    aid = _parse_uuid(appointment_id)
    if not aid:
        return _json_error("ID inválido.", 400)
    a = _get_appointment_for_tenant(ctx, aid)
    if not a:
        return _json_error("Cita no encontrada.", 404)
    return jsonify(_appointment_to_dict(a)), 200


@shop_api.route("/appointments", methods=["POST"])
@shop_jwt_required
def create_appointment(ctx: ShopContext):
    payload = request.get_json(silent=True) or {}
    cid = _parse_uuid(payload.get("client_id"))
    sid = _parse_uuid(payload.get("service_type_id"))
    eid = _parse_uuid(payload.get("employee_id"))
    start = _parse_dt(payload.get("start_time"))
    end = _parse_dt(payload.get("end_time"))
    if not all([cid, sid, eid, start, end]):
        return _json_error(
            "Faltan client_id, service_type_id, employee_id, start_time, end_time.",
            400,
        )

    client = Client.query.filter(
        _client_scope_ids(ctx.business_id), Client.id == cid
    ).first()
    if not client:
        return _json_error("Cliente no encontrado en tu negocio.", 404)

    st = ServiceType.query.filter_by(id=sid, business_id=ctx.business_id).first()
    if not st:
        return _json_error("Servicio no encontrado.", 404)

    emp = Employee.query.filter_by(id=eid, business_id=ctx.business_id).first()
    if not emp:
        return _json_error("Empleado no encontrado.", 404)

    status = _normalize_appointment_status(payload.get("status", "scheduled"))
    notes = payload.get("notes")

    full_name = f"{client.first_name} {client.last_name}".strip()
    a = Appointment(
        client_id=cid,
        service_type_id=sid,
        business_id=ctx.business_id,
        employee_id=eid,
        client_name=full_name[:120],
        client_email=(client.email or "")[:120] or "—",
        client_phone=client.phone,
        start_time=start,
        end_time=end,
        status=status,
        notes=notes,
    )
    db.session.add(a)
    client.appointments_amount = (client.appointments_amount or 0) + 1
    db.session.flush()
    if status == "completed":
        try:
            ensure_sale_for_completed_appointment(
                a, created_by_user_id=ctx.user_id
            )
        except SaleError as exc:
            db.session.rollback()
            return _json_error(exc.message, exc.status_code)
    db.session.commit()
    notification_result = send_appointment_confirmation(a)
    payload = _appointment_to_dict(a)
    payload["notification_status"] = notification_result.get("status")
    return jsonify(payload), 201


@shop_api.route("/appointments/<appointment_id>", methods=["PUT"])
@shop_jwt_required
def update_appointment(ctx: ShopContext, appointment_id: str):
    aid = _parse_uuid(appointment_id)
    if not aid:
        return _json_error("ID inválido.", 400)
    a = _get_appointment_for_tenant(ctx, aid)
    if not a:
        return _json_error("Cita no encontrada.", 404)

    payload = request.get_json(silent=True) or {}
    prev_status = _normalize_appointment_status(a.status)
    becoming_completed = False

    if "client_id" in payload:
        cid = _parse_uuid(payload.get("client_id"))
        if not cid:
            return _json_error("client_id inválido.", 400)
        client = Client.query.filter(
            _client_scope_ids(ctx.business_id), Client.id == cid
        ).first()
        if not client:
            return _json_error("Cliente no encontrado.", 404)
        a.client_id = cid
        full_name = f"{client.first_name} {client.last_name}".strip()
        a.client_name = full_name[:120]
        a.client_email = (client.email or "")[:120] or "—"
        a.client_phone = client.phone

    if "service_type_id" in payload:
        sid = _parse_uuid(payload.get("service_type_id"))
        if not sid:
            return _json_error("service_type_id inválido.", 400)
        st = ServiceType.query.filter_by(id=sid, business_id=ctx.business_id).first()
        if not st:
            return _json_error("Servicio no encontrado.", 404)
        a.service_type_id = sid

    if "employee_id" in payload:
        eid = _parse_uuid(payload.get("employee_id"))
        if not eid:
            return _json_error("employee_id inválido.", 400)
        emp = Employee.query.filter_by(id=eid, business_id=ctx.business_id).first()
        if not emp:
            return _json_error("Empleado no encontrado.", 404)
        a.employee_id = eid

    if "start_time" in payload:
        t = _parse_dt(payload.get("start_time"))
        if not t:
            return _json_error("start_time inválido.", 400)
        a.start_time = t
    if "end_time" in payload:
        t = _parse_dt(payload.get("end_time"))
        if not t:
            return _json_error("end_time inválido.", 400)
        a.end_time = t
    if "status" in payload:
        new_status = _normalize_appointment_status(payload.get("status"))
        becoming_completed = (
            new_status == "completed" and prev_status != "completed"
        )
        a.status = new_status
    if "notes" in payload:
        a.notes = payload.get("notes")

    if becoming_completed:
        try:
            ensure_sale_for_completed_appointment(
                a, created_by_user_id=ctx.user_id
            )
        except SaleError as exc:
            db.session.rollback()
            return _json_error(exc.message, exc.status_code)

    db.session.commit()
    return jsonify(_appointment_to_dict(a)), 200


@shop_api.route("/appointments/<appointment_id>", methods=["DELETE"])
@shop_jwt_required
def delete_appointment(ctx: ShopContext, appointment_id: str):
    aid = _parse_uuid(appointment_id)
    if not aid:
        return _json_error("ID inválido.", 400)
    a = _get_appointment_for_tenant(ctx, aid)
    if not a:
        return _json_error("Cita no encontrada.", 404)
    client = Client.query.get(a.client_id)
    db.session.delete(a)
    if client and client.appointments_amount > 0:
        client.appointments_amount -= 1
    db.session.commit()
    return ("", 204)


# --- Clients ---


@shop_api.route("/clients", methods=["GET"])
@shop_jwt_required
def list_clients(ctx: ShopContext):
    search = (request.args.get("q") or "").strip().lower()
    q = Client.query.filter(_client_scope_ids(ctx.business_id))
    if search:
        like = f"%{search}%"
        q = q.filter(
            or_(
                func.lower(Client.first_name).like(like),
                func.lower(Client.last_name).like(like),
                func.lower(Client.phone).like(like),
                func.lower(func.coalesce(Client.email, "")).like(like),
            )
        )
    items = q.order_by(Client.last_name, Client.first_name).limit(500).all()
    return jsonify({"items": [_client_to_dict(c) for c in items]}), 200


@shop_api.route("/clients/<client_id>", methods=["GET"])
@shop_jwt_required
def get_client(ctx: ShopContext, client_id: str):
    cid = _parse_uuid(client_id)
    if not cid:
        return _json_error("ID inválido.", 400)
    c = Client.query.filter(_client_scope_ids(ctx.business_id), Client.id == cid).first()
    if not c:
        return _json_error("Cliente no encontrado.", 404)
    return jsonify(_client_to_dict(c)), 200


@shop_api.route("/clients/<client_id>/appointments", methods=["GET"])
@shop_jwt_required
def client_appointments(ctx: ShopContext, client_id: str):
    cid = _parse_uuid(client_id)
    if not cid:
        return _json_error("ID inválido.", 400)
    c = Client.query.filter(_client_scope_ids(ctx.business_id), Client.id == cid).first()
    if not c:
        return _json_error("Cliente no encontrado.", 404)
    rows = (
        Appointment.query.filter_by(business_id=ctx.business_id, client_id=cid)
        .order_by(Appointment.start_time.desc())
        .limit(200)
        .all()
    )
    return jsonify({"items": [_appointment_to_dict(a) for a in rows]}), 200


@shop_api.route("/clients/<client_id>/sales", methods=["GET"])
@shop_jwt_required
def client_sales(ctx: ShopContext, client_id: str):
    cid = _parse_uuid(client_id)
    if not cid:
        return _json_error("ID inválido.", 400)
    c = Client.query.filter(_client_scope_ids(ctx.business_id), Client.id == cid).first()
    if not c:
        return _json_error("Cliente no encontrado.", 404)
    rows = (
        Sale.query.filter_by(business_id=ctx.business_id, client_id=cid)
        .order_by(Sale.created_at.desc())
        .limit(100)
        .all()
    )
    return jsonify({"items": [sale_to_dict(s) for s in rows]}), 200


@shop_api.route("/clients", methods=["POST"])
@shop_jwt_required
def create_client(ctx: ShopContext):
    payload = request.get_json(silent=True) or {}
    fn = (payload.get("first_name") or "").strip()
    ln = (payload.get("last_name") or "").strip()
    phone = (payload.get("phone") or "").strip()
    if not fn or not ln or not phone:
        return _json_error("first_name, last_name y phone son obligatorios.", 400)

    pref = _parse_uuid(payload.get("preferred_employee_id"))
    if pref:
        emp = Employee.query.filter_by(id=pref, business_id=ctx.business_id).first()
        if not emp:
            return _json_error("preferred_employee_id no válido.", 400)

    c = Client(
        business_id=ctx.business_id,
        first_name=fn,
        last_name=ln,
        phone=phone,
        email=(payload.get("email") or "").strip() or None,
        address=(payload.get("address") or "").strip() or None,
        notes=payload.get("notes"),
        preferred_employee_id=pref,
        appointments_amount=0,
    )
    db.session.add(c)
    db.session.commit()
    return jsonify(_client_to_dict(c)), 201


@shop_api.route("/clients/<client_id>", methods=["PUT"])
@shop_jwt_required
def update_client(ctx: ShopContext, client_id: str):
    cid = _parse_uuid(client_id)
    if not cid:
        return _json_error("ID inválido.", 400)
    c = Client.query.filter(_client_scope_ids(ctx.business_id), Client.id == cid).first()
    if not c:
        return _json_error("Cliente no encontrado.", 404)

    payload = request.get_json(silent=True) or {}
    if "first_name" in payload:
        v = (payload.get("first_name") or "").strip()
        if not v:
            return _json_error("first_name vacío.", 400)
        c.first_name = v
    if "last_name" in payload:
        v = (payload.get("last_name") or "").strip()
        if not v:
            return _json_error("last_name vacío.", 400)
        c.last_name = v
    if "phone" in payload:
        v = (payload.get("phone") or "").strip()
        if not v:
            return _json_error("phone vacío.", 400)
        c.phone = v
    if "email" in payload:
        c.email = (payload.get("email") or "").strip() or None
    if "address" in payload:
        c.address = (payload.get("address") or "").strip() or None
    if "notes" in payload:
        c.notes = payload.get("notes")
    if "preferred_employee_id" in payload:
        pref = _parse_uuid(payload.get("preferred_employee_id"))
        if pref:
            emp = Employee.query.filter_by(id=pref, business_id=ctx.business_id).first()
            if not emp:
                return _json_error("preferred_employee_id no válido.", 400)
        c.preferred_employee_id = pref

    if c.business_id is None:
        c.business_id = ctx.business_id

    db.session.commit()
    return jsonify(_client_to_dict(c)), 200


@shop_api.route("/clients/<client_id>", methods=["DELETE"])
@shop_jwt_required
def delete_client(ctx: ShopContext, client_id: str):
    cid = _parse_uuid(client_id)
    if not cid:
        return _json_error("ID inválido.", 400)
    c = Client.query.filter(_client_scope_ids(ctx.business_id), Client.id == cid).first()
    if not c:
        return _json_error("Cliente no encontrado.", 404)
    if Appointment.query.filter_by(client_id=cid).first():
        return _json_error(
            "No se puede eliminar: el cliente tiene citas. Cancela o reasigna primero.",
            409,
        )
    db.session.delete(c)
    db.session.commit()
    return ("", 204)


# --- Services ---


@shop_api.route("/services", methods=["GET"])
@shop_jwt_required
def list_services(ctx: ShopContext):
    items = (
        ServiceType.query.filter_by(business_id=ctx.business_id)
        .order_by(ServiceType.name)
        .all()
    )
    return jsonify({"items": [_service_to_dict(s) for s in items]}), 200


@shop_api.route("/services", methods=["POST"])
@shop_jwt_required
def create_service(ctx: ShopContext):
    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    duration = payload.get("duration")
    price = _parse_decimal(payload.get("price"))
    try:
        duration_i = int(duration)
    except (TypeError, ValueError):
        duration_i = None
    if not name or duration_i is None or duration_i < 1 or price is None or price < 0:
        return _json_error("name, duration (>0) y price (>=0) son obligatorios.", 400)

    s = ServiceType(
        business_id=ctx.business_id,
        name=name,
        description=payload.get("description"),
        duration=duration_i,
        price=price,
        is_active=bool(payload.get("is_active", True)),
    )
    db.session.add(s)
    db.session.commit()
    return jsonify(_service_to_dict(s)), 201


@shop_api.route("/services/<service_id>", methods=["PUT"])
@shop_jwt_required
def update_service(ctx: ShopContext, service_id: str):
    sid = _parse_uuid(service_id)
    if not sid:
        return _json_error("ID inválido.", 400)
    s = ServiceType.query.filter_by(id=sid, business_id=ctx.business_id).first()
    if not s:
        return _json_error("Servicio no encontrado.", 404)

    payload = request.get_json(silent=True) or {}
    if "name" in payload:
        v = (payload.get("name") or "").strip()
        if not v:
            return _json_error("name vacío.", 400)
        s.name = v
    if "description" in payload:
        s.description = payload.get("description")
    if "duration" in payload:
        try:
            d = int(payload.get("duration"))
        except (TypeError, ValueError):
            return _json_error("duration inválido.", 400)
        if d < 1:
            return _json_error("duration debe ser >= 1.", 400)
        s.duration = d
    if "price" in payload:
        p = _parse_decimal(payload.get("price"))
        if p is None or p < 0:
            return _json_error("price inválido.", 400)
        s.price = p
    if "is_active" in payload:
        s.is_active = bool(payload.get("is_active"))

    db.session.commit()
    return jsonify(_service_to_dict(s)), 200


@shop_api.route("/services/<service_id>", methods=["DELETE"])
@shop_jwt_required
def delete_service(ctx: ShopContext, service_id: str):
    sid = _parse_uuid(service_id)
    if not sid:
        return _json_error("ID inválido.", 400)
    s = ServiceType.query.filter_by(id=sid, business_id=ctx.business_id).first()
    if not s:
        return _json_error("Servicio no encontrado.", 404)
    if Appointment.query.filter_by(service_type_id=sid).first():
        return _json_error("No se puede eliminar: hay citas con este servicio.", 409)
    db.session.delete(s)
    db.session.commit()
    return ("", 204)


# --- Inventory ---


@shop_api.route("/inventory", methods=["GET"])
@shop_jwt_required
def list_inventory(ctx: ShopContext):
    items = (
        InventoryProduct.query.filter_by(business_id=ctx.business_id)
        .order_by(InventoryProduct.name)
        .all()
    )
    return jsonify({"items": [_inventory_to_dict(p) for p in items]}), 200


@shop_api.route("/inventory", methods=["POST"])
@shop_jwt_required
def create_inventory(ctx: ShopContext):
    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    price = _parse_decimal(payload.get("price"))
    if not name or price is None or price < 0:
        return _json_error("name y price (>=0) son obligatorios.", 400)
    try:
        stock = int(payload.get("stock", 0))
        min_stock = int(payload.get("min_stock", 0))
    except (TypeError, ValueError):
        return _json_error("stock y min_stock deben ser enteros.", 400)
    if stock < 0 or min_stock < 0:
        return _json_error("stock y min_stock deben ser >= 0.", 400)

    unit_cost = _parse_decimal(payload.get("unit_cost"))

    p = InventoryProduct(
        business_id=ctx.business_id,
        name=name,
        category=(payload.get("category") or "").strip() or None,
        price=price,
        unit_cost=unit_cost,
        supplier=(payload.get("supplier") or "").strip() or None,
        stock=0,
        min_stock=min_stock,
        is_active=bool(payload.get("is_active", True)),
    )
    db.session.add(p)
    db.session.flush()
    if stock > 0:
        try:
            apply_stock_movement(
                business_id=ctx.business_id,
                product_id=p.id,
                movement_type="restock",
                quantity=stock,
                created_by_user_id=ctx.user_id,
                unit_cost=unit_cost,
                notes="Stock inicial",
                update_product_cost=False,
            )
        except InventoryMovementError as exc:
            db.session.rollback()
            return _json_error(exc.message, exc.status_code)
    db.session.commit()
    return jsonify(_inventory_to_dict(p)), 201


@shop_api.route("/inventory/<product_id>", methods=["PUT"])
@shop_jwt_required
def update_inventory(ctx: ShopContext, product_id: str):
    pid = _parse_uuid(product_id)
    if not pid:
        return _json_error("ID inválido.", 400)
    p = InventoryProduct.query.filter_by(id=pid, business_id=ctx.business_id).first()
    if not p:
        return _json_error("Producto no encontrado.", 404)

    payload = request.get_json(silent=True) or {}
    if "name" in payload:
        v = (payload.get("name") or "").strip()
        if not v:
            return _json_error("name vacío.", 400)
        p.name = v
    if "category" in payload:
        p.category = (payload.get("category") or "").strip() or None
    if "price" in payload:
        pr = _parse_decimal(payload.get("price"))
        if pr is None or pr < 0:
            return _json_error("price inválido.", 400)
        p.price = pr
    if "unit_cost" in payload:
        p.unit_cost = _parse_decimal(payload.get("unit_cost"))
    if "supplier" in payload:
        p.supplier = (payload.get("supplier") or "").strip() or None
    if "stock" in payload:
        try:
            new_stock = int(payload.get("stock"))
        except (TypeError, ValueError):
            return _json_error("stock inválido.", 400)
        try:
            apply_stock_correction_if_needed(
                business_id=ctx.business_id,
                product=p,
                new_stock=new_stock,
                created_by_user_id=ctx.user_id,
                notes="Corrección desde edición de producto",
            )
        except InventoryMovementError as exc:
            db.session.rollback()
            return _json_error(exc.message, exc.status_code)
    if "min_stock" in payload:
        try:
            p.min_stock = int(payload.get("min_stock"))
        except (TypeError, ValueError):
            return _json_error("min_stock inválido.", 400)
        if p.min_stock < 0:
            return _json_error("min_stock >= 0.", 400)
    if "is_active" in payload:
        p.is_active = bool(payload.get("is_active"))

    db.session.commit()
    return jsonify(_inventory_to_dict(p)), 200


@shop_api.route("/inventory/<product_id>", methods=["DELETE"])
@shop_jwt_required
def delete_inventory(ctx: ShopContext, product_id: str):
    pid = _parse_uuid(product_id)
    if not pid:
        return _json_error("ID inválido.", 400)
    p = InventoryProduct.query.filter_by(id=pid, business_id=ctx.business_id).first()
    if not p:
        return _json_error("Producto no encontrado.", 404)
    db.session.delete(p)
    db.session.commit()
    return ("", 204)


@shop_api.route("/inventory/movements", methods=["GET"])
@shop_jwt_required
def list_inventory_movements(ctx: ShopContext):
    q = InventoryMovement.query.filter_by(business_id=ctx.business_id)

    pid = _parse_uuid(request.args.get("product_id"))
    if pid:
        q = q.filter(InventoryMovement.product_id == pid)

    mtype = (request.args.get("movement_type") or "").strip().lower()
    if mtype:
        q = q.filter(InventoryMovement.movement_type == mtype)

    sales_only = (request.args.get("sales_only") or "").strip().lower()
    if sales_only in {"1", "true", "yes"}:
        q = q.filter(InventoryMovement.movement_type == "sale")
    elif sales_only in {"0", "false", "no"}:
        q = q.filter(InventoryMovement.movement_type != "sale")

    df = _parse_dt(request.args.get("from"))
    dt = _parse_dt(request.args.get("to"))
    if df:
        q = q.filter(InventoryMovement.created_at >= df)
    if dt:
        q = q.filter(InventoryMovement.created_at <= dt)

    created_by = _parse_uuid(request.args.get("created_by_user_id"))
    if created_by:
        q = q.filter(InventoryMovement.created_by_user_id == created_by)

    cid = _parse_uuid(request.args.get("client_id"))
    if cid:
        q = q.filter(InventoryMovement.client_id == cid)

    items = q.order_by(InventoryMovement.created_at.desc()).limit(300).all()
    product_ids = {m.product_id for m in items}
    names: dict = {}
    if product_ids:
        names = {
            p.id: p.name
            for p in InventoryProduct.query.filter(
                InventoryProduct.business_id == ctx.business_id,
                InventoryProduct.id.in_(product_ids),
            ).all()
        }
    return (
        jsonify(
            {
                "items": [
                    movement_to_dict(m, product_name=names.get(m.product_id))
                    for m in items
                ]
            }
        ),
        200,
    )


@shop_api.route("/inventory/<product_id>/movements", methods=["GET"])
@shop_jwt_required
def list_product_movements(ctx: ShopContext, product_id: str):
    pid = _parse_uuid(product_id)
    if not pid:
        return _json_error("ID inválido.", 400)
    p = InventoryProduct.query.filter_by(id=pid, business_id=ctx.business_id).first()
    if not p:
        return _json_error("Producto no encontrado.", 404)
    items = (
        InventoryMovement.query.filter_by(business_id=ctx.business_id, product_id=pid)
        .order_by(InventoryMovement.created_at.desc())
        .limit(200)
        .all()
    )
    return (
        jsonify(
            {
                "product": _inventory_to_dict(p),
                "items": [movement_to_dict(m, product_name=p.name) for m in items],
            }
        ),
        200,
    )


@shop_api.route("/inventory/<product_id>/movements", methods=["POST"])
@shop_jwt_required
def create_inventory_movement(ctx: ShopContext, product_id: str):
    pid = _parse_uuid(product_id)
    if not pid:
        return _json_error("ID inválido.", 400)
    payload = request.get_json(silent=True) or {}
    try:
        qty = int(payload.get("quantity"))
    except (TypeError, ValueError):
        return _json_error("quantity debe ser un entero.", 400)

    mtype = (payload.get("movement_type") or "").strip().lower()
    # Product sales registered from Inventario must also create a Ventas ticket.
    if mtype == "sale":
        try:
            sale, replayed = create_sale(
                business_id=ctx.business_id,
                created_by_user_id=ctx.user_id,
                items=[
                    {
                        "item_type": "product",
                        "product_id": str(pid),
                        "quantity": qty,
                        "unit_price": payload.get("unit_sale_price"),
                        "unit_cost": payload.get("unit_cost"),
                    }
                ],
                client_id=_parse_uuid(payload.get("client_id")),
                notes=payload.get("notes"),
                payment_method=payload.get("payment_method") or "other",
                idempotency_key=payload.get("idempotency_key")
                or request.headers.get("Idempotency-Key"),
            )
            db.session.commit()
        except SaleError as exc:
            db.session.rollback()
            return _json_error(exc.message, exc.status_code)
        product = InventoryProduct.query.filter_by(
            id=pid, business_id=ctx.business_id
        ).first()
        movement = next(
            (i for i in sale.items if i.inventory_movement_id),
            None,
        )
        mov = None
        if movement and movement.inventory_movement_id:
            mov = InventoryMovement.query.get(movement.inventory_movement_id)
        return (
            jsonify(
                {
                    "sale": sale_to_dict(sale),
                    "movement": movement_to_dict(
                        mov, product_name=product.name if product else None
                    )
                    if mov
                    else None,
                    "product": _inventory_to_dict(product) if product else None,
                    "replayed": replayed,
                }
            ),
            200 if replayed else 201,
        )

    try:
        movement, product, replayed = apply_stock_movement(
            business_id=ctx.business_id,
            product_id=pid,
            movement_type=payload.get("movement_type"),
            quantity=qty,
            created_by_user_id=ctx.user_id,
            unit_cost=payload.get("unit_cost"),
            unit_sale_price=payload.get("unit_sale_price"),
            notes=payload.get("notes"),
            appointment_id=_parse_uuid(payload.get("appointment_id")),
            client_id=_parse_uuid(payload.get("client_id")),
            idempotency_key=payload.get("idempotency_key")
            or request.headers.get("Idempotency-Key"),
            update_product_cost=bool(payload.get("update_product_cost")),
        )
        db.session.commit()
    except InventoryMovementError as exc:
        db.session.rollback()
        return _json_error(exc.message, exc.status_code)

    return (
        jsonify(
            {
                "movement": movement_to_dict(movement, product_name=product.name),
                "product": _inventory_to_dict(product),
                "replayed": replayed,
            }
        ),
        200 if replayed else 201,
    )


@shop_api.route("/inventory/<product_id>/sale", methods=["POST"])
@shop_jwt_required
def register_inventory_sale(ctx: ShopContext, product_id: str):
    pid = _parse_uuid(product_id)
    if not pid:
        return _json_error("ID inválido.", 400)
    payload = request.get_json(silent=True) or {}
    try:
        qty = int(payload.get("quantity"))
    except (TypeError, ValueError):
        return _json_error("quantity debe ser un entero.", 400)

    try:
        sale, replayed = create_sale(
            business_id=ctx.business_id,
            created_by_user_id=ctx.user_id,
            items=[
                {
                    "item_type": "product",
                    "product_id": str(pid),
                    "quantity": qty,
                    "unit_price": payload.get("unit_sale_price"),
                    "unit_cost": payload.get("unit_cost"),
                }
            ],
            client_id=_parse_uuid(payload.get("client_id")),
            notes=payload.get("notes"),
            payment_method=payload.get("payment_method") or "other",
            idempotency_key=payload.get("idempotency_key")
            or request.headers.get("Idempotency-Key"),
        )
        db.session.commit()
    except SaleError as exc:
        db.session.rollback()
        return _json_error(exc.message, exc.status_code)

    product = InventoryProduct.query.filter_by(
        id=pid, business_id=ctx.business_id
    ).first()
    item = next((i for i in sale.items if i.product_id == pid), None)
    mov = (
        InventoryMovement.query.get(item.inventory_movement_id)
        if item and item.inventory_movement_id
        else None
    )
    return (
        jsonify(
            {
                "sale": sale_to_dict(sale),
                "movement": movement_to_dict(
                    mov, product_name=product.name if product else None
                )
                if mov
                else None,
                "product": _inventory_to_dict(product) if product else None,
                "replayed": replayed,
            }
        ),
        200 if replayed else 201,
    )


# --- Sales (POS) ---


@shop_api.route("/sales", methods=["GET"])
@shop_jwt_required
def list_sales(ctx: ShopContext):
    # Promote older Inventario-only sales into Ventas tickets (idempotent).
    linked = link_orphan_inventory_sales(ctx.business_id)
    if linked:
        db.session.commit()

    q = Sale.query.filter_by(business_id=ctx.business_id)

    df = _parse_dt(request.args.get("from"))
    dt = _parse_dt(request.args.get("to"))
    if df:
        q = q.filter(Sale.created_at >= df)
    if dt:
        q = q.filter(Sale.created_at <= dt)

    emp = _parse_uuid(request.args.get("employee_id"))
    if emp:
        q = q.filter(Sale.employee_id == emp)

    cid = _parse_uuid(request.args.get("client_id"))
    if cid:
        q = q.filter(Sale.client_id == cid)

    method = (request.args.get("payment_method") or "").strip().lower()
    if method:
        q = q.filter(Sale.payment_method == method)

    status = (request.args.get("status") or "").strip().lower()
    if status:
        q = q.filter(Sale.status == status)

    items = q.order_by(Sale.created_at.desc()).limit(300).all()
    return jsonify({"items": [sale_to_dict(s) for s in items]}), 200


@shop_api.route("/sales/<sale_id>", methods=["GET"])
@shop_jwt_required
def get_sale(ctx: ShopContext, sale_id: str):
    sid = _parse_uuid(sale_id)
    if not sid:
        return _json_error("ID inválido.", 400)
    sale = Sale.query.filter_by(id=sid, business_id=ctx.business_id).first()
    if not sale:
        return _json_error("Venta no encontrada.", 404)
    return jsonify(sale_to_dict(sale)), 200


@shop_api.route("/sales", methods=["POST"])
@shop_jwt_required
def create_sale_endpoint(ctx: ShopContext):
    payload = request.get_json(silent=True) or {}
    items = payload.get("items")
    if not isinstance(items, list):
        return _json_error("items debe ser una lista.", 400)
    try:
        sale, replayed = create_sale(
            business_id=ctx.business_id,
            created_by_user_id=ctx.user_id,
            items=items,
            client_id=_parse_uuid(payload.get("client_id")),
            employee_id=_parse_uuid(payload.get("employee_id")),
            customer_name=payload.get("customer_name"),
            discount=payload.get("discount", 0),
            tax=payload.get("tax", 0),
            payment_method=payload.get("payment_method") or "cash",
            notes=payload.get("notes"),
            idempotency_key=payload.get("idempotency_key")
            or request.headers.get("Idempotency-Key"),
        )
        db.session.commit()
    except SaleError as exc:
        db.session.rollback()
        return _json_error(exc.message, exc.status_code)

    return jsonify({"sale": sale_to_dict(sale), "replayed": replayed}), (
        200 if replayed else 201
    )


@shop_api.route("/sales/<sale_id>/void", methods=["POST"])
@shop_jwt_required
def void_sale(ctx: ShopContext, sale_id: str):
    """Mark sale void. Does not reverse inventory (future enhancement)."""
    sid = _parse_uuid(sale_id)
    if not sid:
        return _json_error("ID inválido.", 400)
    sale = Sale.query.filter_by(id=sid, business_id=ctx.business_id).first()
    if not sale:
        return _json_error("Venta no encontrada.", 404)
    if sale.status == "void":
        return jsonify(sale_to_dict(sale)), 200
    sale.status = "void"
    db.session.commit()
    return jsonify(sale_to_dict(sale)), 200


# --- Staff ---


def _staff_row(emp: Employee) -> dict:
    u = emp.user
    return {
        "employee_id": str(emp.id),
        "user_id": str(emp.user_id),
        "email": u.email if u else None,
        "role": u.role if u else None,
        "display_name": emp.display_name,
        "phone": emp.phone,
        "is_active": emp.is_active,
    }


@shop_api.route("/staff", methods=["GET"])
@shop_jwt_required
def list_staff(ctx: ShopContext):
    rows = (
        Employee.query.filter_by(business_id=ctx.business_id)
        .order_by(Employee.id)
        .all()
    )
    return jsonify({"items": [_staff_row(e) for e in rows]}), 200


@shop_api.route("/staff/<employee_id>", methods=["PUT"])
@shop_admin_required
def update_staff(ctx: ShopContext, employee_id: str):
    eid = _parse_uuid(employee_id)
    if not eid:
        return _json_error("ID inválido.", 400)
    emp = Employee.query.filter_by(id=eid, business_id=ctx.business_id).first()
    if not emp:
        return _json_error("Empleado no encontrado.", 404)

    payload = request.get_json(silent=True) or {}
    if "display_name" in payload:
        emp.display_name = (payload.get("display_name") or "").strip() or None
    if "phone" in payload:
        emp.phone = (payload.get("phone") or "").strip() or None
    if "is_active" in payload:
        emp.is_active = bool(payload.get("is_active"))

    db.session.commit()
    return jsonify(_staff_row(emp)), 200


# --- Settings (business) ---


@shop_api.route("/settings", methods=["GET"])
@shop_jwt_required
def get_settings(ctx: ShopContext):
    b = Business.query.get(ctx.business_id)
    if not b:
        return _json_error("Negocio no encontrado.", 404)
    return jsonify(_business_to_public(b)), 200


@shop_api.route("/settings", methods=["PUT"])
@shop_admin_required
def update_settings(ctx: ShopContext):
    b = Business.query.get(ctx.business_id)
    if not b:
        return _json_error("Negocio no encontrado.", 404)

    payload = request.get_json(silent=True) or {}
    if "name" in payload:
        v = (payload.get("name") or "").strip()
        if not v:
            return _json_error("name vacío.", 400)
        b.name = v
    if "address" in payload:
        v = (payload.get("address") or "").strip()
        if not v:
            return _json_error("address vacío.", 400)
        b.address = v
    if "email" in payload:
        v = (payload.get("email") or "").strip()
        if not v:
            return _json_error("email vacío.", 400)
        b.email = v
    if "phone" in payload:
        v = (payload.get("phone") or "").strip()
        if not v:
            return _json_error("phone vacío.", 400)
        b.phone = v
    if "logo_url" in payload:
        b.logo_url = (payload.get("logo_url") or "").strip() or None
    if "business_hours_json" in payload:
        b.business_hours_json = payload.get("business_hours_json")
    if "booking_notes" in payload:
        b.booking_notes = payload.get("booking_notes")
    if "is_active" in payload:
        b.is_active = bool(payload.get("is_active"))

    db.session.commit()
    return jsonify(_business_to_public(b)), 200


@shop_api.route("/settings/logo", methods=["POST"])
@shop_admin_required
def upload_business_logo(ctx: ShopContext):
    """Multipart upload field name: ``logo`` (image, max 2 MB)."""
    from app.logo_storage import LogoError, save_business_logo

    b = Business.query.get(ctx.business_id)
    if not b:
        return _json_error("Negocio no encontrado.", 404)

    file = request.files.get("logo")
    try:
        path = save_business_logo(ctx.business_id, file)
    except LogoError as exc:
        return _json_error(exc.message, exc.status_code)

    b.logo_url = path
    db.session.commit()
    return jsonify(_business_to_public(b)), 200


@shop_api.route("/settings/logo", methods=["DELETE"])
@shop_admin_required
def delete_business_logo_endpoint(ctx: ShopContext):
    from app.logo_storage import delete_business_logo

    b = Business.query.get(ctx.business_id)
    if not b:
        return _json_error("Negocio no encontrado.", 404)

    delete_business_logo(ctx.business_id)
    b.logo_url = None
    db.session.commit()
    return jsonify(_business_to_public(b)), 200
