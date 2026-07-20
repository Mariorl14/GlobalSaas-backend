import uuid

from flask import Blueprint, jsonify, request
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import Business, Employee, User
from app.name_utils import staff_display_label, user_full_name


user_routes = Blueprint("user_routes", __name__)


def _json_error(message: str, status_code: int = 400):
    return jsonify({"error": message}), status_code


def _parse_uuid(value):
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _uuid_to_str_or_none(value):
    return str(value) if value is not None else None


def _sync_employee_display_name(user: User) -> None:
    """Keep Employee.display_name aligned with user first/last name when set."""
    if user.employee is None:
        return
    full = user_full_name(user)
    if full:
        user.employee.display_name = full[:120]


def _user_employee_to_dict(user: User):
    employee = user.employee
    return {
        "user": {
            "id": str(user.id),
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "full_name": user_full_name(user),
            "role": user.role,
            "business_id": _uuid_to_str_or_none(user.business_id),
            "is_active": user.is_active,
        },
        "employee": (
            {
                "id": str(employee.id),
                "user_id": str(employee.user_id),
                "business_id": str(employee.business_id),
                "display_name": employee.display_name,
                "label": staff_display_label(employee, user),
            }
            if employee is not None
            else None
        ),
    }


@user_routes.route("/api/users", methods=["POST"])
def create_user():
    """
    Crea un User.

    - admin / employee: requiere business_id y crea Employee asociado.
    - superadmin: sin negocio ni Employee (acceso al panel global).
    """
    payload = request.get_json(silent=True) or {}
    user_payload = payload.get("user") if isinstance(payload.get("user"), dict) else payload
    employee_payload = payload.get("employee") if isinstance(payload.get("employee"), dict) else None

    email = user_payload.get("email")
    password = user_payload.get("password")
    first_name = (user_payload.get("first_name") or "").strip() or None
    last_name = (user_payload.get("last_name") or "").strip() or None
    is_active = user_payload.get("is_active", True)
    role = (user_payload.get("role") or payload.get("role") or "employee").strip()

    if not email or not password:
        return _json_error("Missing required fields: user.email, user.password", 400)
    if not first_name:
        return _json_error("Missing required field: user.first_name", 400)

    if role not in {"admin", "employee", "superadmin"}:
        return _json_error("Invalid role. Use 'admin', 'employee' or 'superadmin'.", 400)

    existing_by_email = User.query.filter_by(email=email).first()
    if existing_by_email:
        return _json_error("User already exists for this email.", 409)

    if role == "superadmin":
        user = User(
            business_id=None,
            email=email,
            first_name=first_name[:80],
            last_name=(last_name[:80] if last_name else None),
            encrypted_password=generate_password_hash(password),
            role="superadmin",
            is_active=bool(is_active),
        )
        db.session.add(user)
        db.session.commit()
        return jsonify(_user_employee_to_dict(user)), 201

    # Admin & Employee always belong to a business.
    business_id = _parse_uuid(
        (employee_payload or {}).get("business_id") or user_payload.get("business_id")
    )
    if business_id is None:
        return _json_error("Missing or invalid 'business_id'. Must be a UUID.", 400)

    business = Business.query.get(business_id)
    if not business:
        return _json_error("Business not found for provided 'business_id'.", 404)

    user = User(
        business_id=business_id,
        email=email,
        first_name=first_name[:80],
        last_name=(last_name[:80] if last_name else None),
        encrypted_password=generate_password_hash(password),
        role=role,
        is_active=bool(is_active),
    )
    db.session.add(user)
    db.session.flush()  # generate user.id

    display = user_full_name(user) or email.split("@")[0]
    employee = Employee(
        user_id=user.id,
        business_id=business_id,
        display_name=display[:120],
    )
    db.session.add(employee)
    db.session.commit()

    return jsonify(_user_employee_to_dict(user)), 201


@user_routes.route("/api/users", methods=["GET"])
def list_users():
    business_id_raw = request.args.get("business_id")
    role = request.args.get("role")

    query = User.query

    if business_id_raw is not None:
        business_id = _parse_uuid(business_id_raw)
        if business_id is None:
            return _json_error("Invalid 'business_id'. Must be a UUID.", 400)
        query = query.filter(User.business_id == business_id)

    if role is not None:
        query = query.filter(User.role == role)

    users = query.order_by(User.email.asc()).all()
    return jsonify({"items": [_user_employee_to_dict(u) for u in users]}), 200


