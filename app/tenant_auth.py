"""JWT helpers for multi-tenant shop (barber) portal — not super admin."""

from __future__ import annotations

import uuid
from functools import wraps
from typing import Callable, NamedTuple, Optional, Tuple, TypeVar

from flask import jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required

# Tenant portal roles
SHOP_ROLES = frozenset({"admin", "owner", "employee"})
# Full shop management (settings, staff, inventory, goals)
SHOP_MANAGER_ROLES = frozenset({"admin", "owner"})
SHOP_ADMIN_ROLE = "admin"  # legacy alias; prefer SHOP_MANAGER_ROLES
SHOP_STAFF_ROLE = "employee"

F = TypeVar("F", bound=Callable)


class ShopContext(NamedTuple):
    user_id: uuid.UUID
    business_id: uuid.UUID
    role: str
    employee_id: Optional[uuid.UUID]

    @property
    def is_manager(self) -> bool:
        return self.role in SHOP_MANAGER_ROLES

    @property
    def is_staff(self) -> bool:
        return self.role == SHOP_STAFF_ROLE

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"


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
        # Browser CORS preflight has no Authorization header.
        if request.method == "OPTIONS":
            return "", 200
        ctx, err = get_shop_context()
        if err is not None:
            return err[0], err[1]
        return fn(ctx, *args, **kwargs)

    return decorated  # type: ignore[return-value]


def shop_admin_required(fn: F) -> F:
    """Owner or admin of the shop (full management)."""

    @wraps(fn)
    @jwt_required()
    def decorated(*args, **kwargs):
        if request.method == "OPTIONS":
            return "", 200
        ctx, err = get_shop_context()
        if err is not None:
            return err[0], err[1]
        if not ctx.is_manager:
            return (
                jsonify(
                    {
                        "error": "Solo el propietario o administrador de la tienda puede hacer esto."
                    }
                ),
                403,
            )
        return fn(ctx, *args, **kwargs)

    return decorated  # type: ignore[return-value]


def shop_manager_required(fn: F) -> F:
    """Alias for shop_admin_required (owner + admin)."""
    return shop_admin_required(fn)


def shop_owner_required(fn: F) -> F:
    """Shop owner only (cancel/delete appointments, etc.)."""

    @wraps(fn)
    @jwt_required()
    def decorated(*args, **kwargs):
        if request.method == "OPTIONS":
            return "", 200
        ctx, err = get_shop_context()
        if err is not None:
            return err[0], err[1]
        if not ctx.is_owner:
            return (
                jsonify({"error": "Solo el propietario del negocio puede hacer esto."}),
                403,
            )
        return fn(ctx, *args, **kwargs)

    return decorated  # type: ignore[return-value]
