"""ai_voice_pushes — where a published voice version stands on the phone system

Revision ID: a9d2f4c6e813
Revises: 7b4e2a91c063
Create Date: 2026-09-25

ONE new table and nothing else (voice agents phase 2b, DECISIONS.md 2026-09-25). No ALTER of
an existing column, no DROP, no UPDATE, no backfill: every existing row in every existing
table is untouched by running it. `ai_agent_versions` stays immutable — a push is retried,
refused and superseded, so its state is a row of its own that refers to the version.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a9d2f4c6e813'
down_revision: str | Sequence[str] | None = '7b4e2a91c063'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'ai_voice_pushes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('version_id', sa.Integer(), nullable=False),
        sa.Column('agent_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), server_default='pending', nullable=False),
        sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
        sa.Column('last_attempt_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('pushed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('owen_agent_id', sa.String(length=64), nullable=True),
        sa.Column('owen_version_id', sa.String(length=64), nullable=True),
        sa.Column('owen_version', sa.Integer(), nullable=True),
        sa.Column('imported', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['version_id'], ['ai_agent_versions.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('version_id'),
    )
    op.create_index(op.f('ix_ai_voice_pushes_agent_id'), 'ai_voice_pushes', ['agent_id'],
                    unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_ai_voice_pushes_agent_id'), table_name='ai_voice_pushes')
    op.drop_table('ai_voice_pushes')
