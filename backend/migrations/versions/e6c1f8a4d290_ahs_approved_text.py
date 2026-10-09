"""ahs_approved_text — the "AHS authorized the repair" text

Revision ID: e6c1f8a4d290
Revises: d3a7b9e2f415
Create Date: 2026-10-09

Two ADD COLUMNs on reminder_settings (one with a server default). No DROP, no UPDATE, no
backfill. DECISIONS.md, the 2026-10-09 "AHS authorized the repair" amendment.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'e6c1f8a4d290'
down_revision: str | Sequence[str] | None = 'd3a7b9e2f415'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('reminder_settings', sa.Column('ahs_approved_mode', sa.String(length=10),
                                                 server_default='off', nullable=False))
    op.add_column('reminder_settings', sa.Column(
        'stage_watch_kinds',
        sa.JSON(none_as_null=True).with_variant(postgresql.JSONB(none_as_null=True),
                                                'postgresql'),
        nullable=True))


def downgrade() -> None:
    op.drop_column('reminder_settings', 'stage_watch_kinds')
    op.drop_column('reminder_settings', 'ahs_approved_mode')
