"""ahs_submitted_text — the "inspection report submitted to AHS" text

Revision ID: d3a7b9e2f415
Revises: c8d2f4a6b1e9
Create Date: 2026-10-09

One NEW table and two ADD COLUMNs on reminder_settings (one with a server default). No DROP, no
UPDATE, no backfill. DECISIONS.md, the 2026-10-09 amendment (app/reminders/stage_texts.py).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'd3a7b9e2f415'
down_revision: str | Sequence[str] | None = 'c8d2f4a6b1e9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('reminder_settings', sa.Column('ahs_submitted_mode', sa.String(length=10),
                                                 server_default='off', nullable=False))
    op.add_column('reminder_settings', sa.Column('stage_watch_at', sa.DateTime(timezone=True),
                                                 nullable=True))
    op.create_table(
        'reminder_stage_watch',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('job_uid', sa.String(length=64), nullable=False),
        sa.Column('board', sa.String(length=120), nullable=True),
        sa.Column('status', sa.String(length=120), nullable=True),
        sa.Column('seen_at', sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_reminder_stage_watch_job_uid', 'reminder_stage_watch', ['job_uid'],
                    unique=True)


def downgrade() -> None:
    op.drop_index('ix_reminder_stage_watch_job_uid', table_name='reminder_stage_watch')
    op.drop_table('reminder_stage_watch')
    op.drop_column('reminder_settings', 'stage_watch_at')
    op.drop_column('reminder_settings', 'ahs_submitted_mode')
