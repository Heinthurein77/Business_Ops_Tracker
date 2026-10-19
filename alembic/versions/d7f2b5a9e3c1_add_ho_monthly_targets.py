"""add ho_monthly_targets

Revision ID: d7f2b5a9e3c1
Revises: c4e9a2b6d1f8
Create Date: 2026-08-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd7f2b5a9e3c1'
down_revision: Union[str, Sequence[str], None] = 'c4e9a2b6d1f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('ho_monthly_targets',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('target_month', sa.Date(), nullable=False),
        sa.Column('monthly_revenue_target', sa.Numeric(precision=16, scale=2), nullable=False),
        sa.Column('monthly_cash_flow_target', sa.Numeric(precision=16, scale=2), nullable=False),
        sa.Column('monthly_max_receivable', sa.Numeric(precision=16, scale=2), nullable=False),
        sa.Column('monthly_max_payable', sa.Numeric(precision=16, scale=2), nullable=False),
        sa.Column('set_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['set_by'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('target_month', name='uq_ho_target_month'),
    )
    op.create_index(op.f('ix_ho_monthly_targets_id'), 'ho_monthly_targets', ['id'], unique=False)
    op.create_index(op.f('ix_ho_monthly_targets_target_month'), 'ho_monthly_targets', ['target_month'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_ho_monthly_targets_target_month'), table_name='ho_monthly_targets')
    op.drop_index(op.f('ix_ho_monthly_targets_id'), table_name='ho_monthly_targets')
    op.drop_table('ho_monthly_targets')
