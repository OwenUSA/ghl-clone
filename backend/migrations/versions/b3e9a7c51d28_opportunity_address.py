"""opportunity address: the property the job is on

An opportunity has never had an address. The only one lives on the CONTACT, so a
customer with several properties keeps one address and each card's property survives
only in its title (one customer holds six cards on six roofs). Workiz stores an
address PER JOB and every AHS work order carries a service address per job, so the
owner approved (2026-09-14): every opportunity stores the address of the job it is.
Four nullable columns arrive, typed exactly like the contact's
(e7a3d1c05f84_contact_address_for_the_workiz_import):

    opportunities.address_street       VARCHAR(255) NULL
    opportunities.address_city         VARCHAR(120) NULL
    opportunities.address_state        VARCHAR(80)  NULL
    opportunities.address_postal_code  VARCHAR(20)  NULL

**Additive only, by the standing rule.** Four `op.add_column` calls and nothing else:
no CREATE TABLE, no ALTER of an existing column, no DROP, no UPDATE, no backfill.
Every existing card keeps every value it holds and gains four NULLs, which is what
"no address recorded on the card" means — the modal then shows the contact's
address as a greyed fallback. Filling the existing cards is the Workiz importer's
job, run by a human afterwards, never this migration's.

All four are nullable, so no `server_default` (the rule is for NON-nullable columns),
and inventing an empty-string default would make "no address" and "a blank address"
indistinguishable — the same reasoning as the contact columns.

Revision ID: b3e9a7c51d28
Revises: c8f2b6d41a93
Create Date: 2026-09-14
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'b3e9a7c51d28'
# c8f2b6d41a93 is the single head on origin/main (46edcf3) and in production.
# `uv run alembic heads` must print exactly one line after this lands.
down_revision: str | Sequence[str] | None = 'c8f2b6d41a93'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Four ADD COLUMNs, nothing else."""
    op.add_column('opportunities', sa.Column('address_street', sa.String(length=255),
                                             nullable=True))
    op.add_column('opportunities', sa.Column('address_city', sa.String(length=120),
                                             nullable=True))
    op.add_column('opportunities', sa.Column('address_state', sa.String(length=80),
                                             nullable=True))
    op.add_column('opportunities', sa.Column('address_postal_code', sa.String(length=20),
                                             nullable=True))


def downgrade() -> None:
    """The exact inverse, and lossy in use: run after an import, it throws away every
    card's address. Nothing else references these columns."""
    op.drop_column('opportunities', 'address_postal_code')
    op.drop_column('opportunities', 'address_state')
    op.drop_column('opportunities', 'address_city')
    op.drop_column('opportunities', 'address_street')
