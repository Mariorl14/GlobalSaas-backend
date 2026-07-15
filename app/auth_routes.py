import os
import uuid

from flask import Blueprint, jsonify, request
from flask_jwt_extended import create_access_token
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db
from app.models import Employee, User


auth = Blueprint("auth", __name__)


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


def _json_user(user: User):
    return {
        "id": str(user.id),
        "email": user.email,
        "role": user.role,
        "business_id": _uuid_to_str_or_none(user.business_id),
        "is_active": user.is_active,
    }


@auth.route("/api/auth/signup", methods=["POST"])
def signup():
    payload = request.get_json(silent=True) or {}

    # Bootstrap SuperAdmin (currently the only supported auth flow):
    # - `user`: { email, password, is_active? }
    # - `seed_token`: token provided by env (to prevent anyone from creating superAdmin)
    user_payload = payload.get("user") if isinstance(payload.get("user"), dict) else payload

    email = user_payload.get("email")
    password = user_payload.get("password")
    is_active = user_payload.get("is_active", True)

    if not email or not password:
        return _json_error("Missing required fields: user.email, user.password", 400)

    # Reject payloads with "business" or "employee" (not supported for superAdmin yet).
    if payload.get("business") is not None or payload.get("business_id") is not None:
        return _json_error(
            "For now, signup only supports SuperAdmin. Do not send business fields.",
            400,
        )
    if payload.get("employee") is not None:
        return _json_error(
            "For now, signup only supports SuperAdmin. Do not send employee fields.",
            400,
        )

    seed_token = payload.get("seed_token") or user_payload.get("seed_token")
    expected_seed_token = os.getenv("SUPERADMIN_SEED_TOKEN")
    if not expected_seed_token:
        return _json_error(
            "SUPERADMIN_SEED_TOKEN is not configured. Set it in your .env.",
            500,
        )
    if not seed_token or seed_token != expected_seed_token:
        return _json_error("Invalid seed_token for SuperAdmin bootstrap.", 401)

    existing_by_email = User.query.filter_by(email=email).first()
    if existing_by_email:
        # Avoid ambiguity in signin.
        return _json_error("User already exists for this email.", 409)

    encrypted_password = generate_password_hash(password)
    user = User(
        business_id=None,
        email=email,
        encrypted_password=encrypted_password,
        role="superadmin",
        is_active=bool(is_active),
    )
    db.session.add(user)
    db.session.commit()

    access_token = create_access_token(
        identity=str(user.id),
        additional_claims={"role": user.role, "business_id": None},
    )

    return jsonify({"access_token": access_token, "user": _json_user(user)}), 201


@auth.route("/api/auth/signin", methods=["POST"])
def signin():
    payload = request.get_json(silent=True) or {}

    email = payload.get("email")
    password = payload.get("password")

    if not email or not password:
        return _json_error("Missing required fields: email, password", 400)

    # For now, signin only applies to SuperAdmin.
    user = User.query.filter_by(email=email, role="superadmin").first()
    if not user:
        return _json_error("Invalid credentials.", 401)

    if not user.is_active:
        return _json_error("User is not active.", 403)

    if not check_password_hash(user.encrypted_password, password):
        return _json_error("Invalid credentials.", 401)

    access_token = create_access_token(
        identity=str(user.id),
        additional_claims={"role": user.role, "business_id": None},
    )

    return (
        jsonify(
            {
                "access_token": access_token,
                "user": _json_user(user),
            }
        ),
        200,
    )


@auth.route("/api/auth/shop/signin", methods=["POST"])
def shop_signin():
    """Tenant users (shop admin / staff). Separate from super admin signin."""
    payload = request.get_json(silent=True) or {}
    email = payload.get("email")
    password = payload.get("password")

    if not email or not password:
        return _json_error("Missing required fields: email, password", 400)

    user = User.query.filter_by(email=email).first()
    if not user or user.role not in ("admin", "employee"):
        return _json_error("Invalid credentials.", 401)

    if user.business_id is None:
        return _json_error("Invalid credentials.", 401)

    if not user.is_active:
        return _json_error("User is not active.", 403)

    if not check_password_hash(user.encrypted_password, password):
        return _json_error("Invalid credentials.", 401)

    emp = Employee.query.filter_by(user_id=user.id).first()
    employee_id_str = str(emp.id) if emp else None

    access_token = create_access_token(
        identity=str(user.id),
        additional_claims={
            "role": user.role,
            "business_id": str(user.business_id),
            "employee_id": employee_id_str,
        },
    )

    return (
        jsonify(
            {
                "access_token": access_token,
                "user": _json_user(user),
            }
        ),
        200,
    )

