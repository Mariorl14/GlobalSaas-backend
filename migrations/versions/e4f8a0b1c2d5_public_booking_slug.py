"""Public booking slug and description for businesses

Revision ID: e4f8a0b1c2d5
Revises: d3e5f7a9b0d4
Create Date: 2026-04-09
"""

import re

import sqlalchemy as sa
from alembic import op

revision = "e4f8a0b1c2d5"
down_revision = "d3e5f7a9b0d4"
branch_labels = None
depends_on = None


def _slug_base(name: str) -> str:
    if not name:
        return "barberia"
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-") or "barberia"
    return s[:100]


def upgrade():
    op.add_column(
        "business",
        sa.Column("public_slug", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "business",
        sa.Column("public_description", sa.Text(), nullable=True),
    )
    op.add_column(
        "business",
        sa.Column(
            "allow_any_barber",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )

    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, name FROM business")).fetchall()
    for row in rows:
        bid, name = row[0], row[1] or ""
        base = _slug_base(str(name))
        suffix = str(bid).replace("-", "").replace("{", "").replace("}", "")[:8]
        candidate = f"{base}-{suffix}"[:120]
        conn.execute(
            sa.text("UPDATE business SET public_slug = :slug WHERE id = :id"),
            {"slug": candidate, "id": bid},
        )

    op.alter_column("business", "public_slug", nullable=False)
    op.create_unique_constraint("uq_business_public_slug", "business", ["public_slug"])


def downgrade():
    op.drop_constraint("uq_business_public_slug", "business", type_="unique")
    op.drop_column("business", "allow_any_barber")
    op.drop_column("business", "public_description")
    op.drop_column("business", "public_slug")
