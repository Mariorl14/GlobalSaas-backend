import uuid
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import ForeignKey
from app.extensions import db


class User(db.Model):
    __tablename__ = "user"

    id = db.Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    business_id = db.Column(
        UUID(as_uuid=True),
        # SuperAdmin does not belong to any business (tenant).
        # For `superadmin` the `business_id` will be NULL; the ON DELETE is left
        # in CASCADE to match the applied migration (for now).
        ForeignKey("business.id", ondelete="CASCADE"),
        nullable=True,
    )
    email = db.Column(db.String(120), nullable=False)
    encrypted_password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # admin | employee | superadmin
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    business = db.relationship("Business", back_populates="users")
    employee = db.relationship("Employee", back_populates="user", uselist=False)
