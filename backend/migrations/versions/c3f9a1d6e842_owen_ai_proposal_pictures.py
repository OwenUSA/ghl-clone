"""Owen AI: optional step pictures on a created proposal draft

Revision ID: c3f9a1d6e842
Revises: b8e1d4c7a209
Create Date: 2026-11-09

Five nullable columns on owen_ai_proposal_drafts. No DROP, no UPDATE of existing data.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'c3f9a1d6e842'
down_revision: str | Sequence[str] | None = 'b8e1d4c7a209'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

J = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')
T = 'owen_ai_proposal_drafts'


def upgrade() -> None:
    op.add_column(T, sa.Column('pictures_status', sa.String(12), nullable=True))
    op.add_column(T, sa.Column('pictures', J, nullable=True))
    op.add_column(T, sa.Column('pictures_cost_usd', sa.Float(), nullable=True))
    op.add_column(T, sa.Column('pictures_error', sa.Text(), nullable=True))
    op.add_column(T, sa.Column('pictures_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    for c in ('pictures_at', 'pictures_error', 'pictures_cost_usd', 'pictures', 'pictures_status'):
        op.drop_column(T, c)
