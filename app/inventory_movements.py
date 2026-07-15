"""
Inventory stock movements — apply quantity changes with an audit ledger.

Quantity is always stored positive; movement_type decides stock direction.
Only movement_type == "sale" contributes to product sales revenue metrics.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Appointment, Client, InventoryProduct
from app.models.inventory_movement import (
    DECREASE_TYPES,
    INCREASE_TYPES,
    MOVEMENT_TYPES,
    InventoryMovement,
)


class InventoryMovementError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _money(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception as exc:  # noqa: BLE001
        raise InventoryMovementError("Monto inválido.") from exc


def movement_to_dict(m: InventoryMovement, *, product_name: str | None = None) -> dict:
    return {
        "id": str(m.id),
        "business_id": str(m.business_id),
        "product_id": str(m.product_id),
        "product_name": product_name,
        "movement_type": m.movement_type,
        "quantity": m.quantity,
        "quantity_before": m.quantity_before,
        "quantity_after": m.quantity_after,
        "unit_cost": float(m.unit_cost) if m.unit_cost is not None else None,
        "unit_sale_price": float(m.unit_sale_price) if m.unit_sale_price is not None else None,
        "total_cost": float(m.total_cost) if m.total_cost is not None else None,
        "total_revenue": float(m.total_revenue) if m.total_revenue is not None else None,
        "notes": m.notes,
        "appointment_id": str(m.appointment_id) if m.appointment_id else None,
        "client_id": str(m.client_id) if m.client_id else None,
        "sale_id": str(m.sale_id) if getattr(m, "sale_id", None) else None,
        "created_by_user_id": str(m.created_by_user_id) if m.created_by_user_id else None,
        "idempotency_key": m.idempotency_key,
        "created_at": m.created_at.isoformat() + "Z" if m.created_at else None,
        "is_sale": m.movement_type == "sale",
    }


def apply_stock_movement(
    *,
    business_id: UUID,
    product_id: UUID,
    movement_type: str,
    quantity: int,
    created_by_user_id: UUID | None = None,
    unit_cost: Any = None,
    unit_sale_price: Any = None,
    notes: str | None = None,
    appointment_id: UUID | None = None,
    client_id: UUID | None = None,
    sale_id: UUID | None = None,
    idempotency_key: str | None = None,
    update_product_cost: bool = False,
) -> tuple[InventoryMovement, InventoryProduct, bool]:
    """
    Apply a stock movement in the current DB session (caller commits).

    Returns (movement, product, replayed).
    replayed=True when an existing idempotency_key was reused (no double stock change).
    """
    mtype = (movement_type or "").strip().lower()
    if mtype not in MOVEMENT_TYPES:
        raise InventoryMovementError(
            f"movement_type inválido. Use uno de: {', '.join(sorted(MOVEMENT_TYPES))}"
        )

    if not isinstance(quantity, int):
        try:
            quantity = int(quantity)
        except (TypeError, ValueError) as exc:
            raise InventoryMovementError("quantity debe ser un entero.") from exc

    if quantity <= 0:
        raise InventoryMovementError("quantity debe ser mayor que 0.")

    key = (idempotency_key or "").strip() or None
    if key:
        existing = InventoryMovement.query.filter_by(
            business_id=business_id,
            idempotency_key=key,
        ).first()
        if existing:
            product = InventoryProduct.query.filter_by(
                id=existing.product_id, business_id=business_id
            ).first()
            if not product:
                raise InventoryMovementError("Producto no encontrado.", 404)
            return existing, product, True

    product = (
        InventoryProduct.query.filter_by(id=product_id, business_id=business_id)
        .with_for_update()
        .first()
    )
    if not product:
        raise InventoryMovementError("Producto no encontrado.", 404)

    if appointment_id is not None:
        appt = Appointment.query.filter_by(
            id=appointment_id, business_id=business_id
        ).first()
        if not appt:
            raise InventoryMovementError("Cita no válida para este negocio.", 400)

    if client_id is not None:
        client = Client.query.filter_by(id=client_id, business_id=business_id).first()
        if not client:
            raise InventoryMovementError("Cliente no válido para este negocio.", 400)

    before = int(product.stock or 0)
    if mtype in INCREASE_TYPES:
        after = before + quantity
    elif mtype in DECREASE_TYPES:
        after = before - quantity
        if after < 0:
            raise InventoryMovementError(
                f"Stock insuficiente. Disponible: {before}, solicitado: {quantity}."
            )
    else:
        raise InventoryMovementError("movement_type no soportado.")

    cost = _money(unit_cost)
    if cost is None and product.unit_cost is not None:
        cost = Decimal(str(product.unit_cost))

    sale_price = _money(unit_sale_price)
    if mtype == "sale":
        if sale_price is None:
            sale_price = Decimal(str(product.price))
        if sale_price < 0:
            raise InventoryMovementError("unit_sale_price inválido.")
        total_revenue = (sale_price * quantity).quantize(Decimal("0.01"))
        total_cost = (cost * quantity).quantize(Decimal("0.01")) if cost is not None else None
    else:
        # Non-sale reductions must not create revenue.
        sale_price = None
        total_revenue = None
        total_cost = (cost * quantity).quantize(Decimal("0.01")) if cost is not None else None
        if mtype in INCREASE_TYPES and cost is not None and update_product_cost:
            product.unit_cost = cost

    movement = InventoryMovement(
        business_id=business_id,
        product_id=product.id,
        movement_type=mtype,
        quantity=quantity,
        quantity_before=before,
        quantity_after=after,
        unit_cost=cost,
        unit_sale_price=sale_price,
        total_cost=total_cost,
        total_revenue=total_revenue,
        notes=(notes or "").strip() or None,
        appointment_id=appointment_id,
        client_id=client_id,
        created_by_user_id=created_by_user_id,
        idempotency_key=key,
        sale_id=sale_id,
    )
    try:
        with db.session.begin_nested():
            product.stock = after
            db.session.add(movement)
            db.session.flush()
    except IntegrityError as exc:
        if key:
            existing = InventoryMovement.query.filter_by(
                business_id=business_id,
                idempotency_key=key,
            ).first()
            if existing:
                db.session.refresh(product)
                return existing, product, True
        raise InventoryMovementError(
            "No se pudo registrar el movimiento (conflicto).", 409
        ) from exc

    return movement, product, False


def apply_stock_correction_if_needed(
    *,
    business_id: UUID,
    product: InventoryProduct,
    new_stock: int,
    created_by_user_id: UUID | None,
    notes: str | None = None,
) -> InventoryMovement | None:
    """When product stock is edited directly, record a correction movement."""
    before = int(product.stock or 0)
    if new_stock == before:
        return None
    if new_stock < 0:
        raise InventoryMovementError("stock >= 0.")
    delta = abs(new_stock - before)
    mtype = "correction_increase" if new_stock > before else "correction_decrease"
    # Set stock temporarily to before so apply_stock_movement math is correct
    product.stock = before
    movement, _, _ = apply_stock_movement(
        business_id=business_id,
        product_id=product.id,
        movement_type=mtype,
        quantity=delta,
        created_by_user_id=created_by_user_id,
        notes=notes or "Corrección manual de stock",
    )
    return movement
