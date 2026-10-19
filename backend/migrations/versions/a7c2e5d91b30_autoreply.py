"""autoreply — the polite text to an unanswered Zuper conversation

Revision ID: a7c2e5d91b30
Revises: e6c1f8a4d290
Create Date: 2026-10-19

One CREATE TABLE (autoreply_log), ADD COLUMNs on dispatch_settings (every non-nullable one with a
server default) and one nullable ADD COLUMN on dispatch_calls. No DROP, no UPDATE, no backfill.
The auto-reply starts OFF and in test mode. DECISIONS.md, the 2026-10-19 auto-reply amendment.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a7c2e5d91b30'
down_revision: str | Sequence[str] | None = 'e6c1f8a4d290'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'autoreply_log',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('conversation_uid', sa.String(length=64), nullable=False),
        sa.Column('trigger_uid', sa.String(length=64), nullable=False),
        sa.Column('trigger_kind', sa.String(length=20), nullable=False),
        sa.Column('trigger_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('customer_uid', sa.String(length=64), nullable=True),
        sa.Column('customer_name', sa.String(length=200), nullable=True),
        sa.Column('phone', sa.String(length=10), nullable=True),
        sa.Column('job_uid', sa.String(length=64), nullable=True),
        sa.Column('job_number', sa.String(length=40), nullable=True),
        sa.Column('state', sa.String(length=12), nullable=False),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('reply', sa.Text(), nullable=True),
        sa.Column('after_hours', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('upset', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('needs_person', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('written_by', sa.String(length=12), nullable=True),
        sa.Column('model', sa.String(length=120), nullable=True),
        sa.Column('input_tokens', sa.Integer(), server_default='0', nullable=False),
        sa.Column('output_tokens', sa.Integer(), server_default='0', nullable=False),
        sa.Column('message_uid', sa.String(length=64), nullable=True),
        sa.Column('note_state', sa.String(length=12), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('conversation_uid', 'trigger_uid', name='uq_autoreply_stretch'),
    )
    op.create_index(op.f('ix_autoreply_log_conversation_uid'), 'autoreply_log',
                    ['conversation_uid'], unique=False)
    op.create_index(op.f('ix_autoreply_log_customer_uid'), 'autoreply_log', ['customer_uid'],
                    unique=False)
    op.create_index(op.f('ix_autoreply_log_phone'), 'autoreply_log', ['phone'], unique=False)
    op.create_index(op.f('ix_autoreply_log_created_at'), 'autoreply_log', ['created_at'],
                    unique=False)
    op.create_index(op.f('ix_autoreply_log_sent_at'), 'autoreply_log', ['sent_at'],
                    unique=False)

    op.add_column('dispatch_settings', sa.Column('autoreply_enabled', sa.Boolean(),
                                                 server_default=sa.false(), nullable=False))
    op.add_column('dispatch_settings', sa.Column('autoreply_enabled_at',
                                                 sa.DateTime(timezone=True), nullable=True))
    op.add_column('dispatch_settings', sa.Column('autoreply_test_only', sa.Boolean(),
                                                 server_default=sa.true(), nullable=False))
    op.add_column('dispatch_settings', sa.Column('autoreply_daily_cap', sa.Integer(),
                                                 server_default='30', nullable=False))
    op.add_column('dispatch_settings', sa.Column('autoreply_connection_id', sa.Integer(),
                                                 nullable=True))
    op.add_column('dispatch_settings', sa.Column('autoreply_model', sa.String(length=120),
                                                 nullable=True))
    op.add_column('dispatch_settings', sa.Column('autoreply_transcribe_connection_id',
                                                 sa.Integer(), nullable=True))
    op.add_column('dispatch_settings', sa.Column('autoreply_last_run_at',
                                                 sa.DateTime(timezone=True), nullable=True))
    op.add_column('dispatch_settings', sa.Column('autoreply_last_error', sa.Text(),
                                                 nullable=True))
    op.add_column('dispatch_calls', sa.Column('transcript', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('dispatch_calls', 'transcript')
    for col in ('autoreply_last_error', 'autoreply_last_run_at',
                'autoreply_transcribe_connection_id', 'autoreply_model',
                'autoreply_connection_id', 'autoreply_daily_cap', 'autoreply_test_only',
                'autoreply_enabled_at', 'autoreply_enabled'):
        op.drop_column('dispatch_settings', col)
    for ix in ('sent_at', 'created_at', 'phone', 'customer_uid', 'conversation_uid'):
        op.drop_index(op.f('ix_autoreply_log_%s' % ix), table_name='autoreply_log')
    op.drop_table('autoreply_log')
