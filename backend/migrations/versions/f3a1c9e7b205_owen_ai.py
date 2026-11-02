"""Owen AI: the owner's own assistant's threads and messages

Revision ID: f3a1c9e7b205
Revises: e5b2d8f17a39
Create Date: 2026-11-02

Two new tables. No DROP, no UPDATE of existing data.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'f3a1c9e7b205'
down_revision: str | Sequence[str] | None = 'e5b2d8f17a39'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

J = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')


def upgrade() -> None:
    op.create_table(
        'owen_ai_threads',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(10), nullable=False, server_default='task'),
        sa.Column('title', sa.String(200), nullable=False, server_default='New chat'),
        sa.Column('job_uid', sa.String(64), nullable=True),
        sa.Column('job_number', sa.String(40), nullable=True),
        sa.Column('day', sa.Date(), nullable=True),
        sa.Column('session_id', sa.String(80), nullable=True),
        sa.Column('archived', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index('ix_owen_ai_threads_user_id', 'owen_ai_threads', ['user_id'])
    op.create_index('ix_owen_ai_threads_job_uid', 'owen_ai_threads', ['job_uid'])
    op.create_index('ix_owen_ai_threads_day', 'owen_ai_threads', ['day'])
    op.create_index('ix_owen_ai_threads_updated_at', 'owen_ai_threads', ['updated_at'])
    op.create_table(
        'owen_ai_messages',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('thread_id', sa.Integer(), nullable=False),
        sa.Column('role', sa.String(20), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('status', sa.String(10), nullable=False, server_default='done'),
        sa.Column('steps', J, nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('model', sa.String(120), nullable=True),
        sa.Column('cost_usd', sa.Float(), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('num_turns', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_owen_ai_messages_thread_id', 'owen_ai_messages', ['thread_id'])
    op.create_index('ix_owen_ai_messages_status', 'owen_ai_messages', ['status'])


def downgrade() -> None:
    op.drop_table('owen_ai_messages')
    op.drop_table('owen_ai_threads')
