"""add daily_financials.other_amount and financial_notes table

Revision ID: a1b2c3d4e5f6
Revises: f4b8d2a6c710
Create Date: 2026-07-27 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'f4b8d2a6c710'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('daily_financials', sa.Column('other_amount', sa.Numeric(16, 2), nullable=True))

    op.create_table(
        'financial_notes',
        sa.Column('id', sa.Integer(), primary_key=True, index=True),
        sa.Column('business_unit_id', sa.Integer(), sa.ForeignKey('business_units.id'), nullable=False, index=True),
        sa.Column('note_date', sa.Date(), nullable=False, index=True),
        sa.Column('note_text', sa.Text(), nullable=False),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('financial_notes')
    op.drop_column('daily_financials', 'other_amount')
