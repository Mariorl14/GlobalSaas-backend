"""Add employee work_hours_json for per-staff schedules.

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-07-20
"""

import sqlalchemy as sa
from alembic import op

revision = "f8a9b0c1d2e3"
down_revision = "e7f8a9b0c1d2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("employee", sa.Column("work_hours_json", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("employee", "work_hours_json")