@user_routes.route("/api/users/<user_id>", methods=["GET"])
def get_user(user_id):
    user_uuid = _parse_uuid(user_id)
    if user_uuid is None:
        return _json_error("Invalid 'user_id'. Must be a UUID.", 400)

    user = User.query.get(user_uuid)
    if not user:
        return _json_error("User not found.", 404)

    return jsonify(_user_employee_to_dict(user)), 200


@user_routes.route("/api/users/<user_id>", methods=["PUT"])
def update_user(user_id):
    user_uuid = _parse_uuid(user_id)
    if user_uuid is None:
        return _json_error("Invalid 'user_id'. Must be a UUID.", 400)

    user = User.query.get(user_uuid)
    if not user:
        return _json_error("User not found.", 404)

    payload = request.get_json(silent=True) or {}
    user_payload = payload.get("user") if isinstance(payload.get("user"), dict) else payload
    employee_payload = payload.get("employee") if isinstance(payload.get("employee"), dict) else None
    requested_role = user_payload.get("role") if isinstance(user_payload, dict) else None

    if requested_role is not None:
        requested_role = str(requested_role).strip()
        if requested_role not in {"admin", "employee", "superadmin"}:
            return _json_error("Invalid role. Use 'admin', 'employee' or 'superadmin'.", 400)

    # User updates
    if "email" in user_payload:
        email = user_payload.get("email")
        if not email:
            return _json_error("Invalid 'user.email'.", 400)
        existing = User.query.filter(User.email == email, User.id != user.id).first()
        if existing:
            return _json_error("User already exists for this email.", 409)
        user.email = email

    if "first_name" in user_payload:
        fn = (user_payload.get("first_name") or "").strip()
        if not fn:
            return _json_error("Invalid 'user.first_name'.", 400)
        user.first_name = fn[:80]

    if "last_name" in user_payload:
        ln = (user_payload.get("last_name") or "").strip()
        user.last_name = ln[:80] if ln else None

    if "password" in user_payload:
        password = user_payload.get("password")
        if not password:
            return _json_error("Invalid 'user.password'.", 400)
        user.encrypted_password = generate_password_hash(password)

    if "is_active" in user_payload:
        user.is_active = bool(user_payload.get("is_active"))

    new_role = requested_role if requested_role is not None else user.role

    # Promote to superadmin: drop tenant binding.
    if new_role == "superadmin":
        if isinstance(user_payload, dict) and "business_id" in user_payload:
            return _json_error("Superadmin cannot have a business_id.", 400)
        if isinstance(employee_payload, dict) and "business_id" in employee_payload:
            return _json_error("Superadmin cannot have an employee business_id.", 400)
        if user.employee is not None:
            db.session.delete(user.employee)
            db.session.flush()
        user.business_id = None
        user.role = "superadmin"
        db.session.commit()
        return jsonify(_user_employee_to_dict(user)), 200

    # Shop roles need a business.
    if isinstance(user_payload, dict) and "business_id" in user_payload:
        return _json_error("Updating 'business_id' is not supported.", 400)
    if isinstance(employee_payload, dict) and "business_id" in employee_payload:
        return _json_error("Updating 'employee.business_id' is not supported.", 400)

    if user.business_id is None:
        return _json_error(
            "Cannot assign shop role without a business. Create a new shop user instead.",
            400,
        )

    if requested_role is not None and requested_role != user.role:
        user.role = requested_role

    if user.employee is None:
        db.session.add(Employee(user_id=user.id, business_id=user.business_id))
        db.session.flush()
    else:
        user.employee.business_id = user.business_id

    _sync_employee_display_name(user)

    db.session.commit()
    return jsonify(_user_employee_to_dict(user)), 200


@user_routes.route("/api/users/<user_id>", methods=["DELETE"])
def delete_user(user_id):
    user_uuid = _parse_uuid(user_id)
    if user_uuid is None:
        return _json_error("Invalid 'user_id'. Must be a UUID.", 400)

    user = User.query.get(user_uuid)
    if not user:
        return _json_error("User not found.", 404)

    # Employee is deleted by cascade from FK (employee.user_id -> user.id),
    # but we also explicitly delete it if it exists for clarity.
    if user.employee is not None:
        db.session.delete(user.employee)
    db.session.delete(user)
    db.session.commit()
    return ("", 204)

