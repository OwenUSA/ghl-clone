"""number-only threads: a thread for a phone number that no contact holds

The owner (2026-09-13): "if its a number that is not registered as a contact, it
should not be saved as contact but the numbers with the conversation must display
anyways". ADDITIVE ONLY, by the standing rule — two CREATE TABLEs and their indexes.
No existing column is altered, nothing is dropped, and no row is written or
backfilled.

  number_threads         one row per number with activity and no contact: the
                         number, its last-ten-digits key (UNIQUE), Quo's name for
                         it, unread_count, starred, last_event_at, created_at
  number_thread_events   `conversation_events` column for column, with the parent
                         swapped to number_threads; unique index on dedupe_key

Why new tables and not columns: `conversations.contact_id` and
`conversation_events.conversation_id` are both NOT NULL, so a thread with no contact
cannot live in either without an ALTER, which the rule forbids. See DECISIONS.md,
2026-09-13.

Every existing table comes out of this byte-identical: nothing here mentions
contacts, conversations, conversation_events or jobs.

Every non-nullable column that is not an id or a foreign key carries a server
default (`sa.func.now()`, `'0'`, `sa.false()`, all of which render on SQLite and
PostgreSQL), except the columns every INSERT supplies (`phone`, `phone_key`, `type`,
`direction`, `occurred_at`) — the tables are new and empty, so there is no existing
row for an ALTER to fail on.

THE ENUM TYPES ALREADY EXIST ON POSTGRESQL. `eventtype`, `direction` and
`deliverystatus` were created by the baseline (and `deliverystatus` widened by
a1f4c7d92b30). A plain `sa.Enum` inside `create_table` emits `CREATE TYPE` and fails
on the second use of the name, so these columns use `postgresql.ENUM(...,
create_type=False)`, which reuses the type; on SQLite it renders as the same VARCHAR
a plain Enum would. Member lists are the models' current ones.

Indexes use plain `op.create_index`, never `batch_alter_table`.

Revision ID: c8f2b6d41a93
Revises: a5d2c8e4f917
Create Date: 2026-09-13
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'c8f2b6d41a93'
# a5d2c8e4f917 is the single head on origin/main (04cca1f), after
# feature/ghl-book-appointment merged. `uv run alembic heads` must print exactly one
# line after this lands.
down_revision: str | Sequence[str] | None = 'a5d2c8e4f917'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EVENT_TYPES = ('SMS', 'EMAIL', 'CALL', 'NOTE', 'INTERNAL_COMMENT', 'WHATSAPP', 'CONTACT',
               'APPOINTMENT', 'OPPORTUNITY', 'PAYMENT', 'INVOICE')
DIRECTIONS = ('INBOUND', 'OUTBOUND')
DELIVERY_STATUSES = ('PENDING', 'QUEUED', 'SENT', 'DELIVERED', 'FAILED', 'REFUSED',
                     'LOGGED_ONLY')


def _existing_enum(values, name):
    return postgresql.ENUM(*values, name=name, create_type=False)


def upgrade() -> None:
    op.create_table(
        'number_threads',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('phone', sa.String(length=40), nullable=False),
        sa.Column('phone_key', sa.String(length=20), nullable=False),
        sa.Column('quo_name', sa.String(length=200), nullable=True),
        sa.Column('last_event_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column('unread_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('starred', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_number_threads_last_event_at', 'number_threads',
                    ['last_event_at'], unique=False)
    op.create_index('ix_number_threads_phone_key', 'number_threads',
                    ['phone_key'], unique=True)

    op.create_table(
        'number_thread_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('number_thread_id', sa.Integer(), nullable=False),
        sa.Column('type', _existing_enum(EVENT_TYPES, 'eventtype'), nullable=False),
        sa.Column('direction', _existing_enum(DIRECTIONS, 'direction'), nullable=False),
        sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('body', sa.Text(), nullable=True),
        sa.Column('subject', sa.String(length=255), nullable=True),
        sa.Column('duration_seconds', sa.Integer(), nullable=True),
        sa.Column('call_status', sa.String(length=30), nullable=True),
        sa.Column('recording_url', sa.String(length=500), nullable=True),
        sa.Column('transcript', sa.Text(), nullable=True),
        sa.Column('delivery_status', _existing_enum(DELIVERY_STATUSES, 'deliverystatus'),
                  nullable=True),
        sa.Column('delivery_detail', sa.Text(), nullable=True),
        sa.Column('provider_ref', sa.String(length=120), nullable=True),
        sa.Column('dedupe_key', sa.String(length=200), nullable=True),
        sa.Column('source_system', sa.String(length=40), nullable=True),
        sa.Column('source_number', sa.String(length=40), nullable=True),
        sa.ForeignKeyConstraint(['number_thread_id'], ['number_threads.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_number_thread_events_number_thread_id', 'number_thread_events',
                    ['number_thread_id'], unique=False)
    op.create_index('ix_number_thread_events_occurred_at', 'number_thread_events',
                    ['occurred_at'], unique=False)
    op.create_index('ix_number_thread_events_provider_ref', 'number_thread_events',
                    ['provider_ref'], unique=False)
    op.create_index('ix_number_thread_events_type_occurred', 'number_thread_events',
                    ['type', 'occurred_at'], unique=False)
    op.create_index('uq_number_thread_events_dedupe_key', 'number_thread_events',
                    ['dedupe_key'], unique=True)


def downgrade() -> None:
    """A true inverse. It discards every number-only thread and its events — which
    on the way DOWN is exactly the history this revision exists to keep, so a
    downgrade on a database that holds any should be preceded by adding those numbers
    as contacts (adoption moves their events onto contact threads). The enum types
    belong to the baseline and are left alone."""
    for name in ('uq_number_thread_events_dedupe_key',
                 'ix_number_thread_events_type_occurred',
                 'ix_number_thread_events_provider_ref',
                 'ix_number_thread_events_occurred_at',
                 'ix_number_thread_events_number_thread_id'):
        op.drop_index(name, table_name='number_thread_events')
    op.drop_table('number_thread_events')
    op.drop_index('ix_number_threads_phone_key', table_name='number_threads')
    op.drop_index('ix_number_threads_last_event_at', table_name='number_threads')
    op.drop_table('number_threads')
