"""accountants belong to exactly one business unit — users BU check update

Revision ID: c8e2a4b6d1f3
Revises: 59cd42cd0c1a
Create Date: 2026-10-22 00:00:00.000000

Accountants used to be group-level accounts (business_unit_id always
NULL), which is what the ck_users_ceo_has_bu CHECK encoded: a BU was
attached exactly when the role was CEO. Accountants are now assigned to
exactly one BU each (the API requires it at creation; legacy accountant
rows with a NULL BU may still exist and keep group-wide access, so the
database rule cannot require a BU for them). This migration swaps the
Postgres CHECK for the new assignment rule:

- CEO        -> business_unit_id IS NOT NULL (unchanged)
- Chairman/HO -> business_unit_id IS NULL (unchanged)
- Accountant -> either (assigned, or a legacy unassigned row)

Postgres-only, like the constraint it replaces: SQLite can't ALTER
constraints without rebuilding the table, and local development relies
on the API's validation (see _pg_check in src/models.py).
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'c8e2a4b6d1f3'
down_revision: Union[str, Sequence[str], None] = '59cd42cd0c1a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_RULE = (
    "(role = 'CEO' AND business_unit_id IS NOT NULL) OR "
    "(role IN ('Chairman', 'HO') AND business_unit_id IS NULL) OR "
    "(role = 'Accountant')"
)
OLD_RULE = "(role = 'CEO') = (business_unit_id IS NOT NULL)"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgres():
        return
    op.drop_constraint('ck_users_ceo_has_bu', 'users', type_='check')
    op.create_check_constraint('ck_users_bu_assignment', 'users', NEW_RULE)


def downgrade() -> None:
    if not _is_postgres():
        return
    op.drop_constraint('ck_users_bu_assignment', 'users', type_='check')
    op.create_check_constraint('ck_users_ceo_has_bu', 'users', OLD_RULE)
