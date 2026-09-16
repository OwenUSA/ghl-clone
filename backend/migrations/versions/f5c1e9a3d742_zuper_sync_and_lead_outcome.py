"""zuper sync and lead outcome: mappings, sync state, webhook inbox, conflict log, delete
snapshots, money documents, digests, settings; opportunities.lead_outcome*

The Zuper sync (DECISIONS.md, 2026-09-16, with the owner's revised decisions), shipped switched
OFF, and the CRM-only lead outcome. EIGHT new tables:

    zuper_settings          one row: the switch, the Workiz cutover date, the setup check
    zuper_mappings          CRM record <-> Zuper record, the agreed field view, content hashes;
                            also the initial load's per-record checkpoint (state "creating")
    zuper_sync_state        one row: the sweep's cursor and heartbeat, the load report
    zuper_webhook_inbox     raw deliveries, stored before they are processed
    zuper_conflict_log      every value one side changed that the sync overwrote
    zuper_delete_snapshots  a restorable copy of every record the sync deleted, or whose
                            delete it mirrored
    zuper_documents         quotes and invoices as Zuper last reported them (no customer data)
    zuper_digests           one daily activity note per customer per day

and THREE nullable columns ADDED to `opportunities` — the lead outcome, CRM-only:

    opportunities.lead_outcome          VARCHAR(40) NULL   one of app/lead_outcomes.OUTCOMES
    opportunities.lead_outcome_note     TEXT NULL          required for "Other"
    opportunities.lead_outcome_set_at   TIMESTAMP NULL     the report's period

Columns on the card, not a table: one value per card, read with the card by the board, the
modal, bulk actions and every report, never a history. NULL for every existing card — "no
outcome recorded", which is true — and nothing backfills them.

**Additive only, by the standing rule.** CREATE TABLE, CREATE INDEX and ADD COLUMN (nullable,
plain `op.add_column`): no ALTER of an existing column, no DROP, no UPDATE, no backfill; no
existing row is read or written.
Every reference to a CRM record is a plain integer with no foreign key, so no existing delete
path gains a constraint to trip over. `server_default` on every non-nullable column that has a
default in the model. Plain `op.create_index`, never `batch_alter_table`, in upgrade().

Revision ID: f5c1e9a3d742
Revises: e1b4d7c96a05
Create Date: 2026-09-16
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'f5c1e9a3d742'
# e1b4d7c96a05 (message attachments, feature/mms-images) is the single head on main and in
# production (operator, 2026-09-16). `uv run alembic heads` must print exactly one line.
down_revision: str | Sequence[str] | None = 'e1b4d7c96a05'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOW = sa.func.now()
JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def _ts(name: str, *, now: bool = False) -> sa.Column:
    if now:
        return sa.Column(name, sa.DateTime(timezone=True), server_default=NOW, nullable=False)
    return sa.Column(name, sa.DateTime(timezone=True), nullable=True)


def _str(name: str, length: int, *, nullable: bool = True, default: str | None = None
         ) -> sa.Column:
    return sa.Column(name, sa.String(length=length), server_default=default,
                     nullable=nullable)


def _index(table: str, *cols: str, unique: bool = False) -> None:
    op.create_index('ix_%s_%s' % (table, '_'.join(cols)), table, list(cols), unique=unique)


def upgrade() -> None:
    op.create_table(
        'zuper_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('enabled', sa.Boolean(), server_default=sa.false(), nullable=False),
        _str('workiz_cutover_date', 10),
        _ts('enabled_at'),
        _ts('setup_checked_at'),
        sa.Column('setup_passed', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('setup_results', JSON, nullable=True),
        sa.Column('confirmations', JSON, nullable=True),
        _str('sync_user_uid', 64),
        sa.Column('lead_sources', JSON, nullable=True),
        _ts('updated_at'),
        sa.Column('updated_by_id', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'zuper_mappings',
        sa.Column('id', sa.Integer(), nullable=False),
        _str('crm_type', 20, nullable=False),
        sa.Column('crm_id', sa.Integer(), nullable=False),
        _str('zuper_type', 20, nullable=False),
        _str('zuper_uid', 64),
        _str('parent_uid', 64),
        _str('state', 20, nullable=False, default='linked'),
        sa.Column('base', JSON, nullable=True),
        _str('crm_hash', 64),
        _str('zuper_hash', 64),
        _str('zuper_updated_at', 40),
        _ts('last_pushed_at'),
        _ts('last_pulled_at'),
        sa.Column('last_error', sa.Text(), nullable=True),
        _ts('created_at', now=True),
        _ts('updated_at'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('crm_type', 'crm_id', name='uq_zuper_mapping_crm'),
        sa.UniqueConstraint('zuper_type', 'zuper_uid', name='uq_zuper_mapping_zuper'),
    )
    _index('zuper_mappings', 'crm_type')
    _index('zuper_mappings', 'parent_uid')
    _index('zuper_mappings', 'zuper_uid')

    op.create_table(
        'zuper_sync_state',
        sa.Column('id', sa.Integer(), nullable=False),
        _ts('last_sweep_started_at'),
        _ts('last_sweep_finished_at'),
        _ts('last_sweep_success_at'),
        _ts('sweep_cursor'),
        sa.Column('filter_checks', JSON, nullable=True),
        _ts('last_push_at'),
        _ts('last_pull_at'),
        _ts('last_webhook_at'),
        sa.Column('last_error', sa.Text(), nullable=True),
        _ts('last_error_at'),
        sa.Column('last_counts', JSON, nullable=True),
        sa.Column('load_report', JSON, nullable=True),
        _str('last_digest_day', 10),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'zuper_webhook_inbox',
        sa.Column('id', sa.Integer(), nullable=False),
        _ts('received_at', now=True),
        sa.Column('body', sa.Text(), nullable=False),
        _str('body_sha256', 64, nullable=False),
        _str('content_type', 120),
        _str('event', 120),
        _str('module', 60),
        _str('record_uid', 64),
        _str('signature', 20, nullable=False, default='none'),
        _str('status', 20, nullable=False, default='pending'),
        sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
        _str('outcome', 200),
        _ts('processed_at'),
        sa.Column('error', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    _index('zuper_webhook_inbox', 'body_sha256', unique=True)
    _index('zuper_webhook_inbox', 'received_at')
    _index('zuper_webhook_inbox', 'status')

    op.create_table(
        'zuper_conflict_log',
        sa.Column('id', sa.Integer(), nullable=False),
        _ts('occurred_at', now=True),
        _str('crm_type', 20, nullable=False),
        sa.Column('crm_id', sa.Integer(), nullable=True),
        _str('zuper_uid', 64),
        _str('field', 120, nullable=False),
        _str('rule', 40, nullable=False),
        _str('winner', 10, nullable=False),
        _str('written_to', 10, nullable=False),
        sa.Column('before', JSON, nullable=True),
        sa.Column('after', JSON, nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    _index('zuper_conflict_log', 'occurred_at')

    op.create_table(
        'zuper_delete_snapshots',
        sa.Column('id', sa.Integer(), nullable=False),
        _str('batch', 40, nullable=False),
        _ts('occurred_at', now=True),
        _str('direction', 20, nullable=False),
        _str('crm_type', 20, nullable=False),
        sa.Column('crm_id', sa.Integer(), nullable=False),
        _str('zuper_type', 20),
        _str('zuper_uid', 64),
        sa.Column('snapshot', JSON, nullable=False),
        _str('state', 20, nullable=False, default='pending'),
        _ts('mirrored_at'),
        sa.Column('error', sa.Text(), nullable=True),
        _ts('restored_at'),
        sa.Column('restored_by_id', sa.Integer(), nullable=True),
        sa.Column('restore_detail', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    _index('zuper_delete_snapshots', 'batch')
    _index('zuper_delete_snapshots', 'occurred_at')
    _index('zuper_delete_snapshots', 'state')

    op.create_table(
        'zuper_documents',
        sa.Column('id', sa.Integer(), nullable=False),
        _str('kind', 10, nullable=False),
        _str('zuper_uid', 64, nullable=False),
        _str('number', 60),
        _str('status', 40),
        sa.Column('total_cents', sa.Integer(), nullable=True),
        sa.Column('balance_cents', sa.Integer(), nullable=True),
        _str('issued_on', 20),
        _str('due_on', 20),
        _str('job_uid', 64),
        _str('customer_uid', 64),
        sa.Column('opportunity_id', sa.Integer(), nullable=True),
        sa.Column('contact_id', sa.Integer(), nullable=True),
        _str('zuper_updated_at', 40),
        _ts('fetched_at', now=True),
        sa.Column('removed_in_zuper', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    _index('zuper_documents', 'zuper_uid', unique=True)
    _index('zuper_documents', 'job_uid')
    _index('zuper_documents', 'customer_uid')
    _index('zuper_documents', 'opportunity_id')
    _index('zuper_documents', 'contact_id')

    op.create_table(
        'zuper_digests',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('contact_id', sa.Integer(), nullable=False),
        _str('day', 10, nullable=False),
        sa.Column('opportunity_id', sa.Integer(), nullable=True),
        _str('job_uid', 64),
        sa.Column('counts', JSON, nullable=False),
        _str('note_uid', 64),
        _str('state', 20, nullable=False, default='pending'),
        _ts('posted_at'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('contact_id', 'day', name='uq_zuper_digest'),
    )
    _index('zuper_digests', 'contact_id')

    op.add_column('opportunities', sa.Column('lead_outcome', sa.String(length=40), nullable=True))
    op.add_column('opportunities', sa.Column('lead_outcome_note', sa.Text(), nullable=True))
    op.add_column('opportunities', sa.Column('lead_outcome_set_at', sa.DateTime(timezone=True),
                                             nullable=True))


def downgrade() -> None:
    """The exact inverse, and lossy in use: it forgets every mapping, snapshot, conflict,
    cached document and lead outcome. Dropping a table drops its indexes. Batch mode for the
    column drops on SQLite; acceptable on the way DOWN only."""
    with op.batch_alter_table('opportunities', schema=None) as batch_op:
        batch_op.drop_column('lead_outcome_set_at')
        batch_op.drop_column('lead_outcome_note')
        batch_op.drop_column('lead_outcome')
    for table in ('zuper_digests', 'zuper_documents', 'zuper_delete_snapshots',
                  'zuper_conflict_log', 'zuper_webhook_inbox', 'zuper_sync_state',
                  'zuper_mappings', 'zuper_settings'):
        op.drop_table(table)
