"""add ceo_profiles.business_details

Revision ID: f4b8d2a6c710
Revises: e7a2c9f1d5b3
Create Date: 2026-07-25 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f4b8d2a6c710'
down_revision: Union[str, Sequence[str], None] = 'e7a2c9f1d5b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('ceo_profiles', sa.Column('business_details', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('ceo_profiles', 'business_details')
