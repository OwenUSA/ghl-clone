"""insights (agent #10): conversation reads and tags, daily snapshots, pinned KPIs

Revision ID: b9e5d27a4c18
Revises: a4d81e6c2f97
Create Date: 2026-10-22

Four CREATE TABLEs. No DROP, no UPDATE. The tagger is off unless INSIGHTS_ENABLED.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'b9e5d27a4c18'
down_revision: str | Sequence[str] | None = 'a4d81e6c2f97'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

J = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        'conversation_reads',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('source_ref', sa.String(length=120), nullable=False),
        sa.Column('read_at', TS, server_default=sa.func.now(), nullable=False),
        sa.Column('tags', sa.Integer(), server_default='0', nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('source_ref'),
    )
    op.create_table(
        'conversation_signals',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('source_ref', sa.String(length=120), nullable=False),
        sa.Column('kind', sa.String(length=10), nullable=False),
        sa.Column('at', TS, nullable=False),
        sa.Column('phone', sa.String(length=10), nullable=True),
        sa.Column('inbound', sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column('job_uid', sa.String(length=64), nullable=True),
        sa.Column('job_number', sa.String(length=40), nullable=True),
        sa.Column('board', sa.String(length=120), nullable=True),
        sa.Column('customer_name', sa.String(length=200), nullable=True),
        sa.Column('technician', sa.String(length=120), nullable=True),
        sa.Column('tag', sa.String(length=30), nullable=False),
        sa.Column('quote', sa.Text(), nullable=False),
        sa.Column('created_at', TS, server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('source_ref', 'tag', name='uq_signal_source_tag'),
    )
    for col in ('at', 'phone', 'job_uid', 'tag'):
        op.create_index(op.f('ix_conversation_signals_%s' % col), 'conversation_signals', [col])
    op.create_table(
        'insight_snapshots',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('values', J, nullable=True),
        sa.Column('created_at', TS, server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('day'),
    )
    op.create_table(
        'insight_saved_kpis',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('spec', J, nullable=False),
        sa.Column('position', sa.Integer(), server_default='0', nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', TS, server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    for t in ('insight_saved_kpis', 'insight_snapshots', 'conversation_signals',
              'conversation_reads'):
        op.drop_table(t)
