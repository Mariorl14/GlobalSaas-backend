"""JWT helpers for public customer portal (Client accounts)."""

from __future__ import annotations

import uuid
from functools import wraps
from typing import Callable, NamedTuple, Optional, Tuple, TypeVar

from flask import jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required

CUSTOMER_ROLE = "customer"

F = TypeVar("F", bound=Callable)


class CustomerContext(NamedTuple):
    client_id: uuid.UUID
    business_id: uuid.UUID


def get_customer_context() -> Tuple[Optional[CustomerContext], Optional[Tuple]]:
    claims = get_jwt()
    if claims.get("role") != CUSTOMER_ROLE:
        return None, (jsonify({"error": "Acceso solo para clientes."}), 403)
    bid = claims.get("business_id")
    cid = claims.get("client_id") or get_jwt_identity()
    if not bid or not cid:
        return None, (jsonify({"error": "Token de cliente inválido."}), 401)
    try:
        return (
            CustomerContext(uuid.UUID(str(cid)), uuid.UUID(str(bid))),
            None,
        )
    except (TypeError, ValueError):
        return None, (jsonify({"error": "Token de cliente inválido."}), 401)


def customer_jwt_required(fn: F) -> F:
    @wraps(fn)
    @jwt_required()
    def decorated(*args, **kwargs):
        if request.method == "OPTIONS":
            return "", 200
        ctx, err = get_customer_context()
        if err is not None:
            return err[0], err[1]
        return fn(ctx, *args, **kwargs)

    return decorated  # type: ignore[return-value]
