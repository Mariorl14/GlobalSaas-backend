"""Allow SuperAdmin to exist without a Business (business_id nullable).

Revision ID: c2d4f6a8b1c3
Revises: e21c9d59b4fe
Create Date: 2026-03-26
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c2d4f6a8b1c3"
down_revision = "e21c9d59b4fe"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("user", schema=None) as batch_op:
        # For SuperAdmin we only need to allow `business_id = NULL`.
        # (For now we avoid touching the FK to avoid depending on the name of the constraint.)
        batch_op.alter_column("business_id", existing_type=sa.UUID(), nullable=True)


def downgrade():
    with op.batch_alter_table("user", schema=None) as batch_op:
        batch_op.alter_column("business_id", existing_type=sa.UUID(), nullable=False)

