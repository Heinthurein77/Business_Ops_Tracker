"""PM-6: sticky revenue-anomaly review state on daily_financials.

Revision ID: 9c4d2e7a1f08
Revises: 621509460777
Create Date: 2026-11-18

Adds revenue_anomaly_reviewed_at / revenue_anomaly_reviewed_by so a
resubmit cannot silently clear an unreviewed anomaly: the flag stays set
until a Chairman/HO reviewer explicitly acknowledges it (POST
/api/admin/business-unit/{bu_id}/anomaly/review). Both columns are
nullable, so the upgrade is a pure additive change and the downgrade
drops them.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '9c4d2e7a1f08'
down_revision: Union[str, Sequence[str], None] = '621509460777'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('daily_financials', sa.Column('revenue_anomaly_reviewed_at', sa.DateTime(), nullable=True))
    op.add_column('daily_financials', sa.Column('revenue_anomaly_reviewed_by', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_daily_financials_anomaly_reviewed_by', 'daily_financials', 'users',
                          ['revenue_anomaly_reviewed_by'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_daily_financials_anomaly_reviewed_by', 'daily_financials', type_='foreignkey')
    op.drop_column('daily_financials', 'revenue_anomaly_reviewed_by')
    op.drop_column('daily_financials', 'revenue_anomaly_reviewed_at')
