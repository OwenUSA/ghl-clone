"""companycam: which project belongs to which card, the hourly check, creation requests

CompanyCam job photos (DECISIONS.md, 2026-09-14). The photos stay in CompanyCam; this CRM
records only:

    companycam_links               card <-> CompanyCam project: how (workiz_job / address /
                                   manual / created), when, by whom, and an admin's unlink
    companycam_review_items        projects that matched a customer by NAME only (never linked)
    companycam_sync_state          one row: the hourly check's heartbeat and cursor
    companycam_project_requests    cards waiting for the worker to find or create a project

**Additive only, by the standing rule.** Four CREATE TABLEs and their indexes, nothing else:
no ALTER of an existing table, no DROP, no UPDATE, no backfill; no existing row is read or
written. Indexes use plain `op.create_index`, never `batch_alter_table`. The two foreign keys
point AT `opportunities` with ON DELETE CASCADE — that changes nothing about `opportunities`
itself, it only means deleting a card also removes its own link and request rows.

Non-nullable columns that can have a sensible default carry a `server_default` (state,
attempts, the empty candidate list, the timestamps); ids, foreign keys and the values a row is
meaningless without (project id, method, origin) do not, as in f3c8e2a61d97. All four tables
are new and empty, so no existing row could ever lack them.

Revision ID: c4e8a2f6b913
Revises: a7d4c2e9f130
Create Date: 2026-09-14
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'c4e8a2f6b913'
# a7d4c2e9f130 (checklist question settings) is the single head on origin/main (e69a9ae)
# after feature/opportunity-checklist merged. `uv run alembic heads` must print exactly one
# line after this lands.
down_revision: str | Sequence[str] | None = 'a7d4c2e9f130'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOW = sa.func.now()
JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    """Four CREATE TABLEs and their indexes, nothing else."""
    op.create_table(
        'companycam_links',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('opportunity_id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.String(length=40), nullable=False),
        sa.Column('method', sa.String(length=20), nullable=False),
        sa.Column('project_name', sa.String(length=255), nullable=True),
        sa.Column('linked_at', sa.DateTime(timezone=True), server_default=NOW,
                  nullable=False),
        sa.Column('linked_by', sa.String(length=200), nullable=True),
        sa.Column('unlinked_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['opportunity_id'], ['opportunities.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('opportunity_id', 'project_id', name='uq_companycam_link'),
    )
    op.create_index('ix_companycam_links_opportunity_id', 'companycam_links',
                    ['opportunity_id'], unique=False)
    op.create_index('ix_companycam_links_project_id', 'companycam_links',
                    ['project_id'], unique=False)

    op.create_table(
        'companycam_review_items',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.String(length=40), nullable=False),
        sa.Column('project_name', sa.String(length=255), nullable=True),
        sa.Column('project_address', sa.String(length=500), nullable=True),
        sa.Column('candidate_opportunity_ids', JSON, server_default='[]', nullable=False),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), server_default=NOW,
                  nullable=False),
        sa.Column('dismissed_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_companycam_review_items_project_id', 'companycam_review_items',
                    ['project_id'], unique=True)

    op.create_table(
        'companycam_sync_state',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('last_started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_success_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_full_sweep_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('modified_cursor', sa.Integer(), nullable=True),
        sa.Column('last_counts', JSON, nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'companycam_project_requests',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('opportunity_id', sa.Integer(), nullable=False),
        sa.Column('origin', sa.String(length=20), nullable=False),
        sa.Column('state', sa.String(length=20), server_default='pending', nullable=False),
        sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
        sa.Column('project_id', sa.String(length=40), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('requested_at', sa.DateTime(timezone=True), server_default=NOW,
                  nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['opportunity_id'], ['opportunities.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_companycam_project_requests_opportunity_id',
                    'companycam_project_requests', ['opportunity_id'], unique=True)
    op.create_index('ix_companycam_project_requests_state', 'companycam_project_requests',
                    ['state'], unique=False)


def downgrade() -> None:
    """The exact inverse. Lossy in use: it forgets every link, the review list, the
    heartbeat and any pending creation request. Nothing in CompanyCam is touched."""
    op.drop_index('ix_companycam_project_requests_state',
                  table_name='companycam_project_requests')
    op.drop_index('ix_companycam_project_requests_opportunity_id',
                  table_name='companycam_project_requests')
    op.drop_table('companycam_project_requests')
    op.drop_table('companycam_sync_state')
    op.drop_index('ix_companycam_review_items_project_id',
                  table_name='companycam_review_items')
    op.drop_table('companycam_review_items')
    op.drop_index('ix_companycam_links_project_id', table_name='companycam_links')
    op.drop_index('ix_companycam_links_opportunity_id', table_name='companycam_links')
    op.drop_table('companycam_links')
