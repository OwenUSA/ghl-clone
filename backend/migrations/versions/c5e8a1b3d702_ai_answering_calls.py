"""ai_agents.answering_calls — answering the phone is its own switch (phase 2c)

Revision ID: c5e8a1b3d702
Revises: a9d2f4c6e813
Create Date: 2026-09-25

ADD COLUMN only, each with a server default or nullable, so every existing row reads as it
did: no agent answers calls until a person switches it on (`answering_calls` false — the same
stance as "created Off"), and an existing push reads as a push. No DROP, no UPDATE, no backfill.
DECISIONS.md, the 2026-09-25 phase-2c amendment.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c5e8a1b3d702'
down_revision: str | Sequence[str] | None = 'a9d2f4c6e813'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('ai_agents', sa.Column('answering_calls', sa.Boolean(),
                                         server_default=sa.false(), nullable=False))
    op.add_column('ai_voice_pushes', sa.Column('op', sa.String(length=20),
                                               server_default='push', nullable=False))
    op.add_column('ai_voice_pushes', sa.Column('owen_answering', sa.Boolean(), nullable=True))
    op.add_column('ai_voice_pushes', sa.Column('owen_active_version', sa.Integer(),
                                               nullable=True))


def downgrade() -> None:
    op.drop_column('ai_voice_pushes', 'owen_active_version')
    op.drop_column('ai_voice_pushes', 'owen_answering')
    op.drop_column('ai_voice_pushes', 'op')
    op.drop_column('ai_agents', 'answering_calls')
