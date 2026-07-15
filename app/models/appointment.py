import uuid
from datetime import datetime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import ForeignKey, DateTime
from app.extensions import db


class Appointment(db.Model):
    __tablename__ = "appointment"

    id = db.Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    client_id = db.Column(
        UUID(as_uuid=True),
        ForeignKey("client.id", ondelete="CASCADE"),
        nullable=False,
    )
    service_type_id = db.Column(
        UUID(as_uuid=True),
        ForeignKey("service_type.id", ondelete="CASCADE"),
        nullable=False,
    )
    business_id = db.Column(
        UUID(as_uuid=True),
        ForeignKey("business.id", ondelete="CASCADE"),
        nullable=False,
    )
    employee_id = db.Column(
        UUID(as_uuid=True),
        ForeignKey("employee.id", ondelete="CASCADE"),
        nullable=False,
    )
    client_name = db.Column(db.String(120), nullable=False)
    client_email = db.Column(db.String(120), nullable=False)
    client_phone = db.Column(db.String(20), nullable=False)
    start_time = db.Column(DateTime, nullable=False)
    end_time = db.Column(DateTime, nullable=False)
    # scheduled | confirmed | completed | canceled | no_show | pending (legacy)
    status = db.Column(db.String(20), nullable=False)
    notes = db.Column(db.Text, nullable=True)

    client = db.relationship("Client", back_populates="appointments")
    service_type = db.relationship("ServiceType", back_populates="appointments")
    business = db.relationship("Business", back_populates="appointments")
    employee = db.relationship("Employee", back_populates="appointments")
