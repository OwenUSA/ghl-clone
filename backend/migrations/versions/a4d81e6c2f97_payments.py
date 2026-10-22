"""payments & collections: agreements, milestones, proofs, suggestions, changes, chat

Revision ID: a4d81e6c2f97
Revises: f2c7a91d3b58
Create Date: 2026-10-21

Six CREATE TABLEs. No DROP, no UPDATE. Off unless PAYMENTS_ENABLED (the pass); the page and
the chat work for the office as soon as it is deployed. DECISIONS.md, the 2026-10-21 payments
amendment.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'a4d81e6c2f97'
down_revision: str | Sequence[str] | None = 'f2c7a91d3b58'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

J = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        'payment_agreements',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('contact_id', sa.Integer(), nullable=True),
        sa.Column('opportunity_id', sa.Integer(), nullable=True),
        sa.Column('job_uid', sa.String(length=64), nullable=True),
        sa.Column('job_number', sa.String(length=40), nullable=True),
        sa.Column('board', sa.String(length=120), nullable=True),
        sa.Column('customer_name', sa.String(length=200), nullable=True),
        sa.Column('phone', sa.String(length=10), nullable=True),
        sa.Column('quoted_cents', sa.Integer(), nullable=True),
        sa.Column('agreed_cents', sa.Integer(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('terms', sa.String(length=300), nullable=True),
        sa.Column('state', sa.String(length=12), server_default='quoted', nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', TS, server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', TS, nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    for col in ('contact_id', 'job_uid', 'phone', 'state'):
        op.create_index(op.f('ix_payment_agreements_%s' % col), 'payment_agreements', [col])
    op.create_table(
        'payment_milestones',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('agreement_id', sa.Integer(), nullable=False),
        sa.Column('position', sa.Integer(), server_default='0', nullable=False),
        sa.Column('label', sa.String(length=120), nullable=False),
        sa.Column('amount_cents', sa.Integer(), nullable=False),
        sa.Column('due_rule', sa.String(length=20), server_default='on_date', nullable=False),
        sa.Column('due_on', sa.Date(), nullable=True),
        sa.Column('state', sa.String(length=10), server_default='pending', nullable=False),
        sa.Column('paid_cents', sa.Integer(), nullable=True),
        sa.Column('paid_on', sa.Date(), nullable=True),
        sa.Column('method', sa.String(length=60), nullable=True),
        sa.Column('marked_by_id', sa.Integer(), nullable=True),
        sa.Column('source', sa.String(length=12), nullable=True),
        sa.Column('reminders_sent', sa.Integer(), server_default='0', nullable=False),
        sa.Column('last_reminded_at', TS, nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_payment_milestones_agreement_id'), 'payment_milestones',
                    ['agreement_id'])
    op.create_table(
        'payment_proofs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('agreement_id', sa.Integer(), nullable=False),
        sa.Column('milestone_id', sa.Integer(), nullable=True),
        sa.Column('message_attachment_id', sa.Integer(), nullable=True),
        sa.Column('sha', sa.String(length=64), nullable=True),
        sa.Column('content_type', sa.String(length=40), nullable=True),
        sa.Column('ai_reading', J, nullable=True),
        sa.Column('state', sa.String(length=12), server_default='to_review', nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', TS, server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('message_attachment_id'),
    )
    op.create_index(op.f('ix_payment_proofs_agreement_id'), 'payment_proofs', ['agreement_id'])
    op.create_table(
        'payment_suggestions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(length=160), nullable=False),
        sa.Column('agreement_id', sa.Integer(), nullable=False),
        sa.Column('milestone_id', sa.Integer(), nullable=True),
        sa.Column('source', sa.String(length=12), nullable=False),
        sa.Column('amount_cents', sa.Integer(), nullable=True),
        sa.Column('paid_on', sa.Date(), nullable=True),
        sa.Column('evidence', sa.Text(), nullable=True),
        sa.Column('proof_id', sa.Integer(), nullable=True),
        sa.Column('state', sa.String(length=10), server_default='open', nullable=False),
        sa.Column('created_at', TS, server_default=sa.func.now(), nullable=False),
        sa.Column('decided_by_id', sa.Integer(), nullable=True),
        sa.Column('decided_at', TS, nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('key'),
    )
    op.create_index(op.f('ix_payment_suggestions_agreement_id'), 'payment_suggestions',
                    ['agreement_id'])
    op.create_index(op.f('ix_payment_suggestions_state'), 'payment_suggestions', ['state'])
    op.create_table(
        'payment_changes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('agreement_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('via', sa.String(length=12), nullable=False),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('before', J, nullable=True),
        sa.Column('after', J, nullable=True),
        sa.Column('created_at', TS, server_default=sa.func.now(), nullable=False),
        sa.Column('undone_at', TS, nullable=True),
        sa.Column('undone_by_id', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_payment_changes_agreement_id'), 'payment_changes',
                    ['agreement_id'])
    op.create_index(op.f('ix_payment_changes_created_at'), 'payment_changes', ['created_at'])
    op.create_table(
        'payment_chat_messages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('role', sa.String(length=10), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('steps', J, nullable=True),
        sa.Column('change_ids', J, nullable=True),
        sa.Column('error', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('created_at', TS, server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_payment_chat_messages_user_id'), 'payment_chat_messages',
                    ['user_id'])
    op.create_index(op.f('ix_payment_chat_messages_created_at'), 'payment_chat_messages',
                    ['created_at'])


def downgrade() -> None:
    for t in ('payment_chat_messages', 'payment_changes', 'payment_suggestions',
              'payment_proofs', 'payment_milestones', 'payment_agreements'):
        op.drop_table(t)
