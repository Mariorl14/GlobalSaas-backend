"""Add appointment.source for walk-in vs scheduled origin.

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-08-12
"""

import sqlalchemy as sa
from alembic import op

revision = "d2e3f4a5b6c7"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "appointment",
        sa.Column("source", sa.String(length=20), nullable=True),
    )


def downgrade():
    op.drop_column("appointment", "source")
