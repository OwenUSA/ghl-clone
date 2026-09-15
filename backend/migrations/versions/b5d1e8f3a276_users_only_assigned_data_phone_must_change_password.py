"""users: only_assigned_data, phone, must_change_password — "Only assigned data" and My Staff

"Only assigned data" (DECISIONS.md, 2026-09-15): a user with it on sees only their own
jobs — opportunities they own, or that have a non-cancelled appointment on their calendar
or assigned to them — and the contacts, conversations, calendar entries, tasks, notes and
photos of those jobs. Reporting, the Dashboard and the Forecast refuse them.

My Staff (the operator's addendum, same day) needs two more on the same table, in this same
ONE revision:

    users.only_assigned_data    BOOLEAN NOT NULL DEFAULT false
    users.phone                 VARCHAR(40) NULL      — shown in My Staff
    users.must_change_password  BOOLEAN NOT NULL DEFAULT false
                                — set when an ADMIN creates a user or resets a password;
                                  the API then refuses everything but changing it

**Additive only, by the standing rule.** THREE `add_column`s, nothing else: no CREATE
TABLE, no ALTER of an existing column, no DROP, no UPDATE, no backfill. Both booleans are
NOT NULL with `server_default` false, so every existing user — including every TECH already
in production — comes out of this with `false`: exactly their access today, and nobody is
forced to change a password they already use. `phone` is nullable: no number recorded.
Nothing here turns the switch on for anyone; the API turns it on only for a user CREATED as
a TECH from now on, and an ADMIN turns it on for an existing user in Settings → My Staff.

Plain `op.add_column`, never `batch_alter_table`: a column with a constant server default
is a single ALTER TABLE ... ADD COLUMN on both SQLite and PostgreSQL.

Revision ID: b5d1e8f3a276
Revises: c4e8a2f6b913
Create Date: 2026-09-15
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'b5d1e8f3a276'
# c4e8a2f6b913 (CompanyCam) is the single head on origin/main and in production.
# `uv run alembic heads` must print exactly one line after this lands.
down_revision: str | Sequence[str] | None = 'c4e8a2f6b913'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Three ADD COLUMNs. Every existing user gets false, false and no phone."""
    op.add_column('users', sa.Column('only_assigned_data', sa.Boolean(),
                                     server_default=sa.false(), nullable=False))
    op.add_column('users', sa.Column('phone', sa.String(length=40), nullable=True))
    op.add_column('users', sa.Column('must_change_password', sa.Boolean(),
                                     server_default=sa.false(), nullable=False))


def downgrade() -> None:
    """The exact inverse, and lossy in use: it forgets who was restricted (so every
    restricted user goes back to seeing everything), every staff phone number, and every
    pending forced password change. Batch mode on SQLite; acceptable on the way DOWN only."""
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('must_change_password')
        batch_op.drop_column('phone')
        batch_op.drop_column('only_assigned_data')
