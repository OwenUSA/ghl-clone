"""reviews & QC (agent #9): one satisfaction check per finished job

Revision ID: c6f1a8e3d952
Revises: b9e5d27a4c18
Create Date: 2026-10-22

One CREATE TABLE. No DROP, no UPDATE. The agent is off unless REVIEWS_ENABLED.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'c6f1a8e3d952'
down_revision: str | Sequence[str] | None = 'b9e5d27a4c18'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

J = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        'review_requests',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('job_uid', sa.String(length=64), nullable=False),
        sa.Column('job_number', sa.String(length=40), nullable=True),
        sa.Column('board', sa.String(length=120), nullable=True),
        sa.Column('customer_uid', sa.String(length=64), nullable=True),
        sa.Column('customer_name', sa.String(length=200), nullable=True),
        sa.Column('phone', sa.String(length=20), nullable=True),
        sa.Column('technician', sa.String(length=120), nullable=True),
        sa.Column('completed_at', TS, nullable=True),
        sa.Column('state', sa.String(length=20), nullable=False),
        sa.Column('reason', sa.String(length=300), nullable=True),
        sa.Column('asked_at', TS, nullable=True),
        sa.Column('asked_body', sa.Text(), nullable=True),
        sa.Column('asked_state', sa.String(length=20), nullable=True),
        sa.Column('score', sa.Integer(), nullable=True),
        sa.Column('reply', sa.Text(), nullable=True),
        sa.Column('replied_at', TS, nullable=True),
        sa.Column('leak', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('improve', sa.Text(), nullable=True),
        sa.Column('link_sent', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('texts', J, nullable=True),
        sa.Column('seen_uids', J, nullable=True),
        sa.Column('call_state', sa.String(length=20), nullable=True),
        sa.Column('call_at', TS, nullable=True),
        sa.Column('item_id', sa.Integer(), nullable=True),
        sa.Column('error', sa.String(length=500), nullable=True),
        sa.Column('created_at', TS, server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('job_uid'),
    )
    op.create_index('ix_review_requests_phone', 'review_requests', ['phone'])
    op.create_index('ix_review_requests_state', 'review_requests', ['state'])


def downgrade() -> None:
    op.drop_index('ix_review_requests_state', table_name='review_requests')
    op.drop_index('ix_review_requests_phone', table_name='review_requests')
    op.drop_table('review_requests')
