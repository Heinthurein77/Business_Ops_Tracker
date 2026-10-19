"""add monthly_targets table

Revision ID: c3f1a9d7e2b4
Revises: 81ec80d58ddf
Create Date: 2026-07-24 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3f1a9d7e2b4'
down_revision: Union[str, Sequence[str], None] = '81ec80d58ddf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'monthly_targets',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('business_unit_id', sa.Integer(), nullable=False),
        sa.Column('target_month', sa.Date(), nullable=False),
        sa.Column('monthly_revenue_target', sa.Numeric(16, 2), nullable=False),
        sa.Column('monthly_cash_flow_target', sa.Numeric(16, 2), nullable=False),
        sa.Column('monthly_max_receivable', sa.Numeric(16, 2), nullable=False),
        sa.Column('monthly_max_payable', sa.Numeric(16, 2), nullable=False),
        sa.Column('set_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['business_unit_id'], ['business_units.id']),
        sa.ForeignKeyConstraint(['set_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('business_unit_id', 'target_month', name='uq_target_bu_month'),
    )
    op.create_index(op.f('ix_monthly_targets_id'), 'monthly_targets', ['id'], unique=False)
    op.create_index(op.f('ix_monthly_targets_business_unit_id'), 'monthly_targets', ['business_unit_id'], unique=False)
    op.create_index(op.f('ix_monthly_targets_target_month'), 'monthly_targets', ['target_month'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_monthly_targets_target_month'), table_name='monthly_targets')
    op.drop_index(op.f('ix_monthly_targets_business_unit_id'), table_name='monthly_targets')
    op.drop_index(op.f('ix_monthly_targets_id'), table_name='monthly_targets')
    op.drop_table('monthly_targets')
