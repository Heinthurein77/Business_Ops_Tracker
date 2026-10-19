"""make legacy health toggle fields nullable

Revision ID: 81ec80d58ddf
Revises: 44995de89f9c
Create Date: 2026-07-21 17:14:16.688408

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '81ec80d58ddf'
down_revision: Union[str, Sequence[str], None] = '44995de89f9c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # SQLite cannot ALTER a column in place.  ``batch_alter_table`` recreates
    # the table on SQLite and uses native ALTER statements on PostgreSQL.
    with op.batch_alter_table('health_statuses') as batch_op:
        for column in ('physical_health', 'stress_level', 'energy_mood', 'sleep_quality'):
            batch_op.alter_column(
                column,
                existing_type=sa.VARCHAR(length=20),
                nullable=True,
            )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('health_statuses') as batch_op:
        for column in ('sleep_quality', 'energy_mood', 'stress_level', 'physical_health'):
            batch_op.alter_column(
                column,
                existing_type=sa.VARCHAR(length=20),
                nullable=False,
            )
