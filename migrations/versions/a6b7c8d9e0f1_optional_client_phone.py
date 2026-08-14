"""Allow customers to omit phone on clients and appointments.

Revision ID: a6b7c8d9e0f1
Revises: f4a5b6c7d8e9
Create Date: 2026-08-14
"""

import sqlalchemy as sa
from alembic import op

revision = "a6b7c8d9e0f1"
down_revision = "f4a5b6c7d8e9"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "client",
        "phone",
        existing_type=sa.String(length=20),
        nullable=True,
    )
    op.alter_column(
        "appointment",
        "client_phone",
        existing_type=sa.String(length=20),
        nullable=True,
    )


def downgrade():
    op.execute("UPDATE client SET phone = '—' WHERE phone IS NULL OR phone = ''")
    op.execute(
        "UPDATE appointment SET client_phone = '—' "
        "WHERE client_phone IS NULL OR client_phone = ''"
    )
    op.alter_column(
        "appointment",
        "client_phone",
        existing_type=sa.String(length=20),
        nullable=False,
    )
    op.alter_column(
        "client",
        "phone",
        existing_type=sa.String(length=20),
        nullable=False,
    )
