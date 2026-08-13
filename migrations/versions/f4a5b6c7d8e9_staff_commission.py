"""Add staff service commission percentage and sale-item snapshots.

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-08-13
"""

import sqlalchemy as sa
from alembic import op

revision = "f4a5b6c7d8e9"
down_revision = "e3f4a5b6c7d8"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "employee",
        sa.Column(
            "commission_percentage",
            sa.Numeric(precision=5, scale=2),
            nullable=False,
            server_default="50",
        ),
    )
    # Owners/admins acting as barbers keep 100% for the business unless they opt in.
    op.execute(
        """
        UPDATE employee
        SET commission_percentage = 0
        WHERE user_id IN (
            SELECT id FROM "user" WHERE role IN ('owner', 'admin')
        )
        """
    )
    op.add_column(
        "sale_item",
        sa.Column("commission_percentage", sa.Numeric(precision=5, scale=2), nullable=True),
    )
    op.add_column(
        "sale_item",
        sa.Column("staff_earnings", sa.Numeric(precision=12, scale=2), nullable=True),
    )
    op.add_column(
        "sale_item",
        sa.Column("business_earnings", sa.Numeric(precision=12, scale=2), nullable=True),
    )


def downgrade():
    op.drop_column("sale_item", "business_earnings")
    op.drop_column("sale_item", "staff_earnings")
    op.drop_column("sale_item", "commission_percentage")
    op.drop_column("employee", "commission_percentage")
