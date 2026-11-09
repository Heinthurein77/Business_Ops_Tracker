"""Real-time risk review (Area A): concurrency & data-integrity backstops

Revision ID: 7f3a9c1e5b2d
Revises: f58eca28a187
Create Date: 2026-11-09

- CM-8 optimistic locking: integer ``version`` (default 0) on
  purchase_orders, sales_invoices, inventory_items, customers,
  payroll_runs, expense_requests.
- CM-6 idempotency: ``idempotency_keys`` table (user, endpoint, key)
  storing the JSON response of the first execution for 24 h replays.
- CC-1 / CM-4 (stock_batches): unique (item_id, batch_no) so two
  concurrent receives of the same batch number cannot both insert,
  and CHECK quantity >= 0 so FEFO over-consumption can never drive a
  batch negative silently.
- Cm-7: partial unique index on journal_entries
  (business_unit_id, source, source_id) WHERE source = 'year_close' —
  at most one closing journal per BU per year, on both dialects.

Safety on a live database: the stock_batches unique constraint aborts
the upgrade (naming the offending rows) if duplicate (item, batch_no)
pairs already exist, and the CHECK aborts if a negative quantity
already exists — both must be fixed in the data first, before the
upgrade. On Postgres the migration is one transaction, so an abort
leaves the database exactly as it was.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7f3a9c1e5b2d'
down_revision: Union[str, Sequence[str], None] = 'f58eca28a187'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_VERSION_TABLES = (
    "purchase_orders",
    "sales_invoices",
    "inventory_items",
    "customers",
    "payroll_runs",
    "expense_requests",
)


def _count(sql: str) -> int:
    return int(op.get_bind().execute(sa.text(sql)).scalar() or 0)


def _abort_if(sql_count: str, sql_list: str, message: str) -> None:
    n = _count(sql_count)
    if n:
        rows = op.get_bind().execute(sa.text(sql_list)).fetchall()
        sample = "; ".join(str(tuple(r)) for r in rows[:10])
        raise RuntimeError(
            f"{message} ({n} row(s), e.g. {sample}). Fix the data, then re-run the upgrade."
        )


def upgrade() -> None:
    """Upgrade schema."""
    # Pre-flight: duplicate batch numbers / negative batch quantities
    # would make the new constraints fail mid-upgrade.
    _abort_if(
        "SELECT count(*) FROM (SELECT item_id, batch_no FROM stock_batches "
        "GROUP BY item_id, batch_no HAVING count(*) > 1)",
        "SELECT item_id, batch_no, count(*) FROM stock_batches "
        "GROUP BY item_id, batch_no HAVING count(*) > 1 LIMIT 10",
        "stock_batches has duplicate (item_id, batch_no) pairs",
    )
    _abort_if(
        "SELECT count(*) FROM stock_batches WHERE quantity < 0",
        "SELECT id, item_id, batch_no, quantity FROM stock_batches "
        "WHERE quantity < 0 LIMIT 10",
        "stock_batches has negative quantities",
    )

    for table in _VERSION_TABLES:
        with op.batch_alter_table(table, recreate="never") as batch_op:
            batch_op.add_column(
                sa.Column("version", sa.Integer(), server_default="0", nullable=False)
            )

    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("endpoint", sa.String(length=120), nullable=False),
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("response_body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_idempotency_keys_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_idempotency_keys")),
        sa.UniqueConstraint("user_id", "endpoint", "key", name="uq_idempotency_user_endpoint_key"),
    )
    op.create_index(op.f("ix_idempotency_keys_user_id"), "idempotency_keys", ["user_id"], unique=False)
    op.create_index(op.f("ix_idempotency_keys_endpoint"), "idempotency_keys", ["endpoint"], unique=False)
    op.create_index(op.f("ix_idempotency_keys_id"), "idempotency_keys", ["id"], unique=False)

    with op.batch_alter_table("stock_batches") as batch_op:
        batch_op.create_unique_constraint(
            "uq_stock_batches_item_batch_no", ["item_id", "batch_no"]
        )
        batch_op.create_check_constraint(
            "ck_stock_batches_qty_non_negative", "quantity >= 0"
        )

    # Partial unique index: one year-close journal per BU per year.
    op.create_index(
        "uq_journal_year_close",
        "journal_entries",
        ["business_unit_id", "source", "source_id"],
        unique=True,
        postgresql_where=sa.text("source = 'year_close'"),
        sqlite_where=sa.text("source = 'year_close'"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_journal_year_close", table_name="journal_entries")
    with op.batch_alter_table("stock_batches") as batch_op:
        batch_op.drop_constraint("ck_stock_batches_qty_non_negative", type_="check")
        batch_op.drop_constraint("uq_stock_batches_item_batch_no", type_="unique")
    op.drop_index(op.f("ix_idempotency_keys_id"), table_name="idempotency_keys")
    op.drop_index(op.f("ix_idempotency_keys_endpoint"), table_name="idempotency_keys")
    op.drop_index(op.f("ix_idempotency_keys_user_id"), table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
    for table in _VERSION_TABLES:
        with op.batch_alter_table(table, recreate="never") as batch_op:
            batch_op.drop_column("version")
