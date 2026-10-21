"""inspection reports: the post-inspection report agent's drafts

Revision ID: e8b3c62f9a10
Revises: d5a2f8c41e73
Create Date: 2026-10-21

One CREATE TABLE. No DROP, no UPDATE. Off unless INSPECTION_REPORT_ENABLED.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'e8b3c62f9a10'
down_revision: str | Sequence[str] | None = 'd5a2f8c41e73'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

J = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')


def upgrade() -> None:
    op.create_table(
        'inspection_reports',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(length=160), nullable=False),
        sa.Column('job_uid', sa.String(length=64), nullable=False),
        sa.Column('job_number', sa.String(length=40), nullable=True),
        sa.Column('customer_name', sa.String(length=200), nullable=True),
        sa.Column('address', sa.String(length=300), nullable=True),
        sa.Column('board', sa.String(length=120), nullable=True),
        sa.Column('visit_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('technician', sa.String(length=120), nullable=True),
        sa.Column('language', sa.String(length=2), server_default='en', nullable=False),
        sa.Column('title', sa.String(length=200), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('findings', J, nullable=True),
        sa.Column('photos', J, nullable=True),
        sa.Column('candidates', J, nullable=True),
        sa.Column('state', sa.String(length=12), server_default='building', nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('model', sa.String(length=120), nullable=True),
        sa.Column('input_tokens', sa.Integer(), server_default='0', nullable=False),
        sa.Column('output_tokens', sa.Integer(), server_default='0', nullable=False),
        sa.Column('cost_micros', sa.Integer(), nullable=True),
        sa.Column('item_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('approved_by_id', sa.Integer(), nullable=True),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('key'),
    )
    op.create_index(op.f('ix_inspection_reports_job_uid'), 'inspection_reports', ['job_uid'])
    op.create_index(op.f('ix_inspection_reports_state'), 'inspection_reports', ['state'])
    op.create_index(op.f('ix_inspection_reports_created_at'), 'inspection_reports',
                    ['created_at'])


def downgrade() -> None:
    op.drop_index(op.f('ix_inspection_reports_created_at'), table_name='inspection_reports')
    op.drop_index(op.f('ix_inspection_reports_state'), table_name='inspection_reports')
    op.drop_index(op.f('ix_inspection_reports_job_uid'), table_name='inspection_reports')
    op.drop_table('inspection_reports')
