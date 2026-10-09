"""dispatch_notes, dispatch_activity, dispatch_plans, dispatch_availability

Revision ID: b4e1d7a2c9f6
Revises: a7c2e5f91b30
Create Date: 2026-10-08

Four NEW tables. No DROP, no UPDATE, no backfill. DECISIONS.md, the 2026-10-08 amendment
"The Dispatch assistant reads like the office": the text of every Zuper job note (a
technician's on-site dictation) and Zuper's account activity log (who moved, rescheduled or
deleted what), both filled by the Dispatch reader; and each week plan as it was made
(`dispatch_plans`), so the chat, the calendar and the Excel show the same plan; and what each
customer said about when they can have the visit (`dispatch_availability`), read from their
calls and texts. Nothing here writes to Zuper.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import Text
from sqlalchemy.dialects import postgresql

revision: str = 'b4e1d7a2c9f6'
down_revision: str | Sequence[str] | None = 'a7c2e5f91b30'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON(none_as_null=True).with_variant(
    postgresql.JSONB(none_as_null=True, astext_type=Text()), 'postgresql')


def upgrade() -> None:
    op.create_table(
        'dispatch_notes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('note_uid', sa.String(length=64), nullable=False),
        sa.Column('job_uid', sa.String(length=64), nullable=False),
        sa.Column('job_number', sa.String(length=40), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('by_name', sa.String(length=120), nullable=True),
        sa.Column('note_type', sa.String(length=30), nullable=True),
        sa.Column('text', sa.Text(), nullable=True),
        sa.Column('attachments', sa.Integer(), server_default='0', nullable=False),
        sa.Column('removed', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_dispatch_notes_note_uid', 'dispatch_notes', ['note_uid'], unique=True)
    op.create_index('ix_dispatch_notes_job_uid', 'dispatch_notes', ['job_uid'])
    op.create_index('ix_dispatch_notes_created_at', 'dispatch_notes', ['created_at'])
    op.create_table(
        'dispatch_availability',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('job_uid', sa.String(length=64), nullable=False),
        sa.Column('job_number', sa.String(length=40), nullable=True),
        sa.Column('limits', _JSON, nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('evidence', _JSON, nullable=True),
        sa.Column('confidence', sa.String(length=10), nullable=True),
        sa.Column('source', sa.String(length=10), server_default='ai', nullable=False),
        sa.Column('fingerprint', sa.String(length=80), nullable=True),
        sa.Column('newer_messages', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('model', sa.String(length=120), nullable=True),
        sa.Column('error', sa.String(length=500), nullable=True),
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('edited_by_id', sa.Integer(), nullable=True),
        sa.Column('edited_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_dispatch_availability_job_uid', 'dispatch_availability', ['job_uid'],
                    unique=True)
    op.create_table(
        'dispatch_plans',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('source', sa.String(length=20), server_default='page', nullable=False),
        sa.Column('params', _JSON, nullable=True),
        sa.Column('result', _JSON, nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_dispatch_plans_created_at', 'dispatch_plans', ['created_at'])
    op.create_table(
        'dispatch_activity',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('activity_uid', sa.String(length=64), nullable=False),
        sa.Column('at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('user_name', sa.String(length=120), nullable=True),
        sa.Column('user_uid', sa.String(length=64), nullable=True),
        sa.Column('activity_type', sa.String(length=30), nullable=True),
        sa.Column('module', sa.String(length=40), nullable=True),
        sa.Column('message', sa.Text(), nullable=True),
        sa.Column('job_uid', sa.String(length=64), nullable=True),
        sa.Column('job_number', sa.String(length=40), nullable=True),
        sa.Column('via', sa.String(length=40), nullable=True),
        sa.Column('automatic', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('meta', _JSON, nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_dispatch_activity_activity_uid', 'dispatch_activity', ['activity_uid'],
                    unique=True)
    op.create_index('ix_dispatch_activity_at', 'dispatch_activity', ['at'])
    op.create_index('ix_dispatch_activity_job_uid', 'dispatch_activity', ['job_uid'])


def downgrade() -> None:
    op.drop_index('ix_dispatch_availability_job_uid', table_name='dispatch_availability')
    op.drop_table('dispatch_availability')
    op.drop_index('ix_dispatch_plans_created_at', table_name='dispatch_plans')
    op.drop_table('dispatch_plans')
    op.drop_index('ix_dispatch_activity_job_uid', table_name='dispatch_activity')
    op.drop_index('ix_dispatch_activity_at', table_name='dispatch_activity')
    op.drop_index('ix_dispatch_activity_activity_uid', table_name='dispatch_activity')
    op.drop_table('dispatch_activity')
    op.drop_index('ix_dispatch_notes_created_at', table_name='dispatch_notes')
    op.drop_index('ix_dispatch_notes_job_uid', table_name='dispatch_notes')
    op.drop_index('ix_dispatch_notes_note_uid', table_name='dispatch_notes')
    op.drop_table('dispatch_notes')
