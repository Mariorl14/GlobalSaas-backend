import uuid
from decimal import Decimal, InvalidOperation

from flask import Blueprint, jsonify, request

from app.extensions import db
from app.models import Plan


plan_routes = Blueprint("plan_routes", __name__)


def _json_error(message: str, status_code: int = 400):
    return jsonify({"error": message}), status_code


def _parse_uuid(value):
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _parse_price(value):
    if value is None:
        return None
    try:
        d = Decimal(str(value))
        if d < 0:
            return None
        return d
    except (InvalidOperation, TypeError, ValueError):
        return None


def _parse_non_negative_int(value):
    if value is None:
        return None
    try:
        i = int(value)
        if i < 0:
            return None
        return i
    except (TypeError, ValueError):
        return None


def _plan_to_dict(plan: Plan):
    return {
        "id": str(plan.id),
        "name": plan.name,
        "price": float(plan.price),
        "max_employees": plan.max_employees,
        "max_appointments": plan.max_appointments,
    }


@plan_routes.route("/api/plan", methods=["POST"])
def create_plan():
    payload = request.get_json(silent=True) or {}

    name = payload.get("name")
    price = _parse_price(payload.get("price"))
    max_employees = _parse_non_negative_int(payload.get("max_employees"))
    max_appointments = _parse_non_negative_int(payload.get("max_appointments"))

    if not name or not str(name).strip():
        return _json_error("Missing required field: name", 400)
    if price is None:
        return _json_error("Invalid or missing 'price'. Must be a number >= 0.", 400)
    if max_employees is None:
        return _json_error(
            "Invalid or missing 'max_employees'. Must be an integer >= 0.", 400
        )
    if max_appointments is None:
        return _json_error(
            "Invalid or missing 'max_appointments'. Must be an integer >= 0.", 400
        )

    plan = Plan(
        name=str(name).strip(),
        price=price,
        max_employees=max_employees,
        max_appointments=max_appointments,
    )
    db.session.add(plan)
    db.session.commit()

    return jsonify(_plan_to_dict(plan)), 201


@plan_routes.route("/api/plan", methods=["GET"])
def list_plans():
    page_raw = request.args.get("page", "1")
    per_page_raw = request.args.get("per_page", "20")

    try:
        page = max(1, int(page_raw))
        per_page = max(1, int(per_page_raw))
    except ValueError:
        return _json_error("'page' and 'per_page' must be integers.", 400)

    pagination = Plan.query.order_by(Plan.name.asc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    items = [_plan_to_dict(p) for p in pagination.items]

    return jsonify(
        {
            "items": items,
            "page": page,
            "per_page": per_page,
            "total": pagination.total,
            "pages": pagination.pages,
        }
    ), 200


@plan_routes.route("/api/plan/<plan_id>", methods=["GET"])
def get_plan(plan_id):
    plan_uuid = _parse_uuid(plan_id)
    if plan_uuid is None:
        return _json_error("Invalid 'plan_id'. Must be a UUID.", 400)

    plan = Plan.query.get(plan_uuid)
    if not plan:
        return _json_error("Plan not found.", 404)

    return jsonify(_plan_to_dict(plan)), 200


@plan_routes.route("/api/plan/<plan_id>", methods=["PUT"])
def update_plan(plan_id):
    plan_uuid = _parse_uuid(plan_id)
    if plan_uuid is None:
        return _json_error("Invalid 'plan_id'. Must be a UUID.", 400)

    plan = Plan.query.get(plan_uuid)
    if not plan:
        return _json_error("Plan not found.", 404)

    payload = request.get_json(silent=True) or {}

    name = payload.get("name")
    price = _parse_price(payload.get("price"))
    max_employees = _parse_non_negative_int(payload.get("max_employees"))
    max_appointments = _parse_non_negative_int(payload.get("max_appointments"))

    if not name or not str(name).strip():
        return _json_error("Missing required field: name", 400)
    if price is None:
        return _json_error("Invalid or missing 'price'. Must be a number >= 0.", 400)
    if max_employees is None:
        return _json_error(
            "Invalid or missing 'max_employees'. Must be an integer >= 0.", 400
        )
    if max_appointments is None:
        return _json_error(
            "Invalid or missing 'max_appointments'. Must be an integer >= 0.", 400
        )

    plan.name = str(name).strip()
    plan.price = price
    plan.max_employees = max_employees
    plan.max_appointments = max_appointments

    db.session.commit()
    return jsonify(_plan_to_dict(plan)), 200


@plan_routes.route("/api/plan/<plan_id>", methods=["DELETE"])
def delete_plan(plan_id):
    plan_uuid = _parse_uuid(plan_id)
    if plan_uuid is None:
        return _json_error("Invalid 'plan_id'. Must be a UUID.", 400)

    plan = Plan.query.get(plan_uuid)
    if not plan:
        return _json_error("Plan not found.", 404)

    db.session.delete(plan)
    db.session.commit()

    return ("", 204)
