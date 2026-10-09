"""zuper_reminders — appointment reminder texts from Zuper's job status

Revision ID: c8d2f4a6b1e9
Revises: b4e1d7a2c9f6
Create Date: 2026-10-08

Four NEW tables and one nullable column. No DROP, no UPDATE, no backfill. DECISIONS.md, the
2026-10-08 amendment "Appointment reminder texts from Zuper" (app/reminders).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'c8d2f4a6b1e9'
down_revision: str | Sequence[str] | None = 'b4e1d7a2c9f6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json():
    return sa.JSON(none_as_null=True).with_variant(
        postgresql.JSONB(none_as_null=True), 'postgresql')


def upgrade() -> None:
    op.add_column('dispatch_jobs', sa.Column('mobile', sa.String(length=10), nullable=True))
    op.create_table(
        'reminder_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('mode', sa.String(length=10), server_default='off', nullable=False),
        sa.Column('test_numbers', _json(), nullable=True),
        sa.Column('templates', _json(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_by', sa.Integer(), nullable=True),
        sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_success_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('last_error_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_counts', _json(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'appointment_reminders',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(length=200), nullable=False),
        sa.Column('job_uid', sa.String(length=64), nullable=False),
        sa.Column('job_number', sa.String(length=40), nullable=True),
        sa.Column('board', sa.String(length=120), nullable=True),
        sa.Column('status', sa.String(length=120), nullable=True),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('visit_start', sa.DateTime(timezone=True), nullable=False),
        sa.Column('phone', sa.String(length=10), nullable=True),
        sa.Column('language', sa.String(length=2), nullable=True),
        sa.Column('state', sa.String(length=20), nullable=False),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('body', sa.Text(), nullable=True),
        sa.Column('contact_id', sa.Integer(), nullable=True),
        sa.Column('number_thread_id', sa.Integer(), nullable=True),
        sa.Column('event_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_appointment_reminders_key', 'appointment_reminders', ['key'],
                    unique=True)
    op.create_index('ix_appointment_reminders_job_uid', 'appointment_reminders', ['job_uid'])
    op.create_index('ix_appointment_reminders_state', 'appointment_reminders', ['state'])
    op.create_index('ix_appointment_reminders_created_at', 'appointment_reminders',
                    ['created_at'])
    op.create_table(
        'customer_languages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('phone_key', sa.String(length=10), nullable=False),
        sa.Column('language', sa.String(length=2), server_default='en', nullable=False),
        sa.Column('source', sa.String(length=10), server_default='auto', nullable=False),
        sa.Column('evidence', _json(), nullable=True),
        sa.Column('set_by', sa.Integer(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_customer_languages_phone_key', 'customer_languages', ['phone_key'],
                    unique=True)
    op.create_table(
        'reminder_switch_log',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('at', sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('field', sa.String(length=40), nullable=False),
        sa.Column('old_value', sa.Text(), nullable=True),
        sa.Column('new_value', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_reminder_switch_log_at', 'reminder_switch_log', ['at'])


def downgrade() -> None:
    op.drop_index('ix_reminder_switch_log_at', table_name='reminder_switch_log')
    op.drop_table('reminder_switch_log')
    op.drop_index('ix_customer_languages_phone_key', table_name='customer_languages')
    op.drop_table('customer_languages')
    op.drop_index('ix_appointment_reminders_created_at', table_name='appointment_reminders')
    op.drop_index('ix_appointment_reminders_state', table_name='appointment_reminders')
    op.drop_index('ix_appointment_reminders_job_uid', table_name='appointment_reminders')
    op.drop_index('ix_appointment_reminders_key', table_name='appointment_reminders')
    op.drop_table('appointment_reminders')
    op.drop_table('reminder_settings')
    op.drop_column('dispatch_jobs', 'mobile')
