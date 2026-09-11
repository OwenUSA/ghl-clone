"""contact address, for the Workiz import

`contacts` has never had an address. The Workiz export has one on all 856 client
records, and a roofing CRM that cannot say which roof the job is on is missing the
field the work is organised around, so four nullable columns arrive:

    contacts.address_street       VARCHAR(255) NULL
    contacts.address_city         VARCHAR(120) NULL
    contacts.address_state        VARCHAR(80)  NULL
    contacts.address_postal_code  VARCHAR(20)  NULL

**Additive only, and mechanically so.** This migration contains four `add_column`
calls and nothing else: no `CREATE TABLE`, no `ALTER COLUMN`, no `DROP`, no
`UPDATE`, no backfill. Every existing row keeps every value it holds and gains four
NULLs, which is what "we do not know this contact's address" already means — the
same stance `phone` takes for a contact nobody has a number for. Production holds
real records (CLAUDE.md) and this is safe against them.

All four are nullable, so `server_default` is not needed and is deliberately not
given: the repo rule requires one on a new NON-nullable column, and inventing an
empty-string default here would make "no address recorded" and "an address recorded
as blank" indistinguishable.

Four columns rather than one text blob because the two exports disagree about the
shape — `workiz_jobs.csv` already carries City / State / Zip code separately while
`workiz_clients.csv` has one combined string — and splitting is the only way those
two can describe the same contact. See `app/workiz_import.py`.

Revision ID: e7a3d1c05f84
Revises: a1f4c7d92b30
Create Date: 2026-09-11 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e7a3d1c05f84'
down_revision: str | Sequence[str] | None = 'a1f4c7d92b30'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema. Four ADD COLUMNs, nothing else."""
    op.add_column('contacts', sa.Column('address_street', sa.String(length=255),
                                        nullable=True))
    op.add_column('contacts', sa.Column('address_city', sa.String(length=120),
                                        nullable=True))
    op.add_column('contacts', sa.Column('address_state', sa.String(length=80),
                                        nullable=True))
    op.add_column('contacts', sa.Column('address_postal_code', sa.String(length=20),
                                        nullable=True))


def downgrade() -> None:
    """Downgrade schema.

    This one IS a perfect inverse of the upgrade, and that is worth saying out loud
    because it also means it is **lossy in use**: running it after an import throws
    away every address the import wrote. Nothing else in the schema references these
    columns, so the drop is otherwise unremarkable.
    """
    op.drop_column('contacts', 'address_postal_code')
    op.drop_column('contacts', 'address_state')
    op.drop_column('contacts', 'address_city')
    op.drop_column('contacts', 'address_street')
