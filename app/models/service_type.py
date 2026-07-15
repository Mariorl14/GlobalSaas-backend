import uuid
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import ForeignKey
from app.extensions import db


class ServiceType(db.Model):
    __tablename__ = "service_type"

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
    description = db.Column(db.Text, nullable=True)
    duration = db.Column(db.Integer, nullable=False)  # en minutos
    price = db.Column(db.Numeric(10, 2), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    business = db.relationship("Business", back_populates="service_types")
    appointments = db.relationship("Appointment", back_populates="service_type")
