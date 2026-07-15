import uuid
from sqlalchemy.dialects.postgresql import UUID
from app.extensions import db


class Plan(db.Model):
    __tablename__ = "plan"

    id = db.Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name = db.Column(db.String(120), nullable=False)
    price = db.Column(db.Numeric(10, 2), nullable=False)
    max_employees = db.Column(db.Integer, nullable=False)
    max_appointments = db.Column(db.Integer, nullable=False)

    businesses = db.relationship("Business", back_populates="plan")
