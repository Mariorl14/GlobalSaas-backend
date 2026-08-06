"""Allow nullable sale.payment_method for unrecorded history.

Revision ID: b0c1d2e3f4a5
Revises: a9b0c1d2e3f4
Create Date: 2026-07-20
"""

import sqlalchemy as sa
from alembic import op

revision = "b0c1d2e3f4a5"
down_revision = "a9b0c1d2e3f4"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "sale",
        "payment_method",
        existing_type=sa.String(length=20),
        nullable=True,
    )


def downgrade():
    op.execute(
        "UPDATE sale SET payment_method = 'cash' WHERE payment_method IS NULL"
    )
    op.alter_column(
        "sale",
        "payment_method",
        existing_type=sa.String(length=20),
        nullable=False,
    )
