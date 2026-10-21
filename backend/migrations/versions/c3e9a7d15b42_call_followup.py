"""call follow-up — a text, then an AI call, after the team's call went unanswered

Revision ID: c3e9a7d15b42
Revises: b8d4f1c27e60
Create Date: 2026-10-21

One CREATE TABLE (call_followups) and one nullable ADD COLUMN (agent_registry.config). No DROP,
no UPDATE. The follow-up is off unless CALL_FOLLOWUP_ENABLED is set and starts in Shadow on the
test customers only. DECISIONS.md, the 2026-10-21 call follow-up amendment.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'c3e9a7d15b42'
down_revision: str | Sequence[str] | None = 'b8d4f1c27e60'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('agent_registry', sa.Column(
        'config', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=True))
    op.create_table(
        'call_followups',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('trigger_key', sa.String(length=160), nullable=False),
        sa.Column('phone', sa.String(length=10), nullable=False),
        sa.Column('trigger_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('trigger_source', sa.String(length=20), nullable=True),
        sa.Column('staff', sa.String(length=120), nullable=True),
        sa.Column('customer_uid', sa.String(length=64), nullable=True),
        sa.Column('customer_name', sa.String(length=200), nullable=True),
        sa.Column('job_uid', sa.String(length=64), nullable=True),
        sa.Column('job_number', sa.String(length=40), nullable=True),
        sa.Column('state', sa.String(length=12), server_default='open', nullable=False),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('text_body', sa.Text(), nullable=True),
        sa.Column('text_state', sa.String(length=12), nullable=True),
        sa.Column('text_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('call_state', sa.String(length=12), nullable=True),
        sa.Column('call_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('call_ref', sa.String(length=64), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('trigger_key'),
    )
    op.create_index(op.f('ix_call_followups_phone'), 'call_followups', ['phone'], unique=False)
    op.create_index(op.f('ix_call_followups_state'), 'call_followups', ['state'], unique=False)
    op.create_index(op.f('ix_call_followups_created_at'), 'call_followups', ['created_at'],
                    unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_call_followups_created_at'), table_name='call_followups')
    op.drop_index(op.f('ix_call_followups_state'), table_name='call_followups')
    op.drop_index(op.f('ix_call_followups_phone'), table_name='call_followups')
    op.drop_table('call_followups')
    op.drop_column('agent_registry', 'config')
