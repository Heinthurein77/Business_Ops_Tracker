"""add other_notes to activity_logs, health_statuses, social_logs

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-27 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('activity_logs', sa.Column('other_notes', sa.Text(), nullable=True))
    op.add_column('health_statuses', sa.Column('other_notes', sa.Text(), nullable=True))
    op.add_column('social_logs', sa.Column('other_notes', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('social_logs', 'other_notes')
    op.drop_column('health_statuses', 'other_notes')
    op.drop_column('activity_logs', 'other_notes')
