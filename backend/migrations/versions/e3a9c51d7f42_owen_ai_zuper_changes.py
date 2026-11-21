"""Owen AI: changes it makes in Zuper on Owen's word in the chat, and pictures dropped in a chat

Revision ID: e3a9c51d7f42
Revises: d6b2f8a4c913
Create Date: 2026-11-21

Two new tables. No DROP, no UPDATE of existing data.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'e3a9c51d7f42'
down_revision: str | Sequence[str] | None = 'd6b2f8a4c913'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

J = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')


def upgrade() -> None:
    op.create_table(
        'owen_ai_change_sets',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('thread_id', sa.Integer(), nullable=False),
        sa.Column('proposed_message_id', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(12), nullable=False, server_default='pending'),
        sa.Column('why', sa.Text(), nullable=True),
        sa.Column('changes', J, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column('applied_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('applied_message_id', sa.Integer(), nullable=True),
        sa.Column('owen_said', sa.Text(), nullable=True),
    )
    op.create_index('ix_owen_ai_change_sets_thread_id', 'owen_ai_change_sets', ['thread_id'])
    op.create_index('ix_owen_ai_change_sets_status', 'owen_ai_change_sets', ['status'])
    op.create_table(
        'owen_ai_uploads',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('thread_id', sa.Integer(), nullable=False),
        sa.Column('message_id', sa.Integer(), nullable=True),
        sa.Column('file_name', sa.String(200), nullable=False),
        sa.Column('content_type', sa.String(40), nullable=False),
        sa.Column('size', sa.Integer(), nullable=False),
        sa.Column('sha256', sa.String(64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index('ix_owen_ai_uploads_thread_id', 'owen_ai_uploads', ['thread_id'])
    op.create_index('ix_owen_ai_uploads_message_id', 'owen_ai_uploads', ['message_id'])


def downgrade() -> None:
    op.drop_table('owen_ai_uploads')
    op.drop_table('owen_ai_change_sets')
