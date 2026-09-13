"""appointment description and location, and blocked off time

GoHighLevel's Book appointment modal (2026-09-13). ADDITIVE ONLY, by the owner's
standing rule — two ADD COLUMNs and one CREATE TABLE (with its two indexes). No
existing column is altered, nothing is dropped, and no row is written or backfilled.

  appointments.description   NULLABLE — "Add description"; every existing booking
                             gets NULL, which is exactly "no description"
  appointments.location      NULLABLE — "Meeting location", the resolved address
                             text; every existing booking gets NULL ("not recorded")
  blocked_times              a range blocked off on a calendar; sends nothing,
                             enqueues nothing

Every existing row comes out of this unchanged: no appointment's title, times,
status, notes, contact, calendar or deal link is touched, and the reminder queue
(`jobs`) is not mentioned.

The two ADD COLUMNs are plain `op.add_column`, never `batch_alter_table`, which
RECREATES the table on SQLite. Neither carries a foreign key or a default, so each
is one statement on both SQLite and PostgreSQL.

`blocked_times.created_at` is the only non-nullable column that is not an id or a
foreign key, and it carries a server default (`sa.func.now()`, which renders on
both dialects), per the rule.

Revision ID: a5d2c8e4f917
Revises: f3c8e2a61d97
Create Date: 2026-09-13
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a5d2c8e4f917'
# f3c8e2a61d97 is the single head on origin/main (9c1c3bf) and in production.
# `uv run alembic heads` must print exactly one line after this lands.
down_revision: str | Sequence[str] | None = 'f3c8e2a61d97'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('appointments', sa.Column('description', sa.Text(), nullable=True))
    op.add_column('appointments', sa.Column('location', sa.Text(), nullable=True))

    op.create_table(
        'blocked_times',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('calendar_id', sa.Integer(), nullable=False),
        sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ends_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.ForeignKeyConstraint(['calendar_id'], ['calendars.id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_blocked_times_calendar_id', 'blocked_times',
                    ['calendar_id'], unique=False)
    op.create_index('ix_blocked_times_starts_at', 'blocked_times',
                    ['starts_at'], unique=False)


def downgrade() -> None:
    """A true inverse. It discards the blocked times and every description and
    location typed since — acceptable on the way DOWN only. `drop_column` goes
    through `batch_alter_table` so SQLite can drop it."""
    op.drop_index('ix_blocked_times_starts_at', table_name='blocked_times')
    op.drop_index('ix_blocked_times_calendar_id', table_name='blocked_times')
    op.drop_table('blocked_times')
    with op.batch_alter_table('appointments', schema=None) as batch_op:
        batch_op.drop_column('location')
        batch_op.drop_column('description')
