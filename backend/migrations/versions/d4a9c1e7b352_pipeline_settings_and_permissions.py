"""pipeline settings, stage settings, deal probability, pipeline permissions

The GoHighLevel pipeline modal and row menu (2026-09-13). ADDITIVE ONLY, by the
owner's standing rule: seven ADD COLUMNs and one CREATE TABLE. No existing column
is altered, nothing is dropped, and no row is backfilled.

Every existing pipeline and stage comes out of this behaving exactly as it did:

  pipelines.color_mode                   'none'   (server default) — no colour
  pipelines.use_opportunity_probability  false    (server default)
  pipelines.updated_at                   NULL     — never recorded; shown as "—"
  stages.color                           NULL     — no colour
  stages.probability                     NULL     — the Forecast weights it at the
                                                    conversion rate, as before
  stages.show_in_funnel                  true     (server default) — shown
  stages.show_in_pie                     true     (server default) — shown
  opportunities.probability              NULL
  pipeline_permissions                   empty    — a pipeline with no rows is open
                                                    to everyone

Plain `op.add_column`, never `batch_alter_table`: batch mode RECREATES the table on
SQLite, which is exactly the kind of override the rule forbids.

Revision ID: d4a9c1e7b352
Revises: b7e3f1a8c204
Create Date: 2026-09-13
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'd4a9c1e7b352'
# b7e3f1a8c204 is the single head on origin/main and the revision production is at.
# `uv run alembic heads` must print exactly one line after this lands.
down_revision: str | Sequence[str] | None = 'b7e3f1a8c204'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('pipelines', sa.Column(
        'color_mode', sa.String(length=20), nullable=False, server_default='none'))
    op.add_column('pipelines', sa.Column(
        'use_opportunity_probability', sa.Boolean(), nullable=False,
        server_default=sa.false()))
    op.add_column('pipelines', sa.Column(
        'updated_at', sa.DateTime(timezone=True), nullable=True))

    op.add_column('stages', sa.Column('color', sa.String(length=20), nullable=True))
    op.add_column('stages', sa.Column('probability', sa.Integer(), nullable=True))
    op.add_column('stages', sa.Column(
        'show_in_funnel', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column('stages', sa.Column(
        'show_in_pie', sa.Boolean(), nullable=False, server_default=sa.true()))

    op.add_column('opportunities', sa.Column('probability', sa.Integer(), nullable=True))

    op.create_table(
        'pipeline_permissions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('pipeline_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['pipeline_id'], ['pipelines.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('pipeline_id', 'user_id', name='uq_pipeline_permission'),
    )
    op.create_index(op.f('ix_pipeline_permissions_pipeline_id'),
                    'pipeline_permissions', ['pipeline_id'], unique=False)
    op.create_index(op.f('ix_pipeline_permissions_user_id'),
                    'pipeline_permissions', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_pipeline_permissions_user_id'),
                  table_name='pipeline_permissions')
    op.drop_index(op.f('ix_pipeline_permissions_pipeline_id'),
                  table_name='pipeline_permissions')
    op.drop_table('pipeline_permissions')
    op.drop_column('opportunities', 'probability')
    op.drop_column('stages', 'show_in_pie')
    op.drop_column('stages', 'show_in_funnel')
    op.drop_column('stages', 'probability')
    op.drop_column('stages', 'color')
    op.drop_column('pipelines', 'updated_at')
    op.drop_column('pipelines', 'use_opportunity_probability')
    op.drop_column('pipelines', 'color_mode')
