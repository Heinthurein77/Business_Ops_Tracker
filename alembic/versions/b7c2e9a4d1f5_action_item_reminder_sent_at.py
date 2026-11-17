"""add ActionItem.reminder_sent_at for due/overdue reminders

Revision ID: b7c2e9a4d1f5
Revises: a720b6b7c616
Create Date: 2026-11-17

Throttle column for the action-item due/overdue reminders
(run_remind_action_items in src/routers/ops.py), mirroring
LicenseDocument.expiry_notified_at: one reminder per item per 7 days.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7c2e9a4d1f5'
down_revision: Union[str, Sequence[str], None] = 'a720b6b7c616'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('action_items', sa.Column('reminder_sent_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('action_items', 'reminder_sent_at')
