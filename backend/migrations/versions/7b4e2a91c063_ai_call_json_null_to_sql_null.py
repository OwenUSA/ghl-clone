"""ai_call: the JSON value `null` becomes SQL NULL

`ai_call` shipped as a plain nullable JSON column, and every event written since has held
the JSON scalar `null` rather than SQL NULL — because assigning None to a SQLAlchemy JSON
column SERIALISES it. Nothing on screen was wrong (both decode to None), but the column
lied to anyone querying it: on production, 71 rows answered `WHERE ai_call IS NOT NULL`
and not one of them had an agent record. A partial index or a "how many calls did the
agent take?" count would have been wrong in the same direction.

The model now uses `NullableJSONType` (`none_as_null=True`) so new rows are honest. This
repairs the ones already written.

Only `jsonb_typeof(...) = 'null'` rows are touched: a real record is an object, and the
whole point is that the two are different things. Nothing is deleted — a row holding JSON
null holds no information to lose.

Revision ID: 7b4e2a91c063
Revises: 50c12ee623e2
Create Date: 2026-09-23
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '7b4e2a91c063'
down_revision: str | Sequence[str] | None = '50c12ee623e2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("conversation_events", "number_thread_events")


def upgrade() -> None:
    """SQLite has no jsonb_typeof and stores the same value as the text 'null'; Postgres is
    the one that matters here, but both are handled so a dev database does not drift."""
    bind = op.get_bind()
    postgres = bind.dialect.name == "postgresql"
    for table in TABLES:
        if postgres:
            op.execute(sa.text(
                "UPDATE %s SET ai_call = NULL WHERE jsonb_typeof(ai_call) = 'null'" % table))
        else:
            op.execute(sa.text(
                "UPDATE %s SET ai_call = NULL WHERE ai_call = 'null'" % table))


def downgrade() -> None:
    """Deliberately nothing.

    The old state was a bug, and it is not recoverable anyway: once these rows are SQL NULL
    there is no way to tell which of them used to hold JSON null and which were always
    NULL. Writing `null` back into every row would invent the very confusion this removes.
    """
