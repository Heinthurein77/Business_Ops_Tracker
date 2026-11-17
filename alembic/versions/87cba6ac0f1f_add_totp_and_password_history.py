"""add TOTP columns to users + password_history table

Revision ID: 87cba6ac0f1f
Revises: b7c2e9a4d1f5
Create Date: 2026-11-17

M-01 (TOTP second factor): per-user encrypted TOTP secret, enabled flag
and enrollment timestamp on users. M-27 (password history): a
password_history table keeping the last 5 hashes per user so a password
cannot be reused.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '87cba6ac0f1f'
down_revision: Union[str, Sequence[str], None] = 'b7c2e9a4d1f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('totp_secret', sa.Text(), nullable=True))
    op.add_column('users', sa.Column('totp_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('users', sa.Column('totp_enrolled_at', sa.DateTime(), nullable=True))
    op.create_table(
        'password_history',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('hashed_password', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_password_history_user_id', 'password_history', ['user_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_password_history_user_id', table_name='password_history')
    op.drop_table('password_history')
    op.drop_column('users', 'totp_enrolled_at')
    op.drop_column('users', 'totp_enabled')
    op.drop_column('users', 'totp_secret')
