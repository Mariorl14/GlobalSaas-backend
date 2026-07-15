"""Add inventory_movement ledger for stock audits and product sales.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-14
"""

import sqlalchemy as sa
from alembic import op

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "inventory_movement",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("movement_type", sa.String(length=40), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("quantity_before", sa.Integer(), nullable=False),
        sa.Column("quantity_after", sa.Integer(), nullable=False),
        sa.Column("unit_cost", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("unit_sale_price", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("total_cost", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("total_revenue", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("appointment_id", sa.UUID(), nullable=True),
        sa.Column("client_id", sa.UUID(), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["appointment_id"], ["appointment.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["business_id"], ["business.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["client_id"], ["client.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["user.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["product_id"], ["inventory_product.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "business_id",
            "idempotency_key",
            name="uq_inventory_movement_business_idempotency",
        ),
    )
    op.create_index(
        "ix_inventory_movement_business_created",
        "inventory_movement",
        ["business_id", "created_at"],
    )
    op.create_index(
        "ix_inventory_movement_business_type",
        "inventory_movement",
        ["business_id", "movement_type"],
    )
    op.create_index(
        "ix_inventory_movement_product_created",
        "inventory_movement",
        ["product_id", "created_at"],
    )


def downgrade():
    op.drop_index("ix_inventory_movement_product_created", table_name="inventory_movement")
    op.drop_index("ix_inventory_movement_business_type", table_name="inventory_movement")
    op.drop_index("ix_inventory_movement_business_created", table_name="inventory_movement")
    op.drop_table("inventory_movement")
