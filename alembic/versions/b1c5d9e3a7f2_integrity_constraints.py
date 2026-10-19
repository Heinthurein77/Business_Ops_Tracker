"""database-level integrity: one active CEO per business unit, CHECK constraints

Revision ID: b1c5d9e3a7f2
Revises: a7d4e1c9b2f6
Create Date: 2026-09-24 00:00:00.000000

The API already validates all of this (schemas.py / the submit-report routes),
but the database is the last line of defence: a bug, a second writer, or a
future endpoint must not be able to store a NaN revenue, a 900 bpm heart rate,
a role that doesn't exist, or two active CEOs for the same business unit.

Safety on a live database: every rule is PRE-FLIGHT checked first. Vitals and
percentages that are out of range are set to NULL (they were never valid and
NULL means "not recorded", which the app already handles); anything that would
need a business decision (two active CEOs, an unknown role, an out-of-range
financial figure, a target month that isn't the 1st) aborts the whole upgrade
with a message naming the offending rows, before any constraint is created.
On Postgres the migration is one transaction, so an abort leaves the database
exactly as it was.

The CHECK constraints are Postgres-only: SQLite (local development only) can't
ALTER constraints without rebuilding each table. The partial unique index works
on both.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b1c5d9e3a7f2'
down_revision: Union[str, Sequence[str], None] = 'a7d4e1c9b2f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MONEY_LIMIT = "10000000000000"  # 1e13, well inside Numeric(16, 2)

# (table, [money columns]) — HO twin tables carry the same rules.
FINANCIAL_TABLES = {
    "daily_financials": ["revenue", "cash_flow", "receivable", "payable", "other_amount"],
    "ho_daily_financials": ["revenue", "cash_flow", "receivable", "payable", "other_amount"],
}
ACTIVITY_TABLES = ["activity_logs", "ho_activity_logs"]
HEALTH_TABLES = ["health_statuses", "ho_health_statuses"]
TARGET_TABLES = ["monthly_targets", "ho_monthly_targets"]

VITAL_RANGES = {
    "daily_steps": (0, 200000),
    "heart_rate": (20, 300),
    "sleep_hours": (0, 24),
    "bp_systolic": (40, 300),
    "bp_diastolic": (20, 200),
}


def _is_pg() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _count(sql: str) -> int:
    return int(op.get_bind().execute(sa.text(sql)).scalar() or 0)


def _abort_if(sql_count: str, sql_list: str, message: str) -> None:
    n = _count(sql_count)
    if n:
        rows = op.get_bind().execute(sa.text(sql_list)).fetchall()
        sample = "; ".join(str(tuple(r)) for r in rows[:10])
        raise RuntimeError(f"{message} ({n} row(s), e.g. {sample}). Fix the data, then re-run the upgrade.")


def _null_out_of_range(table: str, column: str, low, high) -> None:
    """A value outside its plausible range was never a real measurement."""
    bad = f"{column} IS NOT NULL AND NOT ({column} BETWEEN {low} AND {high})"
    n = _count(f"SELECT count(*) FROM {table} WHERE {bad}")
    if n:
        print(f"[integrity migration] {table}.{column}: setting {n} out-of-range value(s) to NULL")
        op.execute(f"UPDATE {table} SET {column} = NULL WHERE {bad}")


def upgrade() -> None:
    """Upgrade schema."""
    pg = _is_pg()

    # --- one ACTIVE CEO per business unit (both dialects) -------------------
    _abort_if(
        "SELECT count(*) FROM (SELECT business_unit_id FROM users WHERE role = 'CEO' AND is_active "
        "AND business_unit_id IS NOT NULL GROUP BY business_unit_id HAVING count(*) > 1) d",
        "SELECT business_unit_id, count(*) FROM users WHERE role = 'CEO' AND is_active "
        "AND business_unit_id IS NOT NULL GROUP BY business_unit_id HAVING count(*) > 1",
        "Some business units have more than one ACTIVE CEO account — deactivate all but the current one "
        "(User Management) before upgrading",
    )
    op.create_index(
        'uq_users_one_active_ceo_per_bu',
        'users',
        ['business_unit_id'],
        unique=True,
        postgresql_where=sa.text("role = 'CEO' AND is_active"),
        sqlite_where=sa.text("role = 'CEO' AND is_active = 1"),
    )
    op.create_index('ix_audit_user_action_ts', 'audit_logs', ['user_id', 'action', 'timestamp'])
    op.create_index('ix_financial_notes_bu_date', 'financial_notes', ['business_unit_id', 'note_date'])

    if not pg:
        return

    # --- users ---------------------------------------------------------------
    _abort_if(
        "SELECT count(*) FROM users WHERE role NOT IN ('Chairman', 'HO', 'CEO')",
        "SELECT id, username, role FROM users WHERE role NOT IN ('Chairman', 'HO', 'CEO')",
        "Users with an unknown role",
    )
    _abort_if(
        "SELECT count(*) FROM users WHERE (role = 'CEO') <> (business_unit_id IS NOT NULL)",
        "SELECT id, username, role, business_unit_id FROM users WHERE (role = 'CEO') <> (business_unit_id IS NOT NULL)",
        "A CEO without a business unit, or a non-CEO attached to one",
    )
    _abort_if(
        "SELECT count(*) FROM users WHERE username <> lower(username)",
        "SELECT id, username FROM users WHERE username <> lower(username)",
        "Usernames that aren't lower-case (logins are case-normalised, so these could never sign in)",
    )
    op.create_check_constraint('ck_users_role', 'users', "role IN ('Chairman', 'HO', 'CEO')")
    op.create_check_constraint('ck_users_ceo_has_bu', 'users', "(role = 'CEO') = (business_unit_id IS NOT NULL)")
    op.create_check_constraint('ck_users_username_lower', 'users', "username = lower(username)")

    # --- reset requests / notes ---------------------------------------------
    _abort_if(
        "SELECT count(*) FROM password_reset_requests WHERE status NOT IN ('Pending', 'Approved', 'Rejected')",
        "SELECT id, status FROM password_reset_requests WHERE status NOT IN ('Pending', 'Approved', 'Rejected')",
        "Reset requests with an unknown status",
    )
    op.create_check_constraint(
        'ck_pwreset_status', 'password_reset_requests', "status IN ('Pending', 'Approved', 'Rejected')"
    )
    op.execute("UPDATE financial_notes SET note_text = '(empty note)' WHERE length(btrim(note_text)) = 0")
    op.create_check_constraint('ck_financial_notes_text', 'financial_notes', "length(btrim(note_text)) > 0")

    # --- percentages ---------------------------------------------------------
    for table in ACTIVITY_TABLES:
        for column in ("gym_percent", "swimming_percent", "cycle_percent"):
            _null_out_of_range(table, column, 0, 100)
        op.create_check_constraint(
            f'ck_{table}_percent_range',
            table,
            "gym_percent BETWEEN 0 AND 100 AND swimming_percent BETWEEN 0 AND 100 AND cycle_percent BETWEEN 0 AND 100",
        )

    # --- vitals --------------------------------------------------------------
    for table in HEALTH_TABLES:
        for column, (low, high) in VITAL_RANGES.items():
            _null_out_of_range(table, column, low, high)
        both_bp = "bp_systolic IS NOT NULL AND bp_diastolic IS NOT NULL AND bp_diastolic >= bp_systolic"
        n = _count(f"SELECT count(*) FROM {table} WHERE {both_bp}")
        if n:
            print(f"[integrity migration] {table}: {n} row(s) with diastolic >= systolic — clearing both readings")
            op.execute(f"UPDATE {table} SET bp_systolic = NULL, bp_diastolic = NULL WHERE {both_bp}")
        clauses = [f"{c} BETWEEN {lo} AND {hi}" for c, (lo, hi) in VITAL_RANGES.items()]
        clauses.append("(bp_systolic IS NULL OR bp_diastolic IS NULL OR bp_diastolic < bp_systolic)")
        op.create_check_constraint(f'ck_{table}_vitals_range', table, " AND ".join(clauses))

    # --- money (never altered silently: it's the business's books) ----------
    for table, columns in FINANCIAL_TABLES.items():
        bad = " OR ".join(f"({c} IS NOT NULL AND NOT ({c} BETWEEN -{MONEY_LIMIT} AND {MONEY_LIMIT}))" for c in columns)
        _abort_if(
            f"SELECT count(*) FROM {table} WHERE {bad}",
            f"SELECT id, report_date FROM {table} WHERE {bad}",
            f"{table} has an amount outside +/-1e13 (or NaN)",
        )
        op.create_check_constraint(
            f'ck_{table}_amount_range',
            table,
            " AND ".join(f"{c} BETWEEN -{MONEY_LIMIT} AND {MONEY_LIMIT}" for c in columns),
        )

    # --- monthly targets -----------------------------------------------------
    for table in TARGET_TABLES:
        rule = (
            "monthly_revenue_target BETWEEN 0 AND 10000000000000 "
            f"AND monthly_cash_flow_target BETWEEN -{MONEY_LIMIT} AND {MONEY_LIMIT} "
            "AND monthly_max_receivable BETWEEN 0 AND 10000000000000 "
            "AND monthly_max_payable BETWEEN 0 AND 10000000000000"
        )
        _abort_if(
            f"SELECT count(*) FROM {table} WHERE NOT ({rule})",
            f"SELECT id, target_month FROM {table} WHERE NOT ({rule})",
            f"{table} has a target that is negative (where that is not allowed) or out of range",
        )
        _abort_if(
            f"SELECT count(*) FROM {table} WHERE extract(day from target_month) <> 1",
            f"SELECT id, target_month FROM {table} WHERE extract(day from target_month) <> 1",
            f"{table}.target_month must always be the 1st of a month",
        )
        op.create_check_constraint(f'ck_{table}_targets_range', table, rule)
        op.create_check_constraint(f'ck_{table}_month_first', table, "extract(day from target_month) = 1")


def downgrade() -> None:
    """Downgrade schema."""
    if _is_pg():
        for table in TARGET_TABLES:
            op.drop_constraint(f'ck_{table}_month_first', table, type_='check')
            op.drop_constraint(f'ck_{table}_targets_range', table, type_='check')
        for table in FINANCIAL_TABLES:
            op.drop_constraint(f'ck_{table}_amount_range', table, type_='check')
        for table in HEALTH_TABLES:
            op.drop_constraint(f'ck_{table}_vitals_range', table, type_='check')
        for table in ACTIVITY_TABLES:
            op.drop_constraint(f'ck_{table}_percent_range', table, type_='check')
        op.drop_constraint('ck_financial_notes_text', 'financial_notes', type_='check')
        op.drop_constraint('ck_pwreset_status', 'password_reset_requests', type_='check')
        op.drop_constraint('ck_users_username_lower', 'users', type_='check')
        op.drop_constraint('ck_users_ceo_has_bu', 'users', type_='check')
        op.drop_constraint('ck_users_role', 'users', type_='check')
    op.drop_index('ix_financial_notes_bu_date', table_name='financial_notes')
    op.drop_index('ix_audit_user_action_ts', table_name='audit_logs')
    op.drop_index('uq_users_one_active_ceo_per_bu', table_name='users')
