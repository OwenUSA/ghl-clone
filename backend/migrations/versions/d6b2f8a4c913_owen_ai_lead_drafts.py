"""Owen AI: new leads (customer + job) the owner approves before anything reaches Zuper

Revision ID: d6b2f8a4c913
Revises: a7c3e5f1b902
Create Date: 2026-11-19

One new table. No DROP, no UPDATE of existing data.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'd6b2f8a4c913'
down_revision: str | Sequence[str] | None = 'a7c3e5f1b902'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

J = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')


def upgrade() -> None:
    op.create_table(
        'owen_ai_lead_drafts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('customer_name', sa.String(200), nullable=False),
        sa.Column('board', sa.String(120), nullable=False),
        sa.Column('category_uid', sa.String(64), nullable=False),
        sa.Column('job_title', sa.String(300), nullable=False),
        sa.Column('spec', J, nullable=False),
        sa.Column('status', sa.String(10), nullable=False, server_default='pending'),
        sa.Column('customer_uid', sa.String(64), nullable=True),
        sa.Column('job_uid', sa.String(64), nullable=True),
        sa.Column('job_number', sa.String(40), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('decided_by', sa.String(200), nullable=True),
        sa.Column('thread_id', sa.Integer(), nullable=True),
    )
    op.create_index('ix_owen_ai_lead_drafts_status', 'owen_ai_lead_drafts', ['status'])
    op.create_index('ix_owen_ai_lead_drafts_thread_id', 'owen_ai_lead_drafts', ['thread_id'])


def downgrade() -> None:
    op.drop_table('owen_ai_lead_drafts')
