"""job questions on opportunities, and a booking's deal

Three additions, and nothing else. This migration CREATES and ADDS; it alters no
existing column, drops nothing, rewrites no row and backfills nothing. That is the
owner's standing rule from the telephony work — add new functionality, do not
override the database — and it matters here because production is not empty
(CLAUDE.md): 12 contacts, 10 opportunities, 6 calls, 8 appointments, 1 pipeline,
all real.

  * `custom_field_defs` — the job questions the owner defines himself. It describes
    fields; it never holds an answer. The answers stay exactly where they already
    are, in `opportunities.custom_fields`, which this migration does not touch. So
    there is no data migration to get wrong, and no way for this to lose one.

  * `custom_field_pipelines` — which pipelines each question is asked on. One row
    per attachment, because a definition may attach to several pipelines.

  * `appointments.opportunity_id` — nullable, indexed, foreign key to
    `opportunities.id`. Every existing booking gets NULL, which is exactly what
    "not linked to a deal" means, so there is nothing to backfill. Nullable is
    also why no `server_default` is needed; the non-nullable columns in the two
    new tables carry one where the model does.

The `appointments` half is written as a plain `op.add_column` rather than the
`op.batch_alter_table` block autogenerate emits. On SQLite, batch mode implements
an ALTER by RECREATING the table — create, copy, drop, rename — which is precisely
the shape of operation this migration promises not to perform. `ALTER TABLE ... ADD
COLUMN` is supported natively by both SQLite and PostgreSQL, so the honest
operation is also the portable one — one statement per dialect, adding a column
and nothing else.

Generated against a throwaway SQLite database, per CLAUDE.md — never against
`dtr_ghl_clone` and never against production.

Revision ID: c2d2cc47e4ff
Revises: a1f4c7d92b30
Create Date: 2026-09-11 19:29:04.264364

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c2d2cc47e4ff'
down_revision: str | Sequence[str] | None = 'e7a3d1c05f84'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Mirrors models.JSONType: JSONB on Postgres (indexable), plain JSON on SQLite.
JSON_TYPE = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'custom_field_defs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(length=64), nullable=False),
        sa.Column('label', sa.String(length=160), nullable=False),
        sa.Column('field_type', sa.String(length=20), nullable=False),
        sa.Column('options', JSON_TYPE, nullable=False),
        sa.Column('entity', sa.String(length=20),
                  server_default='opportunity', nullable=False),
        sa.Column('position', sa.Integer(), server_default='0', nullable=False),
        sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_custom_field_defs_key', 'custom_field_defs',
                    ['key'], unique=True)

    op.create_table(
        'custom_field_pipelines',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('field_id', sa.Integer(), nullable=False),
        sa.Column('pipeline_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['field_id'], ['custom_field_defs.id']),
        sa.ForeignKeyConstraint(['pipeline_id'], ['pipelines.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('field_id', 'pipeline_id',
                            name='uq_custom_field_pipeline'),
    )
    op.create_index('ix_custom_field_pipelines_field_id',
                    'custom_field_pipelines', ['field_id'], unique=False)
    op.create_index('ix_custom_field_pipelines_pipeline_id',
                    'custom_field_pipelines', ['pipeline_id'], unique=False)

    if op.get_bind().dialect.name == "sqlite":
        # SQLite has no ALTER TABLE ... ADD CONSTRAINT, so alembic's add_column
        # cannot attach the foreign key in a second statement the way it does on
        # PostgreSQL. It CAN be written inline in the ADD COLUMN, which is one
        # statement, adds nothing but the column, and leaves the same unnamed FK
        # that `create_all` produces on this dialect — so `alembic check` stays
        # clean. The alternative is batch mode, which recreates the table.
        op.execute("ALTER TABLE appointments ADD COLUMN opportunity_id "
                   "INTEGER REFERENCES opportunities (id)")
    else:
        op.add_column('appointments', sa.Column(
            'opportunity_id', sa.Integer(),
            sa.ForeignKey('opportunities.id'), nullable=True))
    op.create_index('ix_appointments_opportunity_id', 'appointments',
                    ['opportunity_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema.

    A true inverse: the two new tables and the one new column come back out, and
    nothing that existed before the upgrade is touched. It does of course discard
    the field definitions themselves — but NOT a single answer, because the answers
    never left `opportunities.custom_fields`. Downgrading turns the questions back
    into anonymous keys in a blob; it does not lose what anybody typed.

    Dropping the column on SQLite goes through `batch_alter_table`, which recreates
    the table: SQLite gained `DROP COLUMN` only in 3.35 and still refuses it for an
    indexed column. That rewrite is acceptable on the way DOWN — it is a rollback,
    and it is not what runs against production.
    """
    with op.batch_alter_table('appointments', schema=None) as batch_op:
        batch_op.drop_index('ix_appointments_opportunity_id')
        batch_op.drop_column('opportunity_id')

    op.drop_index('ix_custom_field_pipelines_pipeline_id',
                  table_name='custom_field_pipelines')
    op.drop_index('ix_custom_field_pipelines_field_id',
                  table_name='custom_field_pipelines')
    op.drop_table('custom_field_pipelines')

    op.drop_index('ix_custom_field_defs_key', table_name='custom_field_defs')
    op.drop_table('custom_field_defs')
