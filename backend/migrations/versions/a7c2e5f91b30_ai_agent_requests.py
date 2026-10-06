"""ai_agent_requests — a caller's change request a voice agent passed on

Revision ID: a7c2e5f91b30
Revises: e3a9c5d1f207
Create Date: 2026-10-06

One NEW table. No DROP, no UPDATE, no backfill. DECISIONS.md, the 2026-10-06 amendment
"Retell voice agents" (C3, app/agent_requests.py): the idempotency key of a request and the
record of where it was filed (an urgent task or a Dispatch item).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a7c2e5f91b30'
down_revision: str | Sequence[str] | None = 'e3a9c5d1f207'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'ai_agent_requests',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(length=80), nullable=False),
        sa.Column('owen_call_id', sa.String(length=120), nullable=False),
        sa.Column('agent_id', sa.Integer(), nullable=True),
        sa.Column('agent_name', sa.String(length=200), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('request', sa.Text(), nullable=False),
        sa.Column('caller_number', sa.String(length=40), nullable=False),
        sa.Column('source', sa.String(length=10), nullable=False),
        sa.Column('contact_id', sa.Integer(), nullable=True),
        sa.Column('opportunity_id', sa.Integer(), nullable=True),
        sa.Column('task_id', sa.Integer(), nullable=True),
        sa.Column('dispatch_item_id', sa.Integer(), nullable=True),
        sa.Column('filed_as', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_ai_agent_requests_key', 'ai_agent_requests', ['key'], unique=True)
    op.create_index('ix_ai_agent_requests_owen_call_id', 'ai_agent_requests',
                    ['owen_call_id'])


def downgrade() -> None:
    op.drop_index('ix_ai_agent_requests_owen_call_id', table_name='ai_agent_requests')
    op.drop_index('ix_ai_agent_requests_key', table_name='ai_agent_requests')
    op.drop_table('ai_agent_requests')
