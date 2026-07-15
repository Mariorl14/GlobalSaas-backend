"""Add insights_goals_json to business for Business Insights goals.

Revision ID: a1b2c3d4e5f6
Revises: f5a6b7c8d9e0
Create Date: 2026-07-14
"""

import sqlalchemy as sa
from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "f5a6b7c8d9e0"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "business",
        sa.Column("insights_goals_json", sa.Text(), nullable=True),
    )


def downgrade():
    op.drop_column("business", "insights_goals_json")
