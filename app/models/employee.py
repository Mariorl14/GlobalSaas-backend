import uuid
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import ForeignKey
from app.extensions import db


class Employee(db.Model):
    __tablename__ = "employee"

    id = db.Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id = db.Column(
        UUID(as_uuid=True),
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,  # 1 user tiene 1 employee
    )
    business_id = db.Column(
        UUID(as_uuid=True),
        ForeignKey("business.id", ondelete="CASCADE"),
        nullable=False,
    )
    display_name = db.Column(db.String(120), nullable=True)
    phone = db.Column(db.String(20), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    # Optional weekly schedule (same JSON shape as business.business_hours_json).
    # NULL / blank = follow the business hours for every day.
    work_hours_json = db.Column(db.Text, nullable=True)

    user = db.relationship("User", back_populates="employee")
    business = db.relationship("Business", back_populates="employees")
    appointments = db.relationship("Appointment", back_populates="employee")
