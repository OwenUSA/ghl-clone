"""opportunity tasks, notes, followers, additional contacts, custom-field tabs

The opportunity modal rebuilt to match GoHighLevel (2026-09-13). ADDITIVE ONLY, by
the owner's standing rule — five CREATE TABLEs and one ADD COLUMN. No existing column
is altered, nothing is dropped, and no row is written or backfilled. Nothing is
seeded either: the owner creates his own tabs ("Roof Inspection", "Photo
Checklist", ...) in the UI.

  custom_field_groups     named, ordered tabs of custom fields; apply to every pipeline
  opportunity_tasks       a deal's to-dos; enqueue nothing, notify nobody
  opportunity_notes       a deal's own staff notes (STAFF-only on every path)
  opportunity_followers   user accounts following a deal (link rows)
  opportunity_contacts    a deal's ADDITIONAL contacts, max 10 (link rows)
  custom_field_defs.group_id   NULLABLE — every existing field gets NULL, which is
                               exactly "no tab: shown under Opportunity details",
                               so there is nothing to backfill

Every existing row comes out of this unchanged: no opportunity, contact, field
definition or answer in `opportunities.custom_fields` is touched — including the
`owen_*` join key and the `workiz_*` idempotency keys.

Every non-nullable column carries a server default, per the rule, even though the
tables are new and empty: `position` '0', timestamps CURRENT_TIMESTAMP (via
`sa.func.now()`, which renders on both SQLite and PostgreSQL). Ids and foreign keys
are the only non-nullable columns without one; a row cannot exist without them.

`custom_field_defs.group_id` is a plain ADD COLUMN, never `batch_alter_table`,
which RECREATES the table on SQLite. SQLite has no ALTER TABLE ... ADD CONSTRAINT,
so there the foreign key is written inline in the ADD COLUMN — one statement, the
column and nothing else, and the same unnamed FK `create_all` produces — exactly
as c2d2cc47e4ff did for `appointments.opportunity_id`.

Revision ID: f3c8e2a61d97
Revises: d4a9c1e7b352
Create Date: 2026-09-13
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'f3c8e2a61d97'
# d4a9c1e7b352 (feature/ghl-pipelines) is the single head on origin/main at 35285b3.
# `uv run alembic heads` must print exactly one line after this lands.
down_revision: str | Sequence[str] | None = 'd4a9c1e7b352'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOW = sa.func.now()


def upgrade() -> None:
    op.create_table(
        'custom_field_groups',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=80), nullable=False),
        sa.Column('position', sa.Integer(), server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=NOW,
                  nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'opportunity_tasks',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('opportunity_id', sa.Integer(), nullable=False),
        sa.Column('contact_id', sa.Integer(), nullable=True),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('due_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('assigned_user_id', sa.Integer(), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=NOW,
                  nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=NOW,
                  nullable=False),
        sa.ForeignKeyConstraint(['opportunity_id'], ['opportunities.id']),
        sa.ForeignKeyConstraint(['contact_id'], ['contacts.id']),
        sa.ForeignKeyConstraint(['assigned_user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_opportunity_tasks_opportunity_id', 'opportunity_tasks',
                    ['opportunity_id'], unique=False)
    op.create_index('ix_opportunity_tasks_contact_id', 'opportunity_tasks',
                    ['contact_id'], unique=False)

    op.create_table(
        'opportunity_notes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('opportunity_id', sa.Integer(), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=NOW,
                  nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=NOW,
                  nullable=False),
        sa.ForeignKeyConstraint(['opportunity_id'], ['opportunities.id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_opportunity_notes_opportunity_id', 'opportunity_notes',
                    ['opportunity_id'], unique=False)

    op.create_table(
        'opportunity_followers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('opportunity_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['opportunity_id'], ['opportunities.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('opportunity_id', 'user_id',
                            name='uq_opportunity_follower'),
    )
    op.create_index('ix_opportunity_followers_opportunity_id',
                    'opportunity_followers', ['opportunity_id'], unique=False)
    op.create_index('ix_opportunity_followers_user_id',
                    'opportunity_followers', ['user_id'], unique=False)

    op.create_table(
        'opportunity_contacts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('opportunity_id', sa.Integer(), nullable=False),
        sa.Column('contact_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['opportunity_id'], ['opportunities.id']),
        sa.ForeignKeyConstraint(['contact_id'], ['contacts.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('opportunity_id', 'contact_id',
                            name='uq_opportunity_contact'),
    )
    op.create_index('ix_opportunity_contacts_opportunity_id',
                    'opportunity_contacts', ['opportunity_id'], unique=False)
    op.create_index('ix_opportunity_contacts_contact_id',
                    'opportunity_contacts', ['contact_id'], unique=False)

    if op.get_bind().dialect.name == "sqlite":
        op.execute("ALTER TABLE custom_field_defs ADD COLUMN group_id "
                   "INTEGER REFERENCES custom_field_groups (id)")
    else:
        op.add_column('custom_field_defs', sa.Column(
            'group_id', sa.Integer(), sa.ForeignKey('custom_field_groups.id'),
            nullable=True))
    op.create_index('ix_custom_field_defs_group_id', 'custom_field_defs',
                    ['group_id'], unique=False)


def downgrade() -> None:
    """A true inverse. It discards the tabs, tasks, notes and links themselves —
    never an answer in `opportunities.custom_fields` and never a field definition,
    which only loses its tab. Dropping the column goes through batch mode on SQLite
    (no DROP COLUMN for an indexed column); acceptable on the way DOWN only."""
    with op.batch_alter_table('custom_field_defs', schema=None) as batch_op:
        batch_op.drop_index('ix_custom_field_defs_group_id')
        batch_op.drop_column('group_id')
    op.drop_index('ix_opportunity_contacts_contact_id', table_name='opportunity_contacts')
    op.drop_index('ix_opportunity_contacts_opportunity_id',
                  table_name='opportunity_contacts')
    op.drop_table('opportunity_contacts')
    op.drop_index('ix_opportunity_followers_user_id', table_name='opportunity_followers')
    op.drop_index('ix_opportunity_followers_opportunity_id',
                  table_name='opportunity_followers')
    op.drop_table('opportunity_followers')
    op.drop_index('ix_opportunity_notes_opportunity_id', table_name='opportunity_notes')
    op.drop_table('opportunity_notes')
    op.drop_index('ix_opportunity_tasks_contact_id', table_name='opportunity_tasks')
    op.drop_index('ix_opportunity_tasks_opportunity_id', table_name='opportunity_tasks')
    op.drop_table('opportunity_tasks')
    op.drop_table('custom_field_groups')
