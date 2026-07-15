import uuid
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import ForeignKey
from app.extensions import db


class Business(db.Model):
    __tablename__ = "business"

    id = db.Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    plan_id = db.Column(
        UUID(as_uuid=True),
        ForeignKey("plan.id", ondelete="SET NULL"),
        nullable=True,
    )
    name = db.Column(db.String(120), nullable=False)
    address = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    logo_url = db.Column(db.String(500), nullable=True)
    business_hours_json = db.Column(db.Text, nullable=True)
    booking_notes = db.Column(db.Text, nullable=True)
    public_slug = db.Column(db.String(120), nullable=False, unique=True)
    public_description = db.Column(db.Text, nullable=True)
    allow_any_barber = db.Column(db.Boolean, nullable=False, default=True)
    # ISO 3166-1 alpha-2 (e.g. CR, MX, US) for phone normalization in notifications.
    country_code = db.Column(db.String(2), nullable=True)
    # JSON goals for Business Insights (monthly_revenue, appointments, etc.).
    insights_goals_json = db.Column(db.Text, nullable=True)

    plan = db.relationship("Plan", back_populates="businesses")
    inventory_products = db.relationship(
        "InventoryProduct", back_populates="business", cascade="all, delete-orphan"
    )
    service_types = db.relationship(
        "ServiceType", back_populates="business", cascade="all, delete-orphan"
    )
    users = db.relationship("User", back_populates="business", cascade="all, delete-orphan")
    employees = db.relationship(
        "Employee", back_populates="business", cascade="all, delete-orphan"
    )
    appointments = db.relationship(
        "Appointment", back_populates="business", cascade="all, delete-orphan"
    )
    clients = db.relationship(
        "Client", back_populates="business", cascade="all, delete-orphan"
    )