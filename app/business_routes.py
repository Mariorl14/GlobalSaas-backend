import uuid
from datetime import datetime
from uuid import UUID

from flask import Blueprint, jsonify, request
from sqlalchemy import func

from app.extensions import db
from app.models import Appointment, Business, Client, Employee, Plan
from app.models.sale import Sale
from app.slug_utils import (
    is_valid_public_slug,
    public_slug_for_business,
    regenerate_public_slug,
)


business_routes = Blueprint("business_routes", __name__)


def _json_error(message: str, status_code: int = 400):
    return jsonify({"error": message}), status_code


def _parse_uuid(value):
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _month_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    now = now or datetime.utcnow()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


def _empty_stats() -> dict:
    return {
        "employees_count": 0,
        "customers_count": 0,
        "appointments_month": 0,
        "monthly_revenue": 0.0,
    }


def _stats_for_businesses(business_ids: list[UUID]) -> dict[UUID, dict]:
    """Batch platform stats for Super Admin directory / detail."""
    out: dict[UUID, dict] = {bid: _empty_stats() for bid in business_ids}
    if not business_ids:
        return out

    month_start, month_end = _month_bounds()

    emp_rows = (
        db.session.query(Employee.business_id, func.count(Employee.id))
        .filter(
            Employee.business_id.in_(business_ids),
            Employee.is_active.is_(True),
        )
        .group_by(Employee.business_id)
        .all()
    )
    for bid, n in emp_rows:
        out[bid]["employees_count"] = int(n)

    client_rows = (
        db.session.query(Client.business_id, func.count(Client.id))
        .filter(Client.business_id.in_(business_ids))
        .group_by(Client.business_id)
        .all()
    )
    for bid, n in client_rows:
        out[bid]["customers_count"] = int(n)

    appt_rows = (
        db.session.query(Appointment.business_id, func.count(Appointment.id))
        .filter(
            Appointment.business_id.in_(business_ids),
            Appointment.start_time >= month_start,
            Appointment.start_time < month_end,
        )
        .group_by(Appointment.business_id)
        .all()
    )
    for bid, n in appt_rows:
        out[bid]["appointments_month"] = int(n)

    rev_rows = (
        db.session.query(
            Sale.business_id,
            func.coalesce(func.sum(Sale.total), 0),
        )
        .filter(
            Sale.business_id.in_(business_ids),
            Sale.status == "completed",
            Sale.created_at >= month_start,
            Sale.created_at < month_end,
        )
        .group_by(Sale.business_id)
        .all()
    )
    for bid, total in rev_rows:
        out[bid]["monthly_revenue"] = round(float(total or 0), 2)

    return out


def _business_to_dict(business: Business, stats: dict | None = None):
    payload = {
        "id": str(business.id),
        "plan_id": str(business.plan_id) if business.plan_id else None,
        "name": business.name,
        "address": business.address,
        "email": business.email,
        "phone": business.phone,
        "is_active": business.is_active,
        "logo_url": business.logo_url,
        "public_slug": business.public_slug,
        "public_description": business.public_description,
        "allow_any_barber": business.allow_any_barber,
    }
    if stats is not None:
        payload.update(stats)
    return payload


@business_routes.route("/api/business", methods=["POST"])
def create_business():
    payload = request.get_json(silent=True) or {}

    name = payload.get("name")
    address = payload.get("address")
    email = payload.get("email")
    phone = payload.get("phone")

    is_active = payload.get("is_active", True)

    plan_id_raw = payload.get("plan_id")
    plan_id = _parse_uuid(plan_id_raw)
    if plan_id_raw is not None and plan_id is None:
        return _json_error("Invalid 'plan_id'. Must be a UUID or null.", 400)

    if not name or not address or not email or not phone:
        return _json_error("Missing required fields: name, address, email, phone", 400)

    if plan_id is not None:
        plan = Plan.query.get(plan_id)
        if not plan:
            return _json_error("Plan not found for provided 'plan_id'.", 404)

    tmp_slug = "tmp-" + str(uuid.uuid4()).replace("-", "")[:20]
    business = Business(
        plan_id=plan_id,
        name=name,
        address=address,
        email=email,
        phone=phone,
        is_active=bool(is_active),
        public_slug=tmp_slug[:120],
        public_description=(payload.get("public_description") or "").strip() or None,
        allow_any_barber=bool(payload.get("allow_any_barber", True)),
    )

    db.session.add(business)
    db.session.flush()
    business.public_slug = public_slug_for_business(business.name, business.id)
    db.session.commit()

    return jsonify(_business_to_dict(business)), 201


