"""Create and serialize tenant POS sales."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.inventory_movements import InventoryMovementError, apply_stock_movement
from app.models import Appointment, Client, Employee, InventoryProduct, ServiceType
from app.models.sale import (
    ITEM_TYPES,
    PAYMENT_METHODS,
    SALE_STATUSES,
    Sale,
    SaleItem,
)

# Idempotency prefix: one POS ticket per completed appointment.
APPOINTMENT_SALE_KEY_PREFIX = "appointment:"


def appointment_sale_idempotency_key(appointment_id: UUID) -> str:
    return f"{APPOINTMENT_SALE_KEY_PREFIX}{appointment_id}"


def is_appointment_sale_key(key: str | None) -> bool:
    return bool(key) and str(key).startswith(APPOINTMENT_SALE_KEY_PREFIX)


class SaleError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _money(value: Any, default: str | None = "0") -> Decimal:
    if value is None or value == "":
        if default is None:
            raise SaleError("Monto inválido.")
        return Decimal(default)
    try:
        return Decimal(str(value))
    except Exception as exc:  # noqa: BLE001
        raise SaleError("Monto inválido.") from exc


def _next_invoice(business_id: UUID) -> tuple[int, str]:
    current = (
        db.session.query(func.max(Sale.invoice_seq))
        .filter(Sale.business_id == business_id)
        .scalar()
    )
    seq = int(current or 0) + 1
    return seq, f"INV-{seq:06d}"


def sale_item_to_dict(item: SaleItem) -> dict:
    return {
        "id": str(item.id),
        "item_type": item.item_type,
        "service_type_id": str(item.service_type_id) if item.service_type_id else None,
        "product_id": str(item.product_id) if item.product_id else None,
        "name": item.name,
        "quantity": item.quantity,
        "unit_price": float(item.unit_price),
        "unit_cost": float(item.unit_cost) if item.unit_cost is not None else None,
        "line_total": float(item.line_total),
        "inventory_movement_id": (
            str(item.inventory_movement_id) if item.inventory_movement_id else None
        ),
    }


def sale_to_dict(sale: Sale, *, include_items: bool = True) -> dict:
    items = sale.items or []
    services = [i for i in items if i.item_type == "service"]
    products = [i for i in items if i.item_type == "product"]
    payload = {
        "id": str(sale.id),
        "business_id": str(sale.business_id),
        "invoice_number": sale.invoice_number,
        "invoice_seq": sale.invoice_seq,
        "client_id": str(sale.client_id) if sale.client_id else None,
        "employee_id": str(sale.employee_id) if sale.employee_id else None,
        "customer_name": sale.customer_name,
        "subtotal": float(sale.subtotal),
        "discount": float(sale.discount),
        "tax": float(sale.tax),
        "total": float(sale.total),
        "payment_method": sale.payment_method,
        "status": sale.status,
        "notes": sale.notes,
        "created_by_user_id": (
            str(sale.created_by_user_id) if sale.created_by_user_id else None
        ),
        "created_at": sale.created_at.isoformat() + "Z" if sale.created_at else None,
        "services_summary": ", ".join(i.name for i in services) or "—",
        "products_summary": ", ".join(
            f"{i.name}×{i.quantity}" for i in products
        )
        or "—",
        "services_count": sum(i.quantity for i in services),
        "products_count": sum(i.quantity for i in products),
    }
    if include_items:
        payload["items"] = [sale_item_to_dict(i) for i in items]
    return payload


def create_sale(
    *,
    business_id: UUID,
    created_by_user_id: UUID | None,
    items: list[dict],
    client_id: UUID | None = None,
    employee_id: UUID | None = None,
    customer_name: str | None = None,
    discount: Any = 0,
    tax: Any = 0,
    payment_method: str = "cash",
    notes: str | None = None,
    idempotency_key: str | None = None,
) -> tuple[Sale, bool]:
    key = (idempotency_key or "").strip() or None
    if key:
        existing = Sale.query.filter_by(
            business_id=business_id, idempotency_key=key
        ).first()
        if existing:
            return existing, True

    if not items:
        raise SaleError("Agrega al menos un servicio o producto.")

    method = (payment_method or "cash").strip().lower()
    if method not in PAYMENT_METHODS:
        raise SaleError(
            f"payment_method inválido. Use: {', '.join(sorted(PAYMENT_METHODS))}"
        )

    disc = _money(discount)
    tax_amt = _money(tax)
    if disc < 0 or tax_amt < 0:
        raise SaleError("discount y tax deben ser >= 0.")

    if client_id is not None:
        client = Client.query.filter_by(id=client_id, business_id=business_id).first()
        if not client:
            raise SaleError("Cliente no válido para este negocio.")
        if not customer_name:
            customer_name = f"{client.first_name} {client.last_name}".strip()

    if employee_id is not None:
        emp = Employee.query.filter_by(id=employee_id, business_id=business_id).first()
        if not emp:
            raise SaleError("Barbero no válido para este negocio.")

    normalized: list[dict] = []
    subtotal = Decimal("0")

    for raw in items:
        itype = (raw.get("item_type") or "").strip().lower()
        if itype not in ITEM_TYPES:
            raise SaleError("item_type debe ser service o product.")
        try:
            qty = int(raw.get("quantity", 1))
        except (TypeError, ValueError) as exc:
            raise SaleError("quantity inválida.") from exc
        if qty <= 0:
            raise SaleError("quantity debe ser > 0.")

        if itype == "service":
            sid = raw.get("service_type_id")
            try:
                sid_uuid = UUID(str(sid))
            except (TypeError, ValueError) as exc:
                raise SaleError("service_type_id inválido.") from exc
            svc = ServiceType.query.filter_by(
                id=sid_uuid, business_id=business_id
            ).first()
            if not svc:
                raise SaleError("Servicio no encontrado.")
            unit = (
                _money(raw.get("unit_price"), default=None)
                if raw.get("unit_price") not in (None, "")
                else Decimal(str(svc.price))
            )
            line = (unit * qty).quantize(Decimal("0.01"))
            subtotal += line
            normalized.append(
                {
                    "item_type": "service",
                    "service_type_id": svc.id,
                    "product_id": None,
                    "name": svc.name,
                    "quantity": qty,
                    "unit_price": unit,
                    "unit_cost": None,
                    "line_total": line,
                }
            )
        else:
            pid = raw.get("product_id")
            try:
                pid_uuid = UUID(str(pid))
            except (TypeError, ValueError) as exc:
                raise SaleError("product_id inválido.") from exc
            product = InventoryProduct.query.filter_by(
                id=pid_uuid, business_id=business_id
            ).first()
            if not product:
                raise SaleError("Producto no encontrado.")
            unit = (
                _money(raw.get("unit_price"), default=None)
                if raw.get("unit_price") not in (None, "")
                else Decimal(str(product.price))
            )
            cost = (
                _money(raw.get("unit_cost"), default=None)
                if raw.get("unit_cost") not in (None, "")
                else (
                    Decimal(str(product.unit_cost))
                    if product.unit_cost is not None
                    else None
                )
            )
            line = (unit * qty).quantize(Decimal("0.01"))
            subtotal += line
            normalized.append(
                {
                    "item_type": "product",
                    "service_type_id": None,
                    "product_id": product.id,
                    "name": product.name,
                    "quantity": qty,
                    "unit_price": unit,
                    "unit_cost": cost,
                    "line_total": line,
                }
            )

    total = (subtotal - disc + tax_amt).quantize(Decimal("0.01"))
    if total < 0:
        raise SaleError("El total no puede ser negativo.")

    seq, invoice = _next_invoice(business_id)
    sale = Sale(
        business_id=business_id,
        invoice_number=invoice,
        invoice_seq=seq,
        client_id=client_id,
        employee_id=employee_id,
        customer_name=(customer_name or "").strip() or None,
        subtotal=subtotal.quantize(Decimal("0.01")),
        discount=disc.quantize(Decimal("0.01")),
        tax=tax_amt.quantize(Decimal("0.01")),
        total=total,
        payment_method=method,
        status="completed",
        notes=(notes or "").strip() or None,
        created_by_user_id=created_by_user_id,
        idempotency_key=key,
    )
    db.session.add(sale)
    db.session.flush()

    for row in normalized:
        movement_id = None
        if row["item_type"] == "product":
            try:
                movement, _, _ = apply_stock_movement(
                    business_id=business_id,
                    product_id=row["product_id"],
                    movement_type="sale",
                    quantity=row["quantity"],
                    created_by_user_id=created_by_user_id,
                    unit_cost=row["unit_cost"],
                    unit_sale_price=row["unit_price"],
                    notes=f"Venta {invoice}",
                    client_id=client_id,
                    sale_id=sale.id,
                    idempotency_key=f"{key}:product:{row['product_id']}" if key else None,
                )
                movement_id = movement.id
            except InventoryMovementError as exc:
                raise SaleError(exc.message, exc.status_code) from exc

        db.session.add(
            SaleItem(
                business_id=business_id,
                sale_id=sale.id,
                item_type=row["item_type"],
                service_type_id=row["service_type_id"],
                product_id=row["product_id"],
                name=row["name"],
                quantity=row["quantity"],
                unit_price=row["unit_price"],
                unit_cost=row["unit_cost"],
                line_total=row["line_total"],
                inventory_movement_id=movement_id,
            )
        )

    try:
        db.session.flush()
    except IntegrityError as exc:
        if key:
            existing = Sale.query.filter_by(
                business_id=business_id, idempotency_key=key
            ).first()
            if existing:
                return existing, True
        raise SaleError("No se pudo crear la venta (conflicto).", 409) from exc

    return sale, False


def ensure_sale_for_completed_appointment(
    appointment: Appointment,
    *,
    created_by_user_id: UUID | None,
    payment_method: str = "cash",
) -> tuple[Sale, bool]:
    """
    Register service revenue for a completed appointment exactly once.

    Uses idempotency_key ``appointment:<id>`` so re-marking Completada
    (or concurrent requests) does not create a second ticket.
    """
    key = appointment_sale_idempotency_key(appointment.id)
    existing = Sale.query.filter_by(
        business_id=appointment.business_id, idempotency_key=key
    ).first()
    if existing:
        return existing, True

    client_id = appointment.client_id
    if client_id is not None:
        scoped = Client.query.filter_by(
            id=client_id, business_id=appointment.business_id
        ).first()
        if not scoped:
            client_id = None

    notes = f"Cita completada {appointment.id}"
    if appointment.notes:
        notes = f"{notes} — {appointment.notes}"[:500]

    return create_sale(
        business_id=appointment.business_id,
        created_by_user_id=created_by_user_id,
        client_id=client_id,
        employee_id=appointment.employee_id,
        customer_name=(appointment.client_name or "").strip() or None,
        payment_method=payment_method,
        notes=notes,
        idempotency_key=key,
        items=[
            {
                "item_type": "service",
                "service_type_id": str(appointment.service_type_id),
                "quantity": 1,
            }
        ],
    )


def link_orphan_inventory_sales(business_id: UUID) -> int:
    """
    Create Sale tickets for inventory product sales that never got a Sale header
    (e.g. registered from Inventario before POS wiring).
    Does not change stock again.
    """
    from app.models.inventory_movement import InventoryMovement

    orphans = (
        InventoryMovement.query.filter(
            InventoryMovement.business_id == business_id,
            InventoryMovement.movement_type == "sale",
            InventoryMovement.sale_id.is_(None),
        )
        .order_by(InventoryMovement.created_at.asc())
        .all()
    )
    if not orphans:
        return 0

    linked = 0
    for m in orphans:
        product = InventoryProduct.query.filter_by(
            id=m.product_id, business_id=business_id
        ).first()
        name = product.name if product else "Producto"
        unit = (
            Decimal(str(m.unit_sale_price))
            if m.unit_sale_price is not None
            else Decimal("0")
        )
        cost = Decimal(str(m.unit_cost)) if m.unit_cost is not None else None
        qty = int(m.quantity or 0)
        if qty <= 0:
            continue
        line = (
            Decimal(str(m.total_revenue))
            if m.total_revenue is not None
            else (unit * qty).quantize(Decimal("0.01"))
        )
        seq, invoice = _next_invoice(business_id)
        sale = Sale(
            business_id=business_id,
            invoice_number=invoice,
            invoice_seq=seq,
            client_id=m.client_id,
            employee_id=None,
            customer_name=None,
            subtotal=line,
            discount=Decimal("0"),
            tax=Decimal("0"),
            total=line,
            payment_method="other",
            status="completed",
            notes=m.notes or "Venta desde inventario",
            created_by_user_id=m.created_by_user_id,
            idempotency_key=f"orphan-movement:{m.id}",
            created_at=m.created_at,
        )
        db.session.add(sale)
        db.session.flush()
        db.session.add(
            SaleItem(
                business_id=business_id,
                sale_id=sale.id,
                item_type="product",
                service_type_id=None,
                product_id=m.product_id,
                name=name,
                quantity=qty,
                unit_price=unit,
                unit_cost=cost,
                line_total=line,
                inventory_movement_id=m.id,
                created_at=m.created_at,
            )
        )
        m.sale_id = sale.id
        linked += 1
    return linked

