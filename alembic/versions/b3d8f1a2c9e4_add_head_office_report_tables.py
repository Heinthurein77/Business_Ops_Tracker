"""add head office report tables

Revision ID: b3d8f1a2c9e4
Revises: a9c1e3f5b7d2
Create Date: 2026-08-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3d8f1a2c9e4'
down_revision: Union[str, Sequence[str], None] = 'a9c1e3f5b7d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('ho_activity_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('report_date', sa.Date(), nullable=False),
        sa.Column('meetings_attended', sa.Text(), nullable=True),
        sa.Column('site_visits', sa.Text(), nullable=True),
        sa.Column('key_decisions', sa.Text(), nullable=True),
        sa.Column('next_day_schedule', sa.Text(), nullable=True),
        sa.Column('document_url', sa.String(length=500), nullable=True),
        sa.Column('gym_percent', sa.Integer(), nullable=True),
        sa.Column('swimming_percent', sa.Integer(), nullable=True),
        sa.Column('cycle_percent', sa.Integer(), nullable=True),
        sa.Column('other_notes', sa.Text(), nullable=True),
        sa.Column('submitted_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['submitted_by'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('report_date', name='uq_ho_activity_date'),
    )
    op.create_index(op.f('ix_ho_activity_logs_id'), 'ho_activity_logs', ['id'], unique=False)
    op.create_index(op.f('ix_ho_activity_logs_report_date'), 'ho_activity_logs', ['report_date'], unique=True)

    op.create_table('ho_health_statuses',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('report_date', sa.Date(), nullable=False),
        sa.Column('physical_health', sa.String(length=20), nullable=True),
        sa.Column('stress_level', sa.String(length=20), nullable=True),
        sa.Column('energy_mood', sa.String(length=20), nullable=True),
        sa.Column('sleep_quality', sa.String(length=20), nullable=True),
        sa.Column('daily_steps', sa.Integer(), nullable=True),
        sa.Column('heart_rate', sa.Integer(), nullable=True),
        sa.Column('sleep_hours', sa.Numeric(precision=4, scale=1), nullable=True),
        sa.Column('bp_systolic', sa.Integer(), nullable=True),
        sa.Column('bp_diastolic', sa.Integer(), nullable=True),
        sa.Column('other_notes', sa.Text(), nullable=True),
        sa.Column('submitted_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['submitted_by'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('report_date', name='uq_ho_health_date'),
    )
    op.create_index(op.f('ix_ho_health_statuses_id'), 'ho_health_statuses', ['id'], unique=False)
    op.create_index(op.f('ix_ho_health_statuses_report_date'), 'ho_health_statuses', ['report_date'], unique=True)

    op.create_table('ho_social_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('report_date', sa.Date(), nullable=False),
        sa.Column('event_flag', sa.Boolean(), nullable=False),
        sa.Column('meeting_flag', sa.Boolean(), nullable=False),
        sa.Column('golf_flag', sa.Boolean(), nullable=False),
        sa.Column('other_notes', sa.Text(), nullable=True),
        sa.Column('submitted_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['submitted_by'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('report_date', name='uq_ho_social_date'),
    )
    op.create_index(op.f('ix_ho_social_logs_id'), 'ho_social_logs', ['id'], unique=False)
    op.create_index(op.f('ix_ho_social_logs_report_date'), 'ho_social_logs', ['report_date'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_ho_social_logs_report_date'), table_name='ho_social_logs')
    op.drop_index(op.f('ix_ho_social_logs_id'), table_name='ho_social_logs')
    op.drop_table('ho_social_logs')
    op.drop_index(op.f('ix_ho_health_statuses_report_date'), table_name='ho_health_statuses')
    op.drop_index(op.f('ix_ho_health_statuses_id'), table_name='ho_health_statuses')
    op.drop_table('ho_health_statuses')
    op.drop_index(op.f('ix_ho_activity_logs_report_date'), table_name='ho_activity_logs')
    op.drop_index(op.f('ix_ho_activity_logs_id'), table_name='ho_activity_logs')
    op.drop_table('ho_activity_logs')
