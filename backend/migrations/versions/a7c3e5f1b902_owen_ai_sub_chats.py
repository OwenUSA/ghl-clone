"""Owen AI: sub-chats a main chat delegates to (parent_id on owen_ai_threads)

Revision ID: a7c3e5f1b902
Revises: e5b9d3a1c7f4
Create Date: 2026-11-19

One nullable column and its index. No DROP, no change to existing data.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a7c3e5f1b902'
down_revision: str | Sequence[str] | None = 'e5b9d3a1c7f4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('owen_ai_threads', sa.Column('parent_id', sa.Integer(), nullable=True))
    op.create_index('ix_owen_ai_threads_parent_id', 'owen_ai_threads', ['parent_id'])


def downgrade() -> None:
    op.drop_index('ix_owen_ai_threads_parent_id', table_name='owen_ai_threads')
    op.drop_column('owen_ai_threads', 'parent_id')
