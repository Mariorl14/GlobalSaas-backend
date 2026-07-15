"""Barber portal: tenant-scoped client, extended inventory/service/business/employee

Revision ID: d3e5f7a9b0d4
Revises: c2d4f6a8b1c3
Create Date: 2026-04-09
"""

from alembic import op
import sqlalchemy as sa


revision = "d3e5f7a9b0d4"
down_revision = "c2d4f6a8b1c3"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("client", schema=None) as batch_op:
        batch_op.add_column(sa.Column("business_id", sa.UUID(), nullable=True))
        batch_op.add_column(sa.Column("email", sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column("notes", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("preferred_employee_id", sa.UUID(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_client_business", "business", ["business_id"], ["id"], ondelete="CASCADE"
        )
        batch_op.create_foreign_key(
            "fk_client_preferred_employee",
            "employee",
            ["preferred_employee_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("service_type", schema=None) as batch_op:
        batch_op.add_column(sa.Column("description", sa.Text(), nullable=True))

    with op.batch_alter_table("inventory_product", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("category", sa.String(length=120), nullable=True)
        )
        batch_op.add_column(
            sa.Column("unit_cost", sa.Numeric(precision=10, scale=2), nullable=True)
        )
        batch_op.add_column(
            sa.Column("supplier", sa.String(length=200), nullable=True)
        )

    with op.batch_alter_table("business", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("logo_url", sa.String(length=500), nullable=True)
        )
        batch_op.add_column(sa.Column("business_hours_json", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("booking_notes", sa.Text(), nullable=True))

    with op.batch_alter_table("employee", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("display_name", sa.String(length=120), nullable=True)
        )
        batch_op.add_column(sa.Column("phone", sa.String(length=20), nullable=True))
        batch_op.add_column(
            sa.Column(
                "is_active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            )
        )


def downgrade():
    with op.batch_alter_table("employee", schema=None) as batch_op:
        batch_op.drop_column("is_active")
        batch_op.drop_column("phone")
        batch_op.drop_column("display_name")

    with op.batch_alter_table("business", schema=None) as batch_op:
        batch_op.drop_column("booking_notes")
        batch_op.drop_column("business_hours_json")
        batch_op.drop_column("logo_url")

    with op.batch_alter_table("inventory_product", schema=None) as batch_op:
        batch_op.drop_column("supplier")
        batch_op.drop_column("unit_cost")
        batch_op.drop_column("category")

    with op.batch_alter_table("service_type", schema=None) as batch_op:
        batch_op.drop_column("description")

    with op.batch_alter_table("client", schema=None) as batch_op:
        batch_op.drop_constraint("fk_client_preferred_employee", type_="foreignkey")
        batch_op.drop_constraint("fk_client_business", type_="foreignkey")
        batch_op.drop_column("preferred_employee_id")
        batch_op.drop_column("notes")
        batch_op.drop_column("email")
        batch_op.drop_column("business_id")
