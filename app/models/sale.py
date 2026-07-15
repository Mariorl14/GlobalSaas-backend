"""POS sale header + line items (tenant-scoped)."""

from __future__ import annotations

import uuid
from datetime import datetime

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

PAYMENT_METHODS = frozenset({"cash", "card", "sinpe", "transfer", "other"})
SALE_STATUSES = frozenset({"completed", "void"})
ITEM_TYPES = frozenset({"service", "product"})


class Sale(db.Model):
    __tablename__ = "sale"
    __table_args__ = (
        UniqueConstraint(
            "business_id",
            "invoice_number",
            name="uq_sale_business_invoice",
        ),
        UniqueConstraint(
            "business_id",
            "idempotency_key",
            name="uq_sale_business_idempotency",
        ),
        Index("ix_sale_business_created", "business_id", "created_at"),
        Index("ix_sale_business_client", "business_id", "client_id"),
    )

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id = db.Column(
        UUID(as_uuid=True),
        ForeignKey("business.id", ondelete="CASCADE"),
        nullable=False,
    )
    invoice_number = db.Column(db.String(40), nullable=False)
    invoice_seq = db.Column(Integer, nullable=False)
    client_id = db.Column(
        UUID(as_uuid=True),
        ForeignKey("client.id", ondelete="SET NULL"),
        nullable=True,
    )
    employee_id = db.Column(
        UUID(as_uuid=True),
        ForeignKey("employee.id", ondelete="SET NULL"),
        nullable=True,
    )
    customer_name = db.Column(db.String(120), nullable=True)
    subtotal = db.Column(Numeric(12, 2), nullable=False, default=0)
    discount = db.Column(Numeric(12, 2), nullable=False, default=0)
    tax = db.Column(Numeric(12, 2), nullable=False, default=0)
    total = db.Column(Numeric(12, 2), nullable=False, default=0)
    payment_method = db.Column(db.String(20), nullable=False, default="cash")
    status = db.Column(db.String(20), nullable=False, default="completed")
    notes = db.Column(Text, nullable=True)
    created_by_user_id = db.Column(
        UUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    idempotency_key = db.Column(db.String(120), nullable=True)
    created_at = db.Column(DateTime, nullable=False, default=datetime.utcnow)

    business = db.relationship("Business", backref="sales")
    client = db.relationship("Client", backref="sales")
    employee = db.relationship("Employee", backref="sales")
    items = db.relationship(
        "SaleItem",
        back_populates="sale",
        cascade="all, delete-orphan",
        order_by="SaleItem.created_at",
    )


class SaleItem(db.Model):
    __tablename__ = "sale_item"
    __table_args__ = (
        Index("ix_sale_item_sale", "sale_id"),
    )

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id = db.Column(
        UUID(as_uuid=True),
        ForeignKey("business.id", ondelete="CASCADE"),
        nullable=False,
    )
    sale_id = db.Column(
        UUID(as_uuid=True),
        ForeignKey("sale.id", ondelete="CASCADE"),
        nullable=False,
    )
    item_type = db.Column(db.String(20), nullable=False)
    service_type_id = db.Column(
        UUID(as_uuid=True),
        ForeignKey("service_type.id", ondelete="SET NULL"),
        nullable=True,
    )
    product_id = db.Column(
        UUID(as_uuid=True),
        ForeignKey("inventory_product.id", ondelete="SET NULL"),
        nullable=True,
    )
    name = db.Column(db.String(160), nullable=False)
    quantity = db.Column(Integer, nullable=False, default=1)
    unit_price = db.Column(Numeric(10, 2), nullable=False)
    unit_cost = db.Column(Numeric(10, 2), nullable=True)
    line_total = db.Column(Numeric(12, 2), nullable=False)
    inventory_movement_id = db.Column(
        UUID(as_uuid=True),
        ForeignKey("inventory_movement.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = db.Column(DateTime, nullable=False, default=datetime.utcnow)

    sale = db.relationship("Sale", back_populates="items")
