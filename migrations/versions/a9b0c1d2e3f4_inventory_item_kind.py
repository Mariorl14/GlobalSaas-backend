"""Add inventory item_kind and allow nullable sale price.

Revision ID: a9b0c1d2e3f4
Revises: f8a9b0c1d2e3
Create Date: 2026-07-20
"""

import sqlalchemy as sa
from alembic import op

revision = "a9b0c1d2e3f4"
down_revision = "f8a9b0c1d2e3"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "inventory_product",
        sa.Column(
            "item_kind",
            sa.String(length=32),
            nullable=False,
            server_default="UNCLASSIFIED",
        ),
    )
    op.alter_column(
        "inventory_product",
        "price",
        existing_type=sa.Numeric(precision=10, scale=2),
        nullable=True,
    )
    # Keep server_default only for migrate; app always sets item_kind explicitly.
    op.alter_column(
        "inventory_product",
        "item_kind",
        server_default=None,
        existing_type=sa.String(length=32),
        existing_nullable=False,
    )


def downgrade():
    op.execute(
        "UPDATE inventory_product SET price = 0 WHERE price IS NULL"
    )
    op.alter_column(
        "inventory_product",
        "price",
        existing_type=sa.Numeric(precision=10, scale=2),
        nullable=False,
    )
    op.drop_column("inventory_product", "item_kind")
