"""explicit ON DELETE policy for every foreign key

Revision ID: c2d6e0f4b8a3
Revises: b1c5d9e3a7f2
Create Date: 2026-09-24 00:00:00.000000

Every FK was created with no ON DELETE clause (= NO ACTION), which happens to
block deletes but says nothing about intent and makes erasing a person's
account impossible without orphaning or destroying history. The policy:

  * business_unit_id  -> RESTRICT   a BU's reports must never silently vanish
  * submitted_by / set_by / created_by / resolved_by -> SET NULL
                                    keep the record when the person is erased
  * audit_logs.user_id -> SET NULL  the audit row keeps its denormalised username
  * ceo_profiles.user_id, password_reset_requests.user_id -> CASCADE
                                    personal data / requests die with the account

Postgres only: SQLite (dev) never enforced these by default and can't alter a
constraint without rebuilding the table. Constraint names are discovered from
the live schema (Postgres auto-names them <table>_<column>_fkey), not assumed.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c2d6e0f4b8a3'
down_revision: Union[str, Sequence[str], None] = 'b1c5d9e3a7f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SET_NULL_COLUMNS = {"submitted_by", "set_by", "created_by", "resolved_by"}
CASCADE = {("ceo_profiles", "user_id"), ("password_reset_requests", "user_id")}


def _policy(table: str, column: str, referred_table: str):
    if referred_table == "business_units":
        return "RESTRICT"
    if (table, column) in CASCADE:
        return "CASCADE"
    if column in SET_NULL_COLUMNS or (table == "audit_logs" and column == "user_id"):
        return "SET NULL"
    return None


def _rewrite(direction_policy) -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    inspector = sa.inspect(bind)
    for table in inspector.get_table_names():
        for fk in inspector.get_foreign_keys(table):
            if len(fk["constrained_columns"]) != 1 or not fk.get("name"):
                continue
            column = fk["constrained_columns"][0]
            referred = fk["referred_table"]
            ondelete = direction_policy(table, column, referred)
            current = (fk.get("options") or {}).get("ondelete")
            if ondelete is None or (current or "NO ACTION").upper() == ondelete.upper():
                continue
            op.drop_constraint(fk["name"], table, type_="foreignkey")
            op.create_foreign_key(
                fk["name"], table, referred, [column], fk["referred_columns"],
                ondelete=None if ondelete == "NO ACTION" else ondelete,
            )


def upgrade() -> None:
    """Upgrade schema."""
    _rewrite(_policy)


def downgrade() -> None:
    """Downgrade schema."""
    _rewrite(lambda table, column, referred: "NO ACTION" if _policy(table, column, referred) else None)
