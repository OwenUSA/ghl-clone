"""mirrored feeds: dedupe_key, source_system, source_number on conversation_events

Purely additive — three nullable columns and one unique index. No existing row is
touched, no existing column changes type, and nothing that writes events today
supplies any of them, so applying this changes the behaviour of nothing.

WHY dedupe_key IS A NEW COLUMN AND NOT A UNIQUE INDEX ON provider_ref

Because `provider_ref` cannot carry that meaning. owen-main's BulkVS call path
deliberately sends the SAME provider_ref on all three call lifecycle phases —
"calls.id, on EVERY phase — the join key must not depend on which event survived"
(integrations/crm/events.py). A unique index on it would collapse started/answered/
ended into one row and break the live path on the first call after deploy.

So idempotency is OPT-IN, on its own column, used only by a feed that may deliver
the same object twice: the OpenPhone mirror's poll re-reads its window every tick,
and a `crm_report` job retried after the response to a successful POST was lost
would otherwise put a second copy of a customer's text on their timeline.

NULLs and the unique index: Postgres and SQLite both treat NULLs as distinct in a
UNIQUE index, so every existing row and every row the BulkVS path writes is
unaffected. That is what makes this safe to apply to a live database with the old
code still running — the old code does not know these columns exist.

Revision ID: b7e3f1a8c204
Revises: c2d2cc47e4ff
Create Date: 2026-09-11
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'b7e3f1a8c204'
# Rebased onto c2d2cc47e4ff (job questions + appointments.opportunity_id), which is
# the head ALREADY APPLIED IN PRODUCTION. Pointing at the older a1f4c7d92b30 would
# leave Alembic with TWO heads, and a two-head tree makes the production deploy fail
# outright -- that happened on 2026-09-11 and cost an outage on /api/custom-fields.
# `uv run alembic heads` must print exactly one line.
down_revision: str | Sequence[str] | None = 'c2d2cc47e4ff'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('conversation_events',
                  sa.Column('dedupe_key', sa.String(length=200), nullable=True))
    op.add_column('conversation_events',
                  sa.Column('source_system', sa.String(length=40), nullable=True))
    op.add_column('conversation_events',
                  sa.Column('source_number', sa.String(length=40), nullable=True))
    # THE constraint. Named explicitly so a later migration can drop it by name
    # rather than guessing what the autogenerator called it.
    op.create_index('uq_conversation_events_dedupe_key', 'conversation_events',
                    ['dedupe_key'], unique=True)


def downgrade() -> None:
    op.drop_index('uq_conversation_events_dedupe_key',
                  table_name='conversation_events')
    op.drop_column('conversation_events', 'source_number')
    op.drop_column('conversation_events', 'source_system')
    op.drop_column('conversation_events', 'dedupe_key')
