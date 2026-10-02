"""zuper_proposal_lines — a signed proposal's option copied onto its job's line items

Revision ID: e3a9c5d1f207
Revises: 0dd68d48a507
Create Date: 2026-10-02

One NEW table. No DROP, no UPDATE, no backfill. DECISIONS.md, the 2026-10-02 amendment
"A signed proposal's option goes onto its job".
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'e3a9c5d1f207'
down_revision: str | Sequence[str] | None = '0dd68d48a507'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NULLABLE_JSON_TYPE = sa.JSON(none_as_null=True).with_variant(
    postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    op.create_table(
        'zuper_proposal_lines',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('estimate_uid', sa.String(length=64), nullable=False),
        sa.Column('estimate_no', sa.String(length=40), nullable=True),
        sa.Column('option_name', sa.String(length=200), nullable=True),
        sa.Column('job_uid', sa.String(length=64), nullable=True),
        sa.Column('job_number', sa.String(length=40), nullable=True),
        sa.Column('outcome', sa.String(length=40), nullable=False),
        sa.Column('amount', sa.Float(), nullable=True),
        sa.Column('detail', NULLABLE_JSON_TYPE, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_zuper_proposal_lines_estimate_uid', 'zuper_proposal_lines',
                    ['estimate_uid'], unique=True)
    op.create_index('ix_zuper_proposal_lines_job_uid', 'zuper_proposal_lines', ['job_uid'])


def downgrade() -> None:
    op.drop_index('ix_zuper_proposal_lines_job_uid', table_name='zuper_proposal_lines')
    op.drop_index('ix_zuper_proposal_lines_estimate_uid', table_name='zuper_proposal_lines')
    op.drop_table('zuper_proposal_lines')
