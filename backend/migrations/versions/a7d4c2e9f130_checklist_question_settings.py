"""checklist question settings: a script, a linked value, a details box

The call Checklist (2026-09-14) is built from ordinary custom fields in one custom tab,
so the owner can reword, reorder, add and archive its questions himself. Three things a
question can now carry need somewhere to live, and each is a setting on the DEFINITION,
never an answer:

    custom_field_defs.script        TEXT         NULL  what the dispatcher says,
                                                       drawn under the question
    custom_field_defs.linked_field  VARCHAR(40)  NULL  yes/no only: show the contact's
                                                       email or the card's address
                                                       beside the tick
    custom_field_defs.details_when  JSON         NULL  dropdown only: the options that
                                                       open a details box

**Why columns and not the existing `options` JSON.** `options` is a list of strings
that every client already reads as the dropdown's choices (the browser, the CLI, the
CSV export). Turning it into an object would change its shape under every one of them;
tucking settings into the list would make a setting look like a choice. Three typed
nullable columns change nothing that exists and say what they are.

**Additive only, by the standing rule.** Three `op.add_column` calls and nothing else:
no CREATE TABLE, no ALTER of an existing column, no DROP, no UPDATE, no backfill. Every
existing field reads NULL in all three, which is exactly "no setting". All three are
nullable, so no `server_default` (the rule is for NON-nullable columns). The new answer
type `paragraph` is a value in the existing VARCHAR(20) `field_type`, and the Checklist
itself is created by `python -m app.checklist_seed`, never here.

Revision ID: a7d4c2e9f130
Revises: b3e9a7c51d28
Create Date: 2026-09-14
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'a7d4c2e9f130'
# b3e9a7c51d28 is the single head on origin/main (c1be901) and in production.
# `uv run alembic heads` must print exactly one line after this lands.
down_revision: str | Sequence[str] | None = 'b3e9a7c51d28'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Mirrors models.JSONType: JSONB on Postgres, plain JSON on SQLite.
JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    """Three ADD COLUMNs, nothing else."""
    op.add_column('custom_field_defs', sa.Column('script', sa.Text(), nullable=True))
    op.add_column('custom_field_defs', sa.Column('linked_field', sa.String(length=40),
                                                 nullable=True))
    op.add_column('custom_field_defs', sa.Column('details_when', JSON_TYPE, nullable=True))


def downgrade() -> None:
    """The exact inverse. Lossy for the settings only: no answer lives in these
    columns, so every recorded answer survives a downgrade."""
    op.drop_column('custom_field_defs', 'details_when')
    op.drop_column('custom_field_defs', 'linked_field')
    op.drop_column('custom_field_defs', 'script')
