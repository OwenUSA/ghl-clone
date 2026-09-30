"""zuper_status_history + zuper_record_versions — Zuper's history kept for the KPI reports

Revision ID: d7f2b9e4a615
Revises: c5e8a1b3d702
Create Date: 2026-09-30

Two NEW tables and one nullable column. No DROP, no UPDATE, no backfill: nothing that exists
changes. Both tables are append-only by design (app/zuper/history.py). DECISIONS.md, the
2026-09-30 KPI history amendment.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'd7f2b9e4a615'
down_revision: str | Sequence[str] | None = 'c5e8a1b3d702'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Mirrors models.JSONType: JSONB on Postgres, plain JSON on SQLite.
JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')
NULLABLE_JSON_TYPE = sa.JSON(none_as_null=True).with_variant(
    postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    op.create_table(
        'zuper_status_history',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('history_uid', sa.String(length=64), nullable=False),
        sa.Column('job_uid', sa.String(length=64), nullable=False),
        sa.Column('job_number', sa.String(length=40), nullable=True),
        sa.Column('category_uid', sa.String(length=64), nullable=True),
        sa.Column('category_name', sa.String(length=120), nullable=True),
        sa.Column('status_uid', sa.String(length=64), nullable=True),
        sa.Column('status_name', sa.String(length=120), nullable=True),
        sa.Column('status_type', sa.String(length=40), nullable=True),
        sa.Column('changed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('done_by_uid', sa.String(length=64), nullable=True),
        sa.Column('done_by_name', sa.String(length=120), nullable=True),
        sa.Column('checklist', NULLABLE_JSON_TYPE, nullable=True),
        sa.Column('first_seen_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_zuper_status_history_history_uid', 'zuper_status_history',
                    ['history_uid'], unique=True)
    op.create_index('ix_zuper_status_history_job_uid', 'zuper_status_history', ['job_uid'])
    op.create_index('ix_zuper_status_history_changed_at', 'zuper_status_history',
                    ['changed_at'])
    op.create_table(
        'zuper_record_versions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('module', sa.String(length=20), nullable=False),
        sa.Column('zuper_uid', sa.String(length=64), nullable=False),
        sa.Column('zuper_updated_at', sa.String(length=40), nullable=True),
        sa.Column('content_hash', sa.String(length=64), nullable=False),
        sa.Column('record', JSON_TYPE, nullable=False),
        sa.Column('captured_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_zuper_record_versions_module_uid', 'zuper_record_versions',
                    ['module', 'zuper_uid'])
    op.create_index('ix_zuper_record_versions_captured_at', 'zuper_record_versions',
                    ['captured_at'])
    op.add_column('zuper_sync_state', sa.Column('last_history_day', sa.String(length=10),
                                                nullable=True))


def downgrade() -> None:
    op.drop_column('zuper_sync_state', 'last_history_day')
    op.drop_index('ix_zuper_record_versions_captured_at', table_name='zuper_record_versions')
    op.drop_index('ix_zuper_record_versions_module_uid', table_name='zuper_record_versions')
    op.drop_table('zuper_record_versions')
    op.drop_index('ix_zuper_status_history_changed_at', table_name='zuper_status_history')
    op.drop_index('ix_zuper_status_history_job_uid', table_name='zuper_status_history')
    op.drop_index('ix_zuper_status_history_history_uid', table_name='zuper_status_history')
    op.drop_table('zuper_status_history')
