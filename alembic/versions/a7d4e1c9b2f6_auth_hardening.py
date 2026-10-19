"""auth hardening: expiring temp passwords, throttle indexes, one pending reset per user

Revision ID: a7d4e1c9b2f6
Revises: f9c2b6a1d4e8
Create Date: 2026-09-24 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a7d4e1c9b2f6'
down_revision: Union[str, Sequence[str], None] = 'f9c2b6a1d4e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('temp_password_expires_at', sa.DateTime(), nullable=True))

    # The login / forgot-password throttles count recent audit rows by
    # (action, username) and (action, ip) — without these two indexes each
    # login attempt would scan the whole, ever-growing audit table.
    op.create_index('ix_audit_action_username_ts', 'audit_logs', ['action', 'username', 'timestamp'])
    op.create_index('ix_audit_action_ip_ts', 'audit_logs', ['action', 'ip_address', 'timestamp'])

    # A user may have at most one open reset request. Before the approval
    # workflow every request was auto-approved, so duplicates among Pending
    # rows should not exist — but if any do, keep only the newest per user
    # (the older ones are stale) rather than letting the index creation fail
    # halfway through a live upgrade.
    op.execute(
        """
        UPDATE password_reset_requests
        SET status = 'Rejected'
        WHERE status = 'Pending'
          AND id NOT IN (
              SELECT MAX(id) FROM password_reset_requests WHERE status = 'Pending' GROUP BY user_id
          )
        """
    )
    op.create_index(
        'uq_pwreset_one_pending_per_user',
        'password_reset_requests',
        ['user_id'],
        unique=True,
        postgresql_where=sa.text("status = 'Pending'"),
        sqlite_where=sa.text("status = 'Pending'"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('uq_pwreset_one_pending_per_user', table_name='password_reset_requests')
    op.drop_index('ix_audit_action_ip_ts', table_name='audit_logs')
    op.drop_index('ix_audit_action_username_ts', table_name='audit_logs')
    op.drop_column('users', 'temp_password_expires_at')
