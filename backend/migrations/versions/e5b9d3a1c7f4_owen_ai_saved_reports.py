"""Owen AI: saved after-inspection reports and their versions

Revision ID: e5b9d3a1c7f4
Revises: d4a7c2e91f36
Create Date: 2026-11-12

Two new tables. No DROP, no change to existing data.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'e5b9d3a1c7f4'
down_revision: str | Sequence[str] | None = 'd4a7c2e91f36'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

J = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')
JN = sa.JSON(none_as_null=True).with_variant(postgresql.JSONB(none_as_null=True), 'postgresql')


def upgrade() -> None:
    op.create_table(
        'owen_ai_reports',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('job_uid', sa.String(64), nullable=False),
        sa.Column('job_number', sa.String(40), nullable=True),
        sa.Column('customer', sa.String(200), nullable=True),
        sa.Column('current_version', sa.Integer(), server_default='1', nullable=False),
        sa.Column('facts', J, nullable=False),
        sa.Column('created_by', sa.String(200), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
    )
    op.create_index('ix_owen_ai_reports_job_uid', 'owen_ai_reports', ['job_uid'])
    op.create_table(
        'owen_ai_report_versions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('report_id', sa.Integer(),
                  sa.ForeignKey('owen_ai_reports.id', ondelete='CASCADE'), nullable=False),
        sa.Column('number', sa.Integer(), nullable=False),
        sa.Column('spec', J, nullable=False),
        sa.Column('source', sa.String(10), nullable=False),
        sa.Column('author', sa.String(200), nullable=True),
        sa.Column('note', sa.String(300), nullable=True),
        sa.Column('pictures', JN, nullable=True),
        sa.Column('warnings', JN, nullable=True),
        sa.Column('has_pdf', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('cost_usd', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.UniqueConstraint('report_id', 'number', name='uq_owen_ai_report_version'),
    )
    op.create_index('ix_owen_ai_report_versions_report_id', 'owen_ai_report_versions',
                    ['report_id'])


def downgrade() -> None:
    op.drop_index('ix_owen_ai_report_versions_report_id', 'owen_ai_report_versions')
    op.drop_table('owen_ai_report_versions')
    op.drop_index('ix_owen_ai_reports_job_uid', 'owen_ai_reports')
    op.drop_table('owen_ai_reports')
