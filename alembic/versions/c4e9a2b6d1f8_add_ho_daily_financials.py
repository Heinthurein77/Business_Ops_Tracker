"""add ho_daily_financials

Revision ID: c4e9a2b6d1f8
Revises: b3d8f1a2c9e4
Create Date: 2026-08-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4e9a2b6d1f8'
down_revision: Union[str, Sequence[str], None] = 'b3d8f1a2c9e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('ho_daily_financials',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('report_date', sa.Date(), nullable=False),
        sa.Column('revenue', sa.Numeric(precision=16, scale=2), nullable=False),
        sa.Column('expenses', sa.Numeric(precision=16, scale=2), nullable=False),
        sa.Column('net_profit', sa.Numeric(precision=16, scale=2), nullable=False),
        sa.Column('cash_in_hand', sa.Numeric(precision=16, scale=2), nullable=False),
        sa.Column('cash_flow', sa.Numeric(precision=16, scale=2), nullable=True),
        sa.Column('receivable', sa.Numeric(precision=16, scale=2), nullable=True),
        sa.Column('payable', sa.Numeric(precision=16, scale=2), nullable=True),
        sa.Column('other_amount', sa.Numeric(precision=16, scale=2), nullable=True),
        sa.Column('other_notes', sa.Text(), nullable=True),
        sa.Column('submitted_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['submitted_by'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('report_date', name='uq_ho_financial_date'),
    )
    op.create_index(op.f('ix_ho_daily_financials_id'), 'ho_daily_financials', ['id'], unique=False)
    op.create_index(op.f('ix_ho_daily_financials_report_date'), 'ho_daily_financials', ['report_date'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_ho_daily_financials_report_date'), table_name='ho_daily_financials')
    op.drop_index(op.f('ix_ho_daily_financials_id'), table_name='ho_daily_financials')
    op.drop_table('ho_daily_financials')
