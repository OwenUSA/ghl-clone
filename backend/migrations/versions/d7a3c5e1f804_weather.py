"""weather agent: NWS points, grid forecasts, official alerts

Revision ID: d7a3c5e1f804
Revises: c6f1a8e3d952
Create Date: 2026-10-22

Three CREATE TABLEs. No DROP, no UPDATE. The agent is off unless WEATHER_ENABLED.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'd7a3c5e1f804'
down_revision: str | Sequence[str] | None = 'c6f1a8e3d952'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

J = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        'weather_points',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('point_key', sa.String(length=40), nullable=False),
        sa.Column('lat', sa.Float(), nullable=False),
        sa.Column('lng', sa.Float(), nullable=False),
        sa.Column('grid_url', sa.String(length=300), nullable=True),
        sa.Column('zones', J, nullable=True),
        sa.Column('error', sa.String(length=300), nullable=True),
        sa.Column('created_at', TS, server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('point_key'),
    )
    op.create_table(
        'weather_grids',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('grid_url', sa.String(length=300), nullable=False),
        sa.Column('hours', J, nullable=True),
        sa.Column('fetched_at', TS, nullable=True),
        sa.Column('nws_updated_at', TS, nullable=True),
        sa.Column('error', sa.String(length=300), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('grid_url'),
    )
    op.create_table(
        'weather_alerts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('alert_id', sa.String(length=300), nullable=False),
        sa.Column('event', sa.String(length=120), nullable=False),
        sa.Column('severity', sa.String(length=30), nullable=True),
        sa.Column('headline', sa.Text(), nullable=True),
        sa.Column('area', sa.Text(), nullable=True),
        sa.Column('zones', J, nullable=True),
        sa.Column('onset', TS, nullable=True),
        sa.Column('ends', TS, nullable=True),
        sa.Column('first_seen', TS, server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('alert_id'),
    )


def downgrade() -> None:
    op.drop_table('weather_alerts')
    op.drop_table('weather_grids')
    op.drop_table('weather_points')
