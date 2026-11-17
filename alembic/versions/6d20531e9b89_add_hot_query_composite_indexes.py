"""add composite indexes on hot query columns (m-12)

Revision ID: 6d20531e9b89
Revises: b7c2e9a4d1f5
Create Date: 2026-11-17

- notifications (user_id, is_read, created_at): the bell query
  (list_notifications) filters user_id + is_read ordered by created_at
  DESC every 60 s per open tab.
- action_items (status, due_date): the reminder job filters
  status IN (...) AND due_date <= today.
- audit_logs (action, timestamp): report_logs filters action IN (...)
  ordered by timestamp DESC.
- users.business_unit_id: per-BU lookups had no index at all.
"""
from typing import Sequence, Union

from alembic import op


revision: str = '6d20531e9b89'
down_revision: Union[str, Sequence[str], None] = '87cba6ac0f1f'  # re-chained at merge: was b7c2e9a4d1f5 on its branch
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        'ix_notifications_user_read_created', 'notifications',
        ['user_id', 'is_read', 'created_at'])
    op.create_index(
        'ix_action_items_status_due', 'action_items',
        ['status', 'due_date'])
    op.create_index(
        'ix_audit_action_ts', 'audit_logs',
        ['action', 'timestamp'])
    op.create_index('ix_users_business_unit_id', 'users', ['business_unit_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_users_business_unit_id', table_name='users')
    op.drop_index('ix_audit_action_ts', table_name='audit_logs')
    op.drop_index('ix_action_items_status_due', table_name='action_items')
    op.drop_index('ix_notifications_user_read_created', table_name='notifications')
