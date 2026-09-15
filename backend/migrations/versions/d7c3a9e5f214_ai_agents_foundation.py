"""ai agents, phase 1: connections, agents and versions, knowledge, runs, suggestions, alerts

GoHighLevel's AI Agents module, foundation only (DECISIONS.md, 2026-09-15). Fifteen NEW
tables:

    ai_settings          one row: "Pause all AI agents", the on-call phone
    ai_connections       provider accounts; the API key only as a Fernet token
    ai_folders           the Agents list's folders
    ai_agents            an agent: its mode (always created "off") and its draft
    ai_agent_versions    immutable published configs (owen-main's agent_versions shape)
    ai_knowledge_bases   ...their FAQs, articles and files (ai_kb_items)
    ai_kb_items
    ai_kb_chunks         searchable text, lexical terms
    ai_knowledge_gaps    questions an agent could not answer, deduplicated
    ai_runs              every trigger, including the ones skipped, with tokens and cost
    ai_run_steps         a run's transcript
    ai_suggestions       Suggest-mode writes waiting for a person
    ai_agent_threads     per agent per customer: texts sent, asleep after a staff reply
    ai_templates         saved agent configs (none shipped)
    ai_alerts            the in-app bell — this CRM had no notification mechanism

and SIX nullable/defaulted columns ADDED to existing tables, so a record an agent wrote says
so ("AI: <agent name>"):

    conversation_events.ai_agent_id     INTEGER NULL
    number_thread_events.ai_agent_id    INTEGER NULL   (column for column with the above)
    opportunity_notes.ai_agent_id       INTEGER NULL
    opportunity_tasks.ai_agent_id       INTEGER NULL
    opportunity_tasks.priority          VARCHAR(20) NOT NULL DEFAULT 'normal'
    appointments.ai_agent_id            INTEGER NULL

**Additive only, by the standing rule.** CREATE TABLE and ADD COLUMN, nothing else: no
ALTER of an existing column, no DROP, no UPDATE, no backfill; no existing row is read or
written. Every existing task reads priority 'normal' and every existing row reads
ai_agent_id NULL — "not written by an agent", which is true. The added columns are plain
integers with no foreign key, so ADD COLUMN stays a single statement on SQLite and
PostgreSQL. A run or a suggestion refers to a contact, deal, conversation or appointment by
plain integer too: logs are kept indefinitely and must never block, or be broken by, a
contact delete. Plain `op.create_index` / `op.add_column`, never `batch_alter_table`, in
upgrade().

Revision ID: d7c3a9e5f214
Revises: b5d1e8f3a276
Create Date: 2026-09-15
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'd7c3a9e5f214'
# b5d1e8f3a276 ("Only assigned data" and My Staff) is the single head on origin/main and in
# production. `uv run alembic heads` must print exactly one line after this lands.
down_revision: str | Sequence[str] | None = 'b5d1e8f3a276'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOW = sa.func.now()
JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def _ts(name: str, *, now: bool = False, nullable: bool = True) -> sa.Column:
    if now:
        return sa.Column(name, sa.DateTime(timezone=True), server_default=NOW, nullable=False)
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _zero(name: str) -> sa.Column:
    return sa.Column(name, sa.Integer(), server_default='0', nullable=False)


def _index(table: str, *cols: str, unique: bool = False, name: str | None = None) -> None:
    op.create_index(name or 'ix_%s_%s' % (table, '_'.join(cols)), table, list(cols),
                    unique=unique)


def upgrade() -> None:
    """Fifteen CREATE TABLEs with their indexes, then six ADD COLUMNs. Nothing else."""
    op.create_table(
        'ai_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('paused', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('on_call_phone', sa.String(length=40), nullable=True),
        _ts('updated_at'),
        sa.Column('updated_by_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['updated_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'ai_connections',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('provider', sa.String(length=30), nullable=False),
        sa.Column('base_url', sa.String(length=500), nullable=True),
        sa.Column('api_key_encrypted', sa.Text(), nullable=False),
        sa.Column('api_key_last4', sa.String(length=8), server_default='', nullable=False),
        sa.Column('default_model', sa.String(length=200), nullable=False),
        sa.Column('price_input_micros', sa.Integer(), nullable=True),
        sa.Column('price_output_micros', sa.Integer(), nullable=True),
        _ts('created_at', now=True),
        _ts('updated_at'),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'ai_folders',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        _zero('position'),
        _ts('created_at', now=True),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'ai_agents',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('folder_id', sa.Integer(), nullable=True),
        sa.Column('channel', sa.String(length=20), server_default='text', nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('mode', sa.String(length=20), server_default='off', nullable=False),
        sa.Column('draft', JSON, nullable=False),
        sa.Column('published_version_id', sa.Integer(), nullable=True),
        _ts('draft_updated_at'),
        _ts('created_at', now=True),
        _ts('updated_at'),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        _ts('archived_at'),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.ForeignKeyConstraint(['folder_id'], ['ai_folders.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    _index('ai_agents', 'folder_id')

    op.create_table(
        'ai_agent_versions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('agent_id', sa.Integer(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('config', JSON, nullable=False),
        _ts('published_at', now=True),
        sa.Column('published_by_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['agent_id'], ['ai_agents.id']),
        sa.ForeignKeyConstraint(['published_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('agent_id', 'version', name='uq_ai_agent_version'),
    )
    _index('ai_agent_versions', 'agent_id')

    op.create_table(
        'ai_knowledge_bases',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        _ts('created_at', now=True),
        _ts('updated_at'),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'ai_kb_items',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('kb_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('title', sa.String(length=500), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('content_type', sa.String(length=120), nullable=True),
        sa.Column('size_bytes', sa.Integer(), nullable=True),
        _ts('created_at', now=True),
        _ts('updated_at'),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.ForeignKeyConstraint(['kb_id'], ['ai_knowledge_bases.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    _index('ai_kb_items', 'kb_id')

    op.create_table(
        'ai_kb_chunks',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('item_id', sa.Integer(), nullable=False),
        sa.Column('kb_id', sa.Integer(), nullable=False),
        _zero('position'),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('terms', sa.Text(), server_default='', nullable=False),
        sa.ForeignKeyConstraint(['item_id'], ['ai_kb_items.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    _index('ai_kb_chunks', 'item_id')
    _index('ai_kb_chunks', 'kb_id')

    op.create_table(
        'ai_knowledge_gaps',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('question_key', sa.String(length=500), nullable=False),
        sa.Column('question', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=20), server_default='open', nullable=False),
        sa.Column('count', sa.Integer(), server_default='1', nullable=False),
        sa.Column('agent_id', sa.Integer(), nullable=True),
        sa.Column('last_run_id', sa.Integer(), nullable=True),
        _ts('first_seen_at', now=True),
        _ts('last_seen_at', now=True),
        sa.Column('resolved_item_id', sa.Integer(), nullable=True),
        _ts('decided_at'),
        sa.Column('decided_by_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['decided_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    _index('ai_knowledge_gaps', 'question_key', unique=True)
    _index('ai_knowledge_gaps', 'status')

    op.create_table(
        'ai_runs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('agent_id', sa.Integer(), nullable=False),
        sa.Column('agent_name', sa.String(length=120), server_default='', nullable=False),
        sa.Column('version_id', sa.Integer(), nullable=True),
        sa.Column('version', sa.Integer(), nullable=True),
        sa.Column('trigger', sa.String(length=40), nullable=False),
        sa.Column('trigger_ref', sa.String(length=120), nullable=True),
        sa.Column('contact_id', sa.Integer(), nullable=True),
        sa.Column('opportunity_id', sa.Integer(), nullable=True),
        sa.Column('appointment_id', sa.Integer(), nullable=True),
        sa.Column('conversation_id', sa.Integer(), nullable=True),
        sa.Column('subject_label', sa.String(length=300), nullable=True),
        sa.Column('mode', sa.String(length=20), nullable=False),
        sa.Column('is_test', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('outcome', sa.String(length=20), server_default='queued', nullable=False),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('connection_id', sa.Integer(), nullable=True),
        sa.Column('provider', sa.String(length=30), nullable=True),
        sa.Column('model', sa.String(length=200), nullable=True),
        _zero('input_tokens'),
        _zero('output_tokens'),
        _zero('cache_write_tokens'),
        _zero('cache_read_tokens'),
        sa.Column('cost_micros', sa.Integer(), nullable=True),
        sa.Column('latency_ms', sa.Integer(), nullable=True),
        _ts('run_after'),
        _ts('started_at'),
        _ts('finished_at'),
        _ts('created_at', now=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    _index('ai_runs', 'agent_id', 'created_at', name='ix_ai_runs_agent_created')
    _index('ai_runs', 'agent_id')
    _index('ai_runs', 'contact_id')
    _index('ai_runs', 'opportunity_id')
    _index('ai_runs', 'outcome')
    _index('ai_runs', 'created_at')

    op.create_table(
        'ai_run_steps',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('run_id', sa.Integer(), nullable=False),
        _zero('position'),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('text', sa.Text(), nullable=True),
        sa.Column('tool_name', sa.String(length=60), nullable=True),
        sa.Column('tool_call_id', sa.String(length=120), nullable=True),
        sa.Column('data', JSON, nullable=True),
        sa.Column('action_status', sa.String(length=20), nullable=True),
        _ts('created_at', now=True),
        sa.ForeignKeyConstraint(['run_id'], ['ai_runs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    _index('ai_run_steps', 'run_id')

    op.create_table(
        'ai_suggestions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('run_id', sa.Integer(), nullable=False),
        sa.Column('agent_id', sa.Integer(), nullable=False),
        sa.Column('action', sa.String(length=60), nullable=False),
        sa.Column('args', JSON, nullable=False),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('contact_id', sa.Integer(), nullable=True),
        sa.Column('opportunity_id', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(length=20), server_default='pending', nullable=False),
        sa.Column('result', JSON, nullable=True),
        _ts('created_at', now=True),
        _ts('decided_at'),
        sa.Column('decided_by_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['decided_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    for col in ('run_id', 'agent_id', 'contact_id', 'opportunity_id', 'status'):
        _index('ai_suggestions', col)

    op.create_table(
        'ai_agent_threads',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('agent_id', sa.Integer(), nullable=False),
        sa.Column('contact_id', sa.Integer(), nullable=False),
        _zero('messages_sent'),
        _ts('asleep_at'),
        sa.Column('asleep_reason', sa.String(length=200), nullable=True),
        _ts('updated_at'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('agent_id', 'contact_id', name='uq_ai_agent_thread'),
    )
    _index('ai_agent_threads', 'agent_id')
    _index('ai_agent_threads', 'contact_id')

    op.create_table(
        'ai_templates',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('channel', sa.String(length=20), server_default='text', nullable=False),
        sa.Column('config', JSON, nullable=False),
        _ts('created_at', now=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'ai_alerts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=30), nullable=False),
        sa.Column('title', sa.String(length=300), nullable=False),
        sa.Column('body', sa.Text(), nullable=True),
        sa.Column('urgent', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('run_id', sa.Integer(), nullable=True),
        sa.Column('agent_id', sa.Integer(), nullable=True),
        sa.Column('contact_id', sa.Integer(), nullable=True),
        sa.Column('opportunity_id', sa.Integer(), nullable=True),
        sa.Column('task_id', sa.Integer(), nullable=True),
        _ts('created_at', now=True),
        _ts('read_at'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    _index('ai_alerts', 'user_id')
    _index('ai_alerts', 'created_at')

    # "AI: <agent name>" on what an agent writes. Nullable integers, and one defaulted
    # string: every existing row reads NULL / 'normal'.
    for table in ('conversation_events', 'number_thread_events', 'opportunity_notes',
                  'opportunity_tasks', 'appointments'):
        op.add_column(table, sa.Column('ai_agent_id', sa.Integer(), nullable=True))
    op.add_column('opportunity_tasks', sa.Column('priority', sa.String(length=20),
                                                 server_default='normal', nullable=False))


def downgrade() -> None:
    """The exact inverse, and lossy in use: it forgets every agent, version, knowledge
    base, run log, suggestion and alert, and which records an agent wrote. Batch mode for
    the column drops on SQLite; acceptable on the way DOWN only."""
    with op.batch_alter_table('opportunity_tasks', schema=None) as batch_op:
        batch_op.drop_column('priority')
    for table in ('appointments', 'opportunity_tasks', 'opportunity_notes',
                  'number_thread_events', 'conversation_events'):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_column('ai_agent_id')
    for table in ('ai_alerts', 'ai_templates', 'ai_agent_threads', 'ai_suggestions',
                  'ai_run_steps', 'ai_runs', 'ai_knowledge_gaps', 'ai_kb_chunks',
                  'ai_kb_items', 'ai_knowledge_bases', 'ai_agent_versions', 'ai_agents',
                  'ai_folders', 'ai_connections', 'ai_settings'):
        op.drop_table(table)  # its indexes go with it