@business_routes.route("/api/business", methods=["GET"])
def list_businesses():
    page_raw = request.args.get("page", "1")
    per_page_raw = request.args.get("per_page", "20")

    try:
        page = max(1, int(page_raw))
        per_page = max(1, int(per_page_raw))
    except ValueError:
        return _json_error("'page' and 'per_page' must be integers.", 400)

    pagination = Business.query.order_by(Business.name.asc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    ids = [b.id for b in pagination.items]
    stats_map = _stats_for_businesses(ids)
    items = [_business_to_dict(b, stats_map.get(b.id)) for b in pagination.items]

    return jsonify(
        {
            "items": items,
            "page": page,
            "per_page": per_page,
            "total": pagination.total,
            "pages": pagination.pages,
        }
    ), 200


@business_routes.route("/api/business/<business_id>", methods=["GET"])
def get_business(business_id):
    business_uuid = _parse_uuid(business_id)
    if business_uuid is None:
        return _json_error("Invalid 'business_id'. Must be a UUID.", 400)

    business = Business.query.get(business_uuid)
    if not business:
        return _json_error("Business not found.", 404)

    stats = _stats_for_businesses([business.id]).get(business.id, _empty_stats())
    return jsonify(_business_to_dict(business, stats)), 200


@business_routes.route("/api/business/<business_id>/stats", methods=["GET"])
def get_business_stats(business_id):
    """Platform metrics for Super Admin business detail (Estadísticas y actividad)."""
    business_uuid = _parse_uuid(business_id)
    if business_uuid is None:
        return _json_error("Invalid 'business_id'. Must be a UUID.", 400)

    business = Business.query.get(business_uuid)
    if not business:
        return _json_error("Business not found.", 404)

    month_start, month_end = _month_bounds()
    stats = _stats_for_businesses([business.id]).get(business.id, _empty_stats())
    return jsonify(
        {
            **stats,
            "period": {
                "from": month_start.isoformat() + "Z",
                "to": (month_end).isoformat() + "Z",
                "label": "month",
            },
        }
    ), 200


@business_routes.route("/api/business/<business_id>", methods=["PUT"])
def update_business(business_id):
    business_uuid = _parse_uuid(business_id)
    if business_uuid is None:
        return _json_error("Invalid 'business_id'. Must be a UUID.", 400)

    business = Business.query.get(business_uuid)
    if not business:
        return _json_error("Business not found.", 404)

    payload = request.get_json(silent=True) or {}

    name = payload.get("name")
    address = payload.get("address")
    email = payload.get("email")
    phone = payload.get("phone")

    is_active = payload.get("is_active", business.is_active)

    plan_id_raw = payload.get("plan_id", business.plan_id)
    plan_id = _parse_uuid(plan_id_raw) if plan_id_raw is not None else None
    if "plan_id" in payload and plan_id_raw is not None and plan_id is None:
        return _json_error("Invalid 'plan_id'. Must be a UUID or null.", 400)

    # If plan_id is changing to a concrete UUID, verify it exists.
    if plan_id is not None:
        plan = Plan.query.get(plan_id)
        if not plan:
            return _json_error("Plan not found for provided 'plan_id'.", 404)

    missing = [k for k, v in {"name": name, "address": address, "email": email, "phone": phone}.items() if not v]
    if missing:
        return _json_error(f"Missing required fields: {', '.join(missing)}", 400)

    business.name = name
    business.address = address
    business.email = email
    business.phone = phone
    business.is_active = bool(is_active)
    business.plan_id = plan_id

    if "public_description" in payload:
        business.public_description = (payload.get("public_description") or "").strip() or None
    if "allow_any_barber" in payload:
        business.allow_any_barber = bool(payload.get("allow_any_barber"))

    if "public_slug" in payload:
        ns = (payload.get("public_slug") or "").strip().lower()
        if not ns:
            return _json_error("public_slug cannot be empty.", 400)
        if not is_valid_public_slug(ns):
            return _json_error(
                "public_slug: solo minúsculas, números y guiones (ej. mi-barberia).",
                400,
            )
        taken = (
            Business.query.filter(
                func.lower(Business.public_slug) == ns,
                Business.id != business.id,
            )
            .first()
        )
        if taken:
            return _json_error("Este slug ya está en uso.", 409)
        business.public_slug = ns

    db.session.commit()
    return jsonify(_business_to_dict(business)), 200


@business_routes.route(
    "/api/business/<business_id>/regenerate-public-slug", methods=["POST"]
)
def regenerate_business_public_slug(business_id):
    business_uuid = _parse_uuid(business_id)
    if business_uuid is None:
        return _json_error("Invalid 'business_id'. Must be a UUID.", 400)

    business = Business.query.get(business_uuid)
    if not business:
        return _json_error("Business not found.", 404)

    business.public_slug = regenerate_public_slug(
        business.name, exclude_business_id=business.id
    )
    db.session.commit()
    return jsonify(_business_to_dict(business)), 200


@business_routes.route("/api/business/<business_id>/logo", methods=["POST"])
def upload_business_logo_sa(business_id):
    """Super Admin: multipart field ``logo``."""
    from app.logo_storage import LogoError, save_business_logo

    business_uuid = _parse_uuid(business_id)
    if business_uuid is None:
        return _json_error("Invalid 'business_id'. Must be a UUID.", 400)

    business = Business.query.get(business_uuid)
    if not business:
        return _json_error("Business not found.", 404)

    file = request.files.get("logo")
    try:
        path = save_business_logo(business_uuid, file)
    except LogoError as exc:
        return _json_error(exc.message, exc.status_code)

    business.logo_url = path
    db.session.commit()
    stats = _stats_for_businesses([business.id]).get(business.id, _empty_stats())
    return jsonify(_business_to_dict(business, stats)), 200


@business_routes.route("/api/business/<business_id>/logo", methods=["DELETE"])
def delete_business_logo_sa(business_id):
    from app.logo_storage import delete_business_logo

    business_uuid = _parse_uuid(business_id)
    if business_uuid is None:
        return _json_error("Invalid 'business_id'. Must be a UUID.", 400)

    business = Business.query.get(business_uuid)
    if not business:
        return _json_error("Business not found.", 404)

    delete_business_logo(business_uuid)
    business.logo_url = None
    db.session.commit()
    stats = _stats_for_businesses([business.id]).get(business.id, _empty_stats())
    return jsonify(_business_to_dict(business, stats)), 200


@business_routes.route("/api/business/<business_id>", methods=["DELETE"])
def delete_business(business_id):
    business_uuid = _parse_uuid(business_id)
    if business_uuid is None:
        return _json_error("Invalid 'business_id'. Must be a UUID.", 400)

    business = Business.query.get(business_uuid)
    if not business:
        return _json_error("Business not found.", 404)

    db.session.delete(business)
    db.session.commit()

    # Client can treat this as success.
    return ("", 204)

