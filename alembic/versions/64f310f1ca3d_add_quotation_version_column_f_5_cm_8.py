"""F-5: CM-8 optimistic locking for quotations.

Revision ID: 64f310f1ca3d
Revises: 7f3a9c1e5b2d
Create Date: 2026-11-16

Adds the integer ``version`` column (default 0) to ``quotations``,
mirroring migration 7f3a9c1e5b2d which added it to the other six
versioned models (purchase_orders, sales_invoices, inventory_items,
customers, payroll_runs, expense_requests). ``update_quotation``
checks it via ``_check_version`` and bumps it on every edit, so two
concurrent edits to a draft quotation 409 instead of one silently
discarding the other's work.

Backfill is safe: ``server_default="0"`` gives every pre-existing
quotation version 0, matching the model's ``default=0`` for new rows.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '64f310f1ca3d'
down_revision: Union[str, Sequence[str], None] = '7f3a9c1e5b2d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("quotations", recreate="never") as batch_op:
        batch_op.add_column(
            sa.Column("version", sa.Integer(), server_default="0", nullable=False)
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("quotations", recreate="never") as batch_op:
        batch_op.drop_column("version")
