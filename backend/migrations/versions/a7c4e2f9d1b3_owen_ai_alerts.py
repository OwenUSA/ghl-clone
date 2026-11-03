"""Owen AI: alerts found by the job audit, and audit messages

Revision ID: a7c4e2f9d1b3
Revises: f3a1c9e7b205
Create Date: 2026-11-03

One new table and two nullable/defaulted columns. No DROP, no UPDATE of existing data.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a7c4e2f9d1b3'
down_revision: str | Sequence[str] | None = 'f3a1c9e7b205'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('owen_ai_messages',
                  sa.Column('kind', sa.String(10), nullable=False, server_default='chat'))
    op.add_column('owen_ai_messages', sa.Column('job_uid', sa.String(64), nullable=True))
    op.create_table(
        'owen_ai_alerts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('job_uid', sa.String(64), nullable=False),
        sa.Column('job_number', sa.String(40), nullable=True),
        sa.Column('rule_id', sa.String(40), nullable=False),
        sa.Column('severity', sa.String(10), nullable=False),
        sa.Column('title', sa.String(300), nullable=False),
        sa.Column('evidence', sa.Text(), nullable=True),
        sa.Column('action', sa.Text(), nullable=True),
        sa.Column('status', sa.String(10), nullable=False, server_default='open'),
        sa.Column('resolution', sa.Text(), nullable=True),
        sa.Column('times_seen', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_owen_ai_alerts_job_uid', 'owen_ai_alerts', ['job_uid'])
    op.create_index('ix_owen_ai_alerts_rule_id', 'owen_ai_alerts', ['rule_id'])
    op.create_index('ix_owen_ai_alerts_status', 'owen_ai_alerts', ['status'])


def downgrade() -> None:
    op.drop_table('owen_ai_alerts')
    op.drop_column('owen_ai_messages', 'job_uid')
    op.drop_column('owen_ai_messages', 'kind')
