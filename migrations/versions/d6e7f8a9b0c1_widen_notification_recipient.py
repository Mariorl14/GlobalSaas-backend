"""Widen notification_log.recipient for email addresses.

Revision ID: d6e7f8a9b0c1
Revises: c3d4e5f6a7b8
Create Date: 2026-07-15
"""

import sqlalchemy as sa
from alembic import op

revision = "d6e7f8a9b0c1"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "notification_log",
        "recipient",
        existing_type=sa.String(length=40),
        type_=sa.String(length=255),
        existing_nullable=True,
    )


def downgrade():
    op.alter_column(
        "notification_log",
        "recipient",
        existing_type=sa.String(length=255),
        type_=sa.String(length=40),
        existing_nullable=True,
    )
