"""inspection checks: the inspection-documentation agent's record of each finished visit

Revision ID: d5a2f8c41e73
Revises: c3e9a7d15b42
Create Date: 2026-10-21

One CREATE TABLE. No DROP, no UPDATE. Off unless INSPECTION_DOCS_ENABLED.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'd5a2f8c41e73'
down_revision: str | Sequence[str] | None = 'c3e9a7d15b42'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

J = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')


def upgrade() -> None:
    op.create_table(
        'inspection_checks',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(length=160), nullable=False),
        sa.Column('job_uid', sa.String(length=64), nullable=False),
        sa.Column('job_number', sa.String(length=40), nullable=True),
        sa.Column('board', sa.String(length=120), nullable=True),
        sa.Column('customer_name', sa.String(length=200), nullable=True),
        sa.Column('kind', sa.String(length=12), nullable=False),
        sa.Column('visit_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('technician', sa.String(length=120), nullable=True),
        sa.Column('tech_phone', sa.String(length=10), nullable=True),
        sa.Column('state', sa.String(length=12), server_default='waiting', nullable=False),
        sa.Column('missing', J, nullable=True),
        sa.Column('found', J, nullable=True),
        sa.Column('photos', sa.Integer(), server_default='0', nullable=False),
        sa.Column('videos', sa.Integer(), server_default='0', nullable=False),
        sa.Column('item_id', sa.Integer(), nullable=True),
        sa.Column('text_state', sa.String(length=12), nullable=True),
        sa.Column('text_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('text_body', sa.Text(), nullable=True),
        sa.Column('call_state', sa.String(length=12), nullable=True),
        sa.Column('call_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('call_ref', sa.String(length=64), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('checked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('key'),
    )
    op.create_index(op.f('ix_inspection_checks_job_uid'), 'inspection_checks', ['job_uid'])
    op.create_index(op.f('ix_inspection_checks_state'), 'inspection_checks', ['state'])
    op.create_index(op.f('ix_inspection_checks_created_at'), 'inspection_checks',
                    ['created_at'])


def downgrade() -> None:
    op.drop_index(op.f('ix_inspection_checks_created_at'), table_name='inspection_checks')
    op.drop_index(op.f('ix_inspection_checks_state'), table_name='inspection_checks')
    op.drop_index(op.f('ix_inspection_checks_job_uid'), table_name='inspection_checks')
    op.drop_table('inspection_checks')
