"""Add appointment reschedule proposal fields and cancel reason.

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-08-13
"""

import sqlalchemy as sa
from alembic import op

revision = "e3f4a5b6c7d8"
down_revision = "d2e3f4a5b6c7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("appointment", sa.Column("proposed_start_time", sa.DateTime(), nullable=True))
    op.add_column("appointment", sa.Column("proposed_end_time", sa.DateTime(), nullable=True))
    op.add_column("appointment", sa.Column("previous_start_time", sa.DateTime(), nullable=True))
    op.add_column("appointment", sa.Column("previous_end_time", sa.DateTime(), nullable=True))
    op.add_column("appointment", sa.Column("reschedule_token", sa.String(length=64), nullable=True))
    op.add_column("appointment", sa.Column("reschedule_token_expires_at", sa.DateTime(), nullable=True))
    op.add_column("appointment", sa.Column("reschedule_message", sa.Text(), nullable=True))
    op.add_column("appointment", sa.Column("cancel_reason", sa.String(length=40), nullable=True))
    op.add_column("appointment", sa.Column("cancel_message", sa.Text(), nullable=True))
    op.create_index(
        "ix_appointment_reschedule_token",
        "appointment",
        ["reschedule_token"],
        unique=True,
    )


def downgrade():
    op.drop_index("ix_appointment_reschedule_token", table_name="appointment")
    op.drop_column("appointment", "cancel_message")
    op.drop_column("appointment", "cancel_reason")
    op.drop_column("appointment", "reschedule_message")
    op.drop_column("appointment", "reschedule_token_expires_at")
    op.drop_column("appointment", "reschedule_token")
    op.drop_column("appointment", "previous_end_time")
    op.drop_column("appointment", "previous_start_time")
    op.drop_column("appointment", "proposed_end_time")
    op.drop_column("appointment", "proposed_start_time")
