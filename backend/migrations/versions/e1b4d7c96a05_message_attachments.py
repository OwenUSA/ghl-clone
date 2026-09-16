"""message attachments — pictures on a text message

Revision ID: e1b4d7c96a05
Revises: d7c3a9e5f214
Create Date: 2026-09-16

ONE new table and nothing else. No ALTER of an existing column, no DROP, no data
backfill — the owner's standing migration rule, and this revision is deliberately the
narrowest thing that can carry the feature: every existing row in every existing table is
byte-for-byte untouched by running it.

The BYTES are not in here. They live on a persistent volume under `MEDIA_ROOT`
(`app/attachments.py`); this table records where, what type, how big, and whether the fetch
worked. See DECISIONS.md, 2026-09-16.

`server_default` is declared on every non-nullable column, as CLAUDE.md requires — though
on a CREATE TABLE there are no existing rows to fail against, the columns are written the
same way as the model so `alembic check` has nothing to report.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'e1b4d7c96a05'
down_revision: str | Sequence[str] | None = 'd7c3a9e5f214'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# `direction` ALREADY EXISTS on PostgreSQL — the baseline created it. A plain `sa.Enum`
# inside a `create_table` emits `CREATE TYPE` and fails on the second use of the name, so
# the column reuses the existing type. On SQLite this renders as the same VARCHAR a plain
# Enum would. Exactly what `c8f2b6d41a93` does, for exactly the same reason.
DIRECTIONS = ('INBOUND', 'OUTBOUND')


def _existing_enum(values, name):
    return postgresql.ENUM(*values, name=name, create_type=False)


def upgrade() -> None:
    op.create_table(
        'message_attachments',
        sa.Column('id', sa.Integer(), nullable=False),
        # Exactly one parent is set — or neither, for a draft the composer is holding.
        sa.Column('conversation_event_id', sa.Integer(), nullable=True),
        sa.Column('number_thread_event_id', sa.Integer(), nullable=True),
        sa.Column('position', sa.Integer(), server_default='0', nullable=False),
        sa.Column('direction', _existing_enum(DIRECTIONS, 'direction'),
                  server_default='INBOUND', nullable=False),
        sa.Column('status', sa.String(length=20), server_default='PENDING', nullable=False),
        sa.Column('detail', sa.Text(), nullable=True),
        sa.Column('source_ref', sa.String(length=200), nullable=True),
        sa.Column('content_type', sa.String(length=80), nullable=True),
        sa.Column('byte_size', sa.Integer(), nullable=True),
        sa.Column('sha256', sa.String(length=64), nullable=True),
        sa.Column('storage_path', sa.String(length=200), nullable=True),
        sa.Column('filename', sa.String(length=200), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['conversation_event_id'], ['conversation_events.id'], ),
        sa.ForeignKeyConstraint(['number_thread_event_id'], ['number_thread_events.id'], ),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_message_attachments_conversation_event_id'),
                    'message_attachments', ['conversation_event_id'], unique=False)
    op.create_index(op.f('ix_message_attachments_number_thread_event_id'),
                    'message_attachments', ['number_thread_event_id'], unique=False)
    op.create_index(op.f('ix_message_attachments_created_at'),
                    'message_attachments', ['created_at'], unique=False)
    op.create_index(op.f('ix_message_attachments_sha256'),
                    'message_attachments', ['sha256'], unique=False)
    op.create_index(op.f('ix_message_attachments_source_ref'),
                    'message_attachments', ['source_ref'], unique=False)
    # The idempotency guarantee for a re-delivered inbound MMS, declared as a unique INDEX
    # and not as a UniqueConstraint — the same choice, for the same reason, as
    # `uq_conversation_events_dedupe_key`: the model declares an Index, so `alembic check`
    # has to see an Index here or it reports a phantom drop-and-recreate for ever.
    op.create_index('uq_message_attachments_conv_event_position', 'message_attachments',
                    ['conversation_event_id', 'position'], unique=True)
    op.create_index('uq_message_attachments_number_event_position', 'message_attachments',
                    ['number_thread_event_id', 'position'], unique=True)


def downgrade() -> None:
    op.drop_index('uq_message_attachments_number_event_position',
                  table_name='message_attachments')
    op.drop_index('uq_message_attachments_conv_event_position',
                  table_name='message_attachments')
    op.drop_index(op.f('ix_message_attachments_source_ref'), table_name='message_attachments')
    op.drop_index(op.f('ix_message_attachments_sha256'), table_name='message_attachments')
    op.drop_index(op.f('ix_message_attachments_created_at'), table_name='message_attachments')
    op.drop_index(op.f('ix_message_attachments_number_thread_event_id'),
                  table_name='message_attachments')
    op.drop_index(op.f('ix_message_attachments_conversation_event_id'),
                  table_name='message_attachments')
    op.drop_table('message_attachments')
