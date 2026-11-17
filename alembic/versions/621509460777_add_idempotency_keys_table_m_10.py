"""add idempotency_keys table for Idempotency-Key'd tracker creates (m-10)

Revision ID: 621509460777
Revises: b7c2e9a4d1f5
Create Date: 2026-11-17

Stores (user_id, endpoint, key) -> first response status/body so a
retried create_action_item / create_document request replays the
original response instead of creating a second row. The unique
constraint on (user_id, endpoint, idempotency_key) is the race guard
for two concurrent replays.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '621509460777'
down_revision: Union[str, Sequence[str], None] = 'b7c2e9a4d1f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'idempotency_keys',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('endpoint', sa.String(length=200), nullable=False),
        sa.Column('idempotency_key', sa.String(length=128), nullable=False),
        sa.Column('response_status', sa.Integer(), nullable=False),
        sa.Column('response_body', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'endpoint', 'idempotency_key',
                            name='uq_idempotency_user_endpoint_key'),
    )
    op.create_index('ix_idempotency_keys_id', 'idempotency_keys', ['id'], unique=False)
    op.create_index('ix_idempotency_keys_user_id', 'idempotency_keys', ['user_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_idempotency_keys_user_id', table_name='idempotency_keys')
    op.drop_index('ix_idempotency_keys_id', table_name='idempotency_keys')
    op.drop_table('idempotency_keys')
