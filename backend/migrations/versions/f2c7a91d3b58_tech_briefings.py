"""tech briefings: the field technician's day-before job briefing

Revision ID: f2c7a91d3b58
Revises: e8b3c62f9a10
Create Date: 2026-10-21

One CREATE TABLE. No DROP, no UPDATE. Off unless TECH_BRIEFING_ENABLED.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'f2c7a91d3b58'
down_revision: str | Sequence[str] | None = 'e8b3c62f9a10'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'tech_briefings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(length=200), nullable=False),
        sa.Column('technician', sa.String(length=120), nullable=False),
        sa.Column('phone', sa.String(length=10), nullable=True),
        sa.Column('brief_date', sa.Date(), nullable=False),
        sa.Column('job_numbers', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'),
                  nullable=True),
        sa.Column('body', sa.Text(), nullable=True),
        sa.Column('state', sa.String(length=12), server_default='building', nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('model', sa.String(length=120), nullable=True),
        sa.Column('input_tokens', sa.Integer(), server_default='0', nullable=False),
        sa.Column('output_tokens', sa.Integer(), server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('key'),
    )
    op.create_index(op.f('ix_tech_briefings_brief_date'), 'tech_briefings', ['brief_date'])
    op.create_index(op.f('ix_tech_briefings_created_at'), 'tech_briefings', ['created_at'])


def downgrade() -> None:
    op.drop_index(op.f('ix_tech_briefings_created_at'), table_name='tech_briefings')
    op.drop_index(op.f('ix_tech_briefings_brief_date'), table_name='tech_briefings')
    op.drop_table('tech_briefings')
