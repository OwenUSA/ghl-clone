"""Follow-ups: sequences, followed-up numbers and their timeline

Revision ID: d4a7c2e91f36
Revises: c3f9a1d6e842
Create Date: 2026-11-11

Three new tables (app/cadences). No change to any existing table or row.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'd4a7c2e91f36'
down_revision: str | Sequence[str] | None = 'c3f9a1d6e842'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

J = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')
TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        'followup_sequences',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('board', sa.String(120), nullable=True),
        sa.Column('is_default', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('active', sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column('steps', J, nullable=False),
        sa.Column('created_at', TZ, server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', TZ, nullable=True),
    )
    op.create_table(
        'followups',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('phone', sa.String(10), nullable=False),
        sa.Column('state', sa.String(12), server_default='open', nullable=False),
        sa.Column('stop_reason', sa.Text(), nullable=True),
        sa.Column('source', sa.String(10), server_default='manual', nullable=False),
        sa.Column('trigger_key', sa.String(160), nullable=True, unique=True),
        sa.Column('customer_uid', sa.String(64), nullable=True),
        sa.Column('customer_name', sa.String(200), nullable=True),
        sa.Column('job_uid', sa.String(64), nullable=True),
        sa.Column('job_number', sa.String(40), nullable=True),
        sa.Column('board', sa.String(120), nullable=True),
        sa.Column('contact_id', sa.Integer(), nullable=True),
        sa.Column('sequence_id', sa.Integer(), nullable=True),
        sa.Column('sequence_name', sa.String(120), nullable=True),
        sa.Column('steps', J, nullable=False),
        sa.Column('step_index', sa.Integer(), server_default='0', nullable=False),
        sa.Column('next_at', TZ, nullable=True),
        sa.Column('due_alerted_step', sa.Integer(), nullable=True),
        sa.Column('assigned_user_id', sa.Integer(), nullable=True),
        sa.Column('assigned_label', sa.String(120), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('started_at', TZ, nullable=False),
        sa.Column('created_by', sa.String(120), nullable=True),
        sa.Column('connect_seen_at', TZ, nullable=True),
        sa.Column('closed_at', TZ, nullable=True),
        sa.Column('created_at', TZ, server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', TZ, nullable=True),
    )
    for col in ('phone', 'state', 'board', 'next_at', 'assigned_user_id', 'created_at'):
        op.create_index('ix_followups_%s' % col, 'followups', [col])
    op.create_table(
        'followup_events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('followup_id', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(200), nullable=True, unique=True),
        sa.Column('at', TZ, nullable=False),
        sa.Column('kind', sa.String(24), nullable=False),
        sa.Column('state', sa.String(12), nullable=True),
        sa.Column('step', sa.Integer(), nullable=True),
        sa.Column('text', sa.Text(), nullable=True),
        sa.Column('who', sa.String(120), nullable=True),
        sa.Column('created_at', TZ, server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_followup_events_followup_id', 'followup_events', ['followup_id'])
    op.create_index('ix_followup_events_at', 'followup_events', ['at'])


def downgrade() -> None:
    op.drop_table('followup_events')
    op.drop_table('followups')
    op.drop_table('followup_sequences')
