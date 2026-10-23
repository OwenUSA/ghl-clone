"""inspection reports: the approved roofing guides each report carries

Revision ID: e5b2d8f17a39
Revises: d7a3c5e1f804
Create Date: 2026-10-23

One nullable column. No DROP, no UPDATE.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'e5b2d8f17a39'
down_revision: str | Sequence[str] | None = 'd7a3c5e1f804'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

J = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')


def upgrade() -> None:
    op.add_column('inspection_reports', sa.Column('guides', J, nullable=True))


def downgrade() -> None:
    op.drop_column('inspection_reports', 'guides')
