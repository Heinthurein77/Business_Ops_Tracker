"""Phase 4 ERP removal: drop 48 ERP tables + Accountant role

Revision ID: a720b6b7c616
Revises: f452a085537d
Create Date: 2026-11-17 00:00:00.000000

Deletes every ERP table from the tracker database and removes the
Accountant role (plan BOT_ERP_Removal_Plan_2026-11-17.md, phase 4,
decisions D1 / D3 / D16 / D19).

Dropped tables (48 — the plan's section 2.3 enumerates 46 names, not
45 as its headline says, plus employees + leave_requests per D1):
  line/child tables first, then headers, accounts last, so no FK is
  ever left dangling — see DROP_TABLES. No kept table has an FK into
  the dropped set (verified: kept tables reference only
  business_units and users).
Also dropped: the business_units.nature column (D19) and the
'Accountant' value from the users role checks (D3).

Data step: the upgrade FAILS LOUDLY if any ACTIVE users row still has
role='Accountant' — such a row must be reassigned (to CEO/HO/Chairman)
or deactivated first (the dev DB's leftover e2e account was deactivated
by hand before this migration was written). It never silently strands
a live Accountant. A row that is already deactivated (is_active = 0)
counts as dispositioned and does not block the upgrade — that is the
disposition the plan allows (D3 / section 4.1 step 5). Note the Postgres
caveat: creating the new ck_users_role would still reject even a
deactivated Accountant row, so on a Postgres database such a row must
be reassigned or deleted outright; the CHECK creation would fail loudly
inside the same transaction (nothing half-applied) if one were present.
No production database has one — go-live never happened.

Constraint replacement is Postgres-only, following the precedent of
c8e2a4b6d1f3 / bc3f5347dc23: the CHECK constraints were created by
Postgres-only migrations and by _pg_check in src/models.py, so they
never existed on SQLite (local dev / tests) and there is nothing to
drop there. The nature-column drop uses batch mode so it works on
both dialects.

DOWNGRADE IS ONE-WAY (deliberate — plan section 4.2). downgrade()
raises instead of re-creating the dropped tables: the data would be
gone anyway, so re-created tables would be empty shells that lie
about the schema's history. Restore path is the pre-removal backup,
NOT a downgrade. NOTE — the backup at
/tmp/local_dev.db.pre-erp-removal.2026-11-17 is the SQLite DEV
database file, not a pg_dump: there is no production database to
restore (go-live never happened, no Neon string was ever supplied),
so for any Postgres deployment the equivalent pre-removal pg_dump
must be taken before this migration runs there.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a720b6b7c616'
down_revision: Union[str, Sequence[str], None] = 'f452a085537d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# FK-safe drop order: every table appears before every table it has an
# FK to (within the dropped set). Children/lines first, headers next,
# accounts last (journal_lines and bank_accounts both reference it).
DROP_TABLES = [
    # Line / child tables
    "journal_lines",
    "sales_return_lines",
    "purchase_return_lines",
    "stock_transfer_lines",
    "stock_movements",
    "stock_batches",
    "item_serials",
    "pos_sale_lines",
    "quotation_lines",
    "sales_invoice_lines",
    "purchase_order_lines",
    "bom_components",
    "invoice_payments",
    "installments",
    "sales_returns",
    "purchase_returns",
    "lease_contracts",
    "property_units",
    "quotations",
    "shipments",
    "pos_sales",
    "sales_invoices",
    "purchase_orders",
    "production_orders",
    "manufacturing_boms",
    "staff_advances",
    "payroll_lines",
    "leave_requests",
    "attendance_records",
    "payroll_runs",
    "employees",
    "expense_requests",
    "bank_transactions",
    "bank_accounts",
    "stock_transfers",
    "journal_entries",
    # Standalone / header tables (FKs only to kept tables or each other,
    # already dropped above)
    "intercompany_transactions",
    "budgets",
    "exchange_rates",
    "posting_rules",
    "fiscal_periods",
    "fixed_assets",
    "projects",
    "customers",
    "inventory_items",
    "suppliers",
    "accounts",
    "idempotency_keys",
]

NEW_BU_RULE = (
    "(role = 'CEO' AND business_unit_id IS NOT NULL) OR "
    "(role IN ('Chairman', 'HO') AND business_unit_id IS NULL)"
)
NEW_ROLE_RULE = "role IN ('Chairman', 'HO', 'CEO')"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    # --- data step: refuse to strand a live Accountant -------------------
    # Deactivated rows (is_active = 0) count as dispositioned (plan D3);
    # only a row that could still sign in blocks the upgrade. The check
    # is done in Python because the is_active literal differs by dialect.
    rows = op.get_bind().execute(
        sa.text("SELECT id, username, business_unit_id, is_active FROM users WHERE role = 'Accountant'")
    ).fetchall()
    live = [r for r in rows if bool(r[3])]
    if live:
        sample = "; ".join(str(tuple(r)) for r in live[:10])
        raise RuntimeError(
            f"Refusing ERP removal: {len(live)} ACTIVE user(s) still have role='Accountant' (e.g. {sample}). "
            "Reassign them to CEO/HO/Chairman or deactivate them first, then re-run the upgrade."
        )

    # --- drop the ERP tables, FK-safe order ------------------------------
    for table in DROP_TABLES:
        op.drop_table(table)

    # --- drop business_units.nature (D19) --------------------------------
    # Batch mode: SQLite needs a table rebuild for DROP COLUMN, and the
    # column carries a plain index; on Postgres this is a plain ALTER.
    # The index must go first — batch mode re-creates the table's
    # reflected indexes, and ix_business_units_nature names the column
    # being dropped (same order as 9dbe5e45c702's own downgrade).
    with op.batch_alter_table("business_units") as batch_op:
        batch_op.drop_index("ix_business_units_nature")
        batch_op.drop_column("nature")

    # --- replace the users CHECK constraints (D3, Postgres-only) ---------
    # The constraints never existed on SQLite (Postgres-only migrations
    # + _pg_check), so there is nothing to swap there.
    if _is_postgres():
        op.drop_constraint("ck_users_bu_assignment", "users", type_="check")
        op.create_check_constraint("ck_users_bu_assignment", "users", NEW_BU_RULE)
        op.drop_constraint("ck_users_role", "users", type_="check")
        op.create_check_constraint("ck_users_role", "users", NEW_ROLE_RULE)


def downgrade() -> None:
    """ONE-WAY. This migration destroys data: 48 tables and every row in
    them. Re-creating empty tables would not restore anything, so
    downgrade() refuses instead of pretending a rollback exists.

    Restore path: the pre-removal backup — /tmp/local_dev.db.pre-erp-
    removal.2026-11-17 (the SQLite DEV database file, NOT a pg_dump;
    there is no production database — go-live never happened). For a
    Postgres deployment, restore the pg_dump taken before this
    migration ran there.
    """
    raise RuntimeError(
        "ERP-removal migration a720b6b7c616 is one-way: it drops 48 tables "
        "and cannot re-create their data. Restore the pre-removal backup "
        "instead (see the migration docstring)."
    )
