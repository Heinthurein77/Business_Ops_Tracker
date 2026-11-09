"""D-14(b): revenue anomaly flag on daily_financials

Revision ID: f58eca28a187
Revises: ee078d0716ad
Create Date: 2026-11-09

Adds ``daily_financials.revenue_anomaly`` (Boolean, default false). At CEO
daily submit, the day's revenue is compared with the BU's trailing 14-day
average revenue; a deviation of more than 50% sets the flag so HO can
review the auto-posted figure, and HO users are notified in-app. The flag
is stored on the row (rather than computed at HO-review read time) so it
survives resubmission edits and is visible to every review view that reads
the row, including the admin business-unit detail view. Existing rows
backfill to false via the server default.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f58eca28a187'
down_revision: Union[str, Sequence[str], None] = 'ee078d0716ad'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("daily_financials", recreate="never") as batch_op:
        batch_op.add_column(
            sa.Column("revenue_anomaly", sa.Boolean(), server_default=sa.false(), nullable=False)
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("daily_financials", recreate="never") as batch_op:
        batch_op.drop_column("revenue_anomaly")
