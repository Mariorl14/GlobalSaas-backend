import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db

NOTIFICATION_CHANNELS = frozenset({"whatsapp", "sms", "email"})
NOTIFICATION_TYPES = frozenset(
    {"appointment_confirmation", "appointment_reminder", "appointment_cancellation"}
)
NOTIFICATION_STATUSES = frozenset(
    {"pending", "sent", "delivered", "read", "failed", "skipped"}
)


class NotificationLog(db.Model):
    __tablename__ = "notification_log"
    __table_args__ = (
        UniqueConstraint(
            "appointment_id",
            "channel",
            "notification_type",
            name="uq_notification_log_appointment_channel_type",
        ),
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
    appointment_id = db.Column(
        UUID(as_uuid=True),
        ForeignKey("appointment.id", ondelete="CASCADE"),
        nullable=False,
    )
    client_id = db.Column(
        UUID(as_uuid=True),
        ForeignKey("client.id", ondelete="SET NULL"),
        nullable=True,
    )
    channel = db.Column(db.String(20), nullable=False)
    notification_type = db.Column(db.String(40), nullable=False)
    provider = db.Column(db.String(40), nullable=True)
    recipient = db.Column(db.String(40), nullable=True)
    provider_message_sid = db.Column(db.String(64), nullable=True)
    template_identifier = db.Column(db.String(120), nullable=True)
    status = db.Column(db.String(20), nullable=False, default="pending")
    attempt_count = db.Column(Integer, nullable=False, default=0)
    error_code = db.Column(db.String(40), nullable=True)
    error_message = db.Column(Text, nullable=True)
    sent_at = db.Column(DateTime, nullable=True)
    created_at = db.Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    business = db.relationship("Business", backref="notification_logs")
    appointment = db.relationship("Appointment", backref="notification_logs")
    client = db.relationship("Client", backref="notification_logs")
