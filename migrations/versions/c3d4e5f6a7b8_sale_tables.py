"""Add sale / sale_item tables and optional sale_id on inventory_movement.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-07-14
"""

import sqlalchemy as sa
from alembic import op

revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "sale",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column("invoice_number", sa.String(length=40), nullable=False),
        sa.Column("invoice_seq", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=True),
        sa.Column("employee_id", sa.UUID(), nullable=True),
        sa.Column("customer_name", sa.String(length=120), nullable=True),
        sa.Column("subtotal", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("discount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("tax", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("total", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("payment_method", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["business.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["client_id"], ["client.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["user.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["employee_id"], ["employee.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "business_id", "idempotency_key", name="uq_sale_business_idempotency"
        ),
        sa.UniqueConstraint(
            "business_id", "invoice_number", name="uq_sale_business_invoice"
        ),
    )
    op.create_index("ix_sale_business_created", "sale", ["business_id", "created_at"])
    op.create_index("ix_sale_business_client", "sale", ["business_id", "client_id"])

    op.create_table(
        "sale_item",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column("sale_id", sa.UUID(), nullable=False),
        sa.Column("item_type", sa.String(length=20), nullable=False),
        sa.Column("service_type_id", sa.UUID(), nullable=True),
        sa.Column("product_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("unit_cost", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("line_total", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("inventory_movement_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["business.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["inventory_movement_id"],
            ["inventory_movement.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["product_id"], ["inventory_product.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["sale_id"], ["sale.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["service_type_id"], ["service_type.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sale_item_sale", "sale_item", ["sale_id"])

    op.add_column(
        "inventory_movement",
        sa.Column("sale_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_inventory_movement_sale_id",
        "inventory_movement",
        "sale",
        ["sale_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade():
    op.drop_constraint(
        "fk_inventory_movement_sale_id", "inventory_movement", type_="foreignkey"
    )
    op.drop_column("inventory_movement", "sale_id")
    op.drop_index("ix_sale_item_sale", table_name="sale_item")
    op.drop_table("sale_item")
    op.drop_index("ix_sale_business_client", table_name="sale")
    op.drop_index("ix_sale_business_created", table_name="sale")
    op.drop_table("sale")
