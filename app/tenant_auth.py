"""JWT helpers for multi-tenant shop (barber) portal — not super admin."""

from __future__ import annotations

import uuid
from functools import wraps
from typing import Callable, NamedTuple, Optional, Tuple, TypeVar

from flask import jsonify
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required

# DB roles for tenant users (maps to product language: shop_admin, barber_staff)
SHOP_ROLES = frozenset({"admin", "employee"})
SHOP_ADMIN_ROLE = "admin"

F = TypeVar("F", bound=Callable)


class ShopContext(NamedTuple):
    user_id: uuid.UUID
    business_id: uuid.UUID
    role: str
    employee_id: Optional[uuid.UUID]


def get_shop_context() -> Tuple[Optional[ShopContext], Optional[Tuple]]:
    """
    Valid only inside a @jwt_required request.
    Returns (ctx, None) or (None, (jsonify(...), status)).
    """
    claims = get_jwt()
    role = claims.get("role")
    if role not in SHOP_ROLES:
        return None, (jsonify({"error": "Acceso solo para usuarios de tienda."}), 403)

    bid = claims.get("business_id")
    if not bid:
        return None, (jsonify({"error": "Token sin negocio asignado."}), 403)

    try:
        user_id = uuid.UUID(str(get_jwt_identity()))
        business_id = uuid.UUID(str(bid))
    except (TypeError, ValueError):
        return None, (jsonify({"error": "Token inválido."}), 401)

    eid_raw = claims.get("employee_id")
    employee_id = None
    if eid_raw:
        try:
            employee_id = uuid.UUID(str(eid_raw))
        except (TypeError, ValueError):
            employee_id = None

    return ShopContext(user_id, business_id, role, employee_id), None


def shop_jwt_required(fn: F) -> F:
    """Inject ShopContext as the first argument after wrapping with JWT + tenant check."""

    @wraps(fn)
    @jwt_required()
    def decorated(*args, **kwargs):
        ctx, err = get_shop_context()
        if err is not None:
            return err[0], err[1]
        return fn(ctx, *args, **kwargs)

    return decorated  # type: ignore[return-value]


def shop_admin_required(fn: F) -> F:
    @wraps(fn)
    @jwt_required()
    def decorated(*args, **kwargs):
        ctx, err = get_shop_context()
        if err is not None:
            return err[0], err[1]
        if ctx.role != SHOP_ADMIN_ROLE:
            return jsonify({"error": "Solo el administrador de la tienda puede hacer esto."}), 403
        return fn(ctx, *args, **kwargs)

    return decorated  # type: ignore[return-value]
