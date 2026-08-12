"""Add user.personal_email for staff booking alerts.

Revision ID: c1d2e3f4a5b6
Revises: b0c1d2e3f4a5
Create Date: 2026-08-12
"""

import sqlalchemy as sa
from alembic import op

revision = "c1d2e3f4a5b6"
down_revision = "b0c1d2e3f4a5"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "user",
        sa.Column("personal_email", sa.String(length=120), nullable=True),
    )


def downgrade():
    op.drop_column("user", "personal_email")
