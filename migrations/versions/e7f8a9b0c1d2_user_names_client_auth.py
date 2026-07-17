"""Add user names and client login credentials.

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
Create Date: 2026-07-17
"""

import sqlalchemy as sa
from alembic import op

revision = "e7f8a9b0c1d2"
down_revision = "d6e7f8a9b0c1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("user", sa.Column("first_name", sa.String(length=80), nullable=True))
    op.add_column("user", sa.Column("last_name", sa.String(length=80), nullable=True))

    op.add_column("client", sa.Column("username", sa.String(length=80), nullable=True))
    op.add_column(
        "client", sa.Column("encrypted_password", sa.String(length=255), nullable=True)
    )
    op.create_index(
        "ix_client_business_username",
        "client",
        ["business_id", "username"],
        unique=True,
    )


def downgrade():
    op.drop_index("ix_client_business_username", table_name="client")
    op.drop_column("client", "encrypted_password")
    op.drop_column("client", "username")
    op.drop_column("user", "last_name")
    op.drop_column("user", "first_name")
