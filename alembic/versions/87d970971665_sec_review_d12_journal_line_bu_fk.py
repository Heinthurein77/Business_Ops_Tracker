"""BOT sec review D-12: DB-level guard that journal lines belong to the entry's BU

Revision ID: 87d970971665
Revises: ee078d0716ad
Create Date: 2026-11-09

Adds ``journal_lines.business_unit_id`` (backfilled from the entry), a
UNIQUE constraint on ``accounts (business_unit_id, id)`` (the composite-FK
target — required even though ``id`` alone is already the PK), and a
composite FK ``journal_lines (business_unit_id, account_id)`` ->
``accounts (business_unit_id, id)``. A line whose account belongs to a
different BU than the entry is now rejected by the database, not just by
``validate_lines`` in the accounting router — so a future code path, script
or migration that writes lines without calling it can no longer silently
post cross-BU lines.

The column stays nullable: the migration-chain smoke test constructs
JournalLine rows directly without it, and every application write goes
through ``create_entry``, which always sets it from the entry. A NULL
business_unit_id line is not checked by the FK (MATCH SIMPLE), so the
guard covers every row the application creates; a NOT NULL tightening is
a follow-up once direct JournalLine construction is gone.

Postgres-safe: all DDL goes through batch_alter_table (SQLite rebuilds the
table; Postgres takes the plain ALTER). The backfill runs before the FK is
added, so no existing row can violate it — the upgrade aborts first if a
line's entry cannot be found (orphan lines, which the entry FK should
already prevent).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '87d970971665'
down_revision: Union[str, Sequence[str], None] = 'ee078d0716ad'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ACCOUNTS_BU_ID_UQ = "uq_accounts_bu_id"
LINES_BU_ACCOUNT_FK = "fk_journal_lines_bu_account_accounts"


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("accounts") as batch_op:
        batch_op.create_unique_constraint(ACCOUNTS_BU_ID_UQ, ["business_unit_id", "id"])

    with op.batch_alter_table("journal_lines") as batch_op:
        batch_op.add_column(sa.Column("business_unit_id", sa.Integer(), nullable=True))

    # Backfill from the entry before the FK is added.
    op.execute(sa.text(
        "UPDATE journal_lines SET business_unit_id = "
        "(SELECT business_unit_id FROM journal_entries "
        " WHERE journal_entries.id = journal_lines.entry_id)"
    ))
    orphaned = int(op.get_bind().execute(sa.text(
        "SELECT count(*) FROM journal_lines WHERE business_unit_id IS NULL"
    )).scalar() or 0)
    if orphaned:
        raise RuntimeError(
            f"journal_lines has {orphaned} row(s) whose entry cannot be found — "
            "resolve the orphan lines, then re-run the upgrade."
        )

    with op.batch_alter_table("journal_lines") as batch_op:
        batch_op.create_foreign_key(
            LINES_BU_ACCOUNT_FK,
            "accounts",
            ["business_unit_id", "account_id"],
            ["business_unit_id", "id"],
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("journal_lines") as batch_op:
        batch_op.drop_constraint(LINES_BU_ACCOUNT_FK, type_="foreignkey")
        batch_op.drop_column("business_unit_id")
    with op.batch_alter_table("accounts") as batch_op:
        batch_op.drop_constraint(ACCOUNTS_BU_ID_UQ, type_="unique")
