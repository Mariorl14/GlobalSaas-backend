"""Notification log and business country_code for WhatsApp confirmations

Revision ID: f5a6b7c8d9e0
Revises: e4f8a0b1c2d5
Create Date: 2026-07-13
"""

import sqlalchemy as sa
from alembic import op

revision = "f5a6b7c8d9e0"
down_revision = "e4f8a0b1c2d5"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "business",
        sa.Column("country_code", sa.String(length=2), nullable=True),
    )

    op.create_table(
        "notification_log",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column("appointment_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=True),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("notification_type", sa.String(length=40), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=True),
        sa.Column("recipient", sa.String(length=40), nullable=True),
        sa.Column("provider_message_sid", sa.String(length=64), nullable=True),
        sa.Column("template_identifier", sa.String(length=120), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(length=40), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["appointment_id"], ["appointment.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["business_id"], ["business.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["client_id"], ["client.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "appointment_id",
            "channel",
            "notification_type",
            name="uq_notification_log_appointment_channel_type",
        ),
    )
    op.create_index(
        "ix_notification_log_provider_message_sid",
        "notification_log",
        ["provider_message_sid"],
        unique=False,
    )


def downgrade():
    op.drop_index("ix_notification_log_provider_message_sid", table_name="notification_log")
    op.drop_table("notification_log")
    op.drop_column("business", "country_code")
