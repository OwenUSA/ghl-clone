"""agent registry — every bot and automation, a scope each, the level of each action

Revision ID: b8d4f1c27e60
Revises: a7c2e5d91b30
Create Date: 2026-10-21

Three CREATE TABLEs (agent_registry, agent_permissions, agent_registry_log) and one nullable ADD
COLUMN on ai_settings. No DROP, no UPDATE, no backfill: no row = the catalog's defaults, which
are exactly how everything runs today. DECISIONS.md, the 2026-10-21 agent registry amendment.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'b8d4f1c27e60'
down_revision: str | Sequence[str] | None = 'a7c2e5d91b30'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'agent_registry',
        sa.Column('key', sa.String(length=80), nullable=False),
        sa.Column('role', sa.Integer(), nullable=True),
        sa.Column('owner_user_id', sa.Integer(), nullable=True),
        sa.Column('is_test', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('daily_cap_micros', sa.Integer(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_by_id', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('key'),
    )
    op.create_table(
        'agent_permissions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('entry_key', sa.String(length=80), nullable=False),
        sa.Column('action', sa.String(length=40), nullable=False),
        sa.Column('level', sa.String(length=12), nullable=False),
        sa.Column('changed_at', sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column('changed_by_id', sa.Integer(), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('entry_key', 'action', name='uq_agent_permission'),
    )
    op.create_index(op.f('ix_agent_permissions_entry_key'), 'agent_permissions', ['entry_key'],
                    unique=False)
    op.create_table(
        'agent_registry_log',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('at', sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column('entry_key', sa.String(length=80), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('what', sa.String(length=20), nullable=False),
        sa.Column('target', sa.String(length=80), nullable=True),
        sa.Column('old_value', sa.Text(), nullable=True),
        sa.Column('new_value', sa.Text(), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_agent_registry_log_at'), 'agent_registry_log', ['at'],
                    unique=False)
    op.create_index(op.f('ix_agent_registry_log_entry_key'), 'agent_registry_log',
                    ['entry_key'], unique=False)
    op.add_column('ai_settings', sa.Column('overall_daily_cap_micros', sa.Integer(),
                                           nullable=True))


def downgrade() -> None:
    op.drop_column('ai_settings', 'overall_daily_cap_micros')
    op.drop_index(op.f('ix_agent_registry_log_entry_key'), table_name='agent_registry_log')
    op.drop_index(op.f('ix_agent_registry_log_at'), table_name='agent_registry_log')
    op.drop_table('agent_registry_log')
    op.drop_index(op.f('ix_agent_permissions_entry_key'), table_name='agent_permissions')
    op.drop_table('agent_permissions')
    op.drop_table('agent_registry')
