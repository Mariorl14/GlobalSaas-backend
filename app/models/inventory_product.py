import uuid
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import ForeignKey
from app.extensions import db


class InventoryProduct(db.Model):
    __tablename__ = "inventory_product"

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
    name = db.Column(db.String(120), nullable=False)
    category = db.Column(db.String(120), nullable=True)
    price = db.Column(db.Numeric(10, 2), nullable=False)
    unit_cost = db.Column(db.Numeric(10, 2), nullable=True)
    supplier = db.Column(db.String(200), nullable=True)
    stock = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    min_stock = db.Column(db.Integer, nullable=False, default=0)

    business = db.relationship("Business", back_populates="inventory_products")
