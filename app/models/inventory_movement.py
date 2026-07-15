"""Inventory stock movement (ledger) for tenant inventory audit + product sales."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db

MOVEMENT_TYPES = frozenset(
    {
        "purchase",
        "restock",
        "sale",
        "damaged",
        "lost",
        "internal_use",
        "correction_increase",
        "correction_decrease",
        "return",
    }
)

INCREASE_TYPES = frozenset(
    {"purchase", "restock", "correction_increase", "return"}
)
DECREASE_TYPES = frozenset(
    {"sale", "damaged", "lost", "internal_use", "correction_decrease"}
)


class InventoryMovement(db.Model):
    __tablename__ = "inventory_movement"
    __table_args__ = (
        UniqueConstraint(
            "business_id",
            "idempotency_key",
            name="uq_inventory_movement_business_idempotency",
        ),
        Index("ix_inventory_movement_business_created", "business_id", "created_at"),
        Index("ix_inventory_movement_business_type", "business_id", "movement_type"),
        Index("ix_inventory_movement_product_created", "product_id", "created_at"),
    )

    id = db.Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    business_id = db.Column(
        UUID(as_uuid=True),
        ForeignKey("business.id", ondelete="CASCADE"),
        nullable=False,
    )
    product_id = db.Column(
        UUID(as_uuid=True),
        ForeignKey("inventory_product.id", ondelete="CASCADE"),
        nullable=False,
    )
    movement_type = db.Column(db.String(40), nullable=False)
    quantity = db.Column(Integer, nullable=False)
    quantity_before = db.Column(Integer, nullable=False)
    quantity_after = db.Column(Integer, nullable=False)
    unit_cost = db.Column(Numeric(10, 2), nullable=True)
    unit_sale_price = db.Column(Numeric(10, 2), nullable=True)
    total_cost = db.Column(Numeric(12, 2), nullable=True)
    total_revenue = db.Column(Numeric(12, 2), nullable=True)
    notes = db.Column(Text, nullable=True)
    appointment_id = db.Column(
        UUID(as_uuid=True),
        ForeignKey("appointment.id", ondelete="SET NULL"),
        nullable=True,
    )
    client_id = db.Column(
        UUID(as_uuid=True),
        ForeignKey("client.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_by_user_id = db.Column(
        UUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    idempotency_key = db.Column(db.String(120), nullable=True)
    sale_id = db.Column(
        UUID(as_uuid=True),
        ForeignKey("sale.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = db.Column(DateTime, nullable=False, default=datetime.utcnow)

    business = db.relationship("Business", backref="inventory_movements")
    product = db.relationship("InventoryProduct", backref="movements")
    appointment = db.relationship("Appointment", backref="inventory_movements")
    client = db.relationship("Client", backref="inventory_movements")
    created_by = db.relationship("User", backref="inventory_movements")
    sale = db.relationship("Sale", backref="inventory_movements")
