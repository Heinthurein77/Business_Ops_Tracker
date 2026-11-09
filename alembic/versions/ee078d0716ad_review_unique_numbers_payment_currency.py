"""BOT review batch: unique document numbers, payment currency, stock CHECK

Revision ID: ee078d0716ad
Revises: c9ec96d6b1e5
Create Date: 2026-11-09

M-D5 (unique-constraints part): every business-facing document number was
``count + 1`` with no unique constraint, so two concurrent creates could
store the same number (journal entry_no, PO/invoice/quotation/sales-return/
purchase-return numbers, stock-transfer number). Each number now carries a
per-business-unit unique constraint. The count+1 generation itself is
unchanged; a collision now fails loudly at the database instead of storing
a silent duplicate, and the create endpoints translate it to a 409.
Pre-flight: if duplicates already exist the upgrade aborts naming them —
renumber the duplicates, then re-run.

M-C5: CHECK ``current_stock >= 0`` on inventory_items. Postgres-only, like
every other CHECK in this codebase (see _pg_check in src/models.py):
SQLite cannot ALTER constraints without rebuilding the table, and local
development relies on the API validation. The pre-flight abort for
negative stock lives in the same Postgres branch.

M-D7: invoice_payments.currency (String(3), server default 'MMK') so a
payment's currency is recorded on the row instead of being assumed to be
the invoice's currency. Existing rows backfill to 'MMK' via the default.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ee078d0716ad'
down_revision: Union[str, Sequence[str], None] = 'c9ec96d6b1e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (table, business-unit column, number column, constraint name). The number
# formats all embed the business unit (e.g. PO-<bu>-0001, JV-YYYY-MM-NNN
# per BU), so the constraint is scoped per BU, matching generation.
UNIQUE_NUMBERS = [
    ("journal_entries", "business_unit_id", "entry_no",
     "uq_journal_entries_bu_entry_no"),
    ("purchase_orders", "business_unit_id", "po_no",
     "uq_purchase_orders_bu_po_no"),
    ("sales_invoices", "business_unit_id", "invoice_no",
     "uq_sales_invoices_bu_invoice_no"),
    ("quotations", "business_unit_id", "quote_no",
     "uq_quotations_bu_quote_no"),
    ("sales_returns", "business_unit_id", "return_no",
     "uq_sales_returns_bu_return_no"),
    ("purchase_returns", "business_unit_id", "return_no",
     "uq_purchase_returns_bu_return_no"),
    ("stock_transfers", "from_business_unit_id", "transfer_no",
     "uq_stock_transfers_from_bu_transfer_no"),
]

STOCK_CHECK_NAME = "ck_inventory_items_stock_non_negative"
STOCK_CHECK_SQL = "current_stock >= 0"


def _is_pg() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _count(sql: str) -> int:
    return int(op.get_bind().execute(sa.text(sql)).scalar() or 0)


def upgrade() -> None:
    """Upgrade schema."""
    for table, bu_col, num_col, name in UNIQUE_NUMBERS:
        n = _count(
            f"SELECT count(*) FROM (SELECT {bu_col}, {num_col} FROM {table} "
            f"GROUP BY {bu_col}, {num_col} HAVING count(*) > 1) d"
        )
        if n:
            rows = op.get_bind().execute(sa.text(
                f"SELECT {bu_col}, {num_col}, count(*) FROM {table} "
                f"GROUP BY {bu_col}, {num_col} HAVING count(*) > 1"
            )).fetchall()
            sample = "; ".join(str(tuple(r)) for r in rows[:10])
            raise RuntimeError(
                f"{table} has duplicate {num_col} values ({n} group(s), e.g. {sample}). "
                "Renumber the duplicates, then re-run the upgrade."
            )
        with op.batch_alter_table(table) as batch_op:
            batch_op.create_unique_constraint(name, [bu_col, num_col])

    with op.batch_alter_table("invoice_payments") as batch_op:
        batch_op.add_column(
            sa.Column("currency", sa.String(length=3), nullable=False,
                      server_default="MMK")
        )

    if _is_pg():
        n = _count("SELECT count(*) FROM inventory_items WHERE current_stock < 0")
        if n:
            raise RuntimeError(
                f"inventory_items has {n} row(s) with current_stock < 0. "
                "Correct the stock figures, then re-run the upgrade."
            )
        op.create_check_constraint(STOCK_CHECK_NAME, "inventory_items", STOCK_CHECK_SQL)


def downgrade() -> None:
    """Downgrade schema."""
    if _is_pg():
        op.drop_constraint(STOCK_CHECK_NAME, "inventory_items", type_="check")
    with op.batch_alter_table("invoice_payments") as batch_op:
        batch_op.drop_column("currency")
    for table, bu_col, num_col, name in UNIQUE_NUMBERS:
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_constraint(name, type_="unique")
