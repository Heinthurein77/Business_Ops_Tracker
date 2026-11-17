"""T-m4: Postgres-only behaviour, executed (not just statically asserted).

The whole local suite runs on SQLite, so every test in this file is
SKIPPED unless DATABASE_URL points at Postgres. It runs in CI
(.github/workflows/postgres-check.yml): a postgres:16-alpine service,
`alembic upgrade head`, then this file — one violating insert per
Postgres CHECK / partial unique index / FK ON DELETE policy, asserting
the database itself rejects it.

NOT verified locally: no Postgres is available in this VM and there is
no docker binary here. Postgres execution is pending CI.

Each test inserts its own scratch rows (ids in the 90000 range, so they
cannot collide with dev seed data) and cleans them up afterwards.
"""
import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError


def _is_postgres():
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return False
    try:
        return make_url(url).get_dialect().name == "postgresql"
    except Exception:
        return "postgres" in url


pytestmark = pytest.mark.skipif(
    not _is_postgres(),
    reason="Postgres-only checks: run under the postgres-check CI workflow "
           "(DATABASE_URL must point at Postgres)",
)


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    yield eng
    eng.dispose()


def _attempt(engine, sql, params=None):
    """Run `sql` in its own transaction; True iff Postgres rejects it
    with an integrity error. The transaction is rolled back either way,
    so a non-violating statement leaves nothing behind."""
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            conn.execute(text(sql), params or {})
        except IntegrityError:
            trans.rollback()
            return True
        trans.rollback()
        return False


def _exec(engine, sql, params=None):
    with engine.begin() as conn:
        conn.execute(text(sql), params or {})


def _scalar(engine, sql, params=None):
    with engine.connect() as conn:
        return conn.execute(text(sql), params or {}).scalar()


def _user_sql(username, role, bu_id=None, active=True):
    return (
        "INSERT INTO users (username, hashed_password, role, business_unit_id, "
        "require_password_change, is_profile_completed, is_active, token_version, created_at) "
        "VALUES (:u, 'x', :role, :bu, false, true, :active, 0, CURRENT_TIMESTAMP)"
    ), {"u": username, "role": role, "bu": bu_id, "active": active}


# ------------------------------------------------------- users CHECKs (b1c5, c8e2)

def test_pg_check_rejects_unknown_role(engine):
    sql, params = _user_sql("pg_bad_role", "SuperUser")
    assert _attempt(engine, sql, params), "ck_users_role did not reject role 'SuperUser'"


def test_pg_check_rejects_mixed_case_username(engine):
    sql, params = _user_sql("MixedCase", "HO")
    assert _attempt(engine, sql, params), "ck_users_username_lower did not reject 'MixedCase'"


def test_pg_check_rejects_ceo_without_bu(engine):
    sql, params = _user_sql("pg_ceo_nobu", "CEO", bu_id=None)
    assert _attempt(engine, sql, params), "ck_users_bu_assignment did not reject a CEO with no BU"


def test_pg_check_rejects_chairman_with_bu(engine):
    _exec(engine, "INSERT INTO business_units (id, code, name, created_at) "
                  "VALUES (90001, 'PG1', 'PG BU 1', CURRENT_TIMESTAMP)")
    try:
        sql, params = _user_sql("pg_chair_with_bu", "Chairman", bu_id=90001)
        assert _attempt(engine, sql, params), \
            "ck_users_bu_assignment did not reject a Chairman with a BU"
    finally:
        _exec(engine, "DELETE FROM business_units WHERE id = 90001")


# ------------------------------------------------------- partial unique index (b1c5)

def test_pg_partial_index_blocks_second_active_ceo(engine):
    _exec(engine, "INSERT INTO business_units (id, code, name, created_at) "
                  "VALUES (90002, 'PG2', 'PG BU 2', CURRENT_TIMESTAMP)")
    try:
        sql1, p1 = _user_sql("pg_ceo_a", "CEO", bu_id=90002, active=True)
        sql2, p2 = _user_sql("pg_ceo_b", "CEO", bu_id=90002, active=True)
        _exec(engine, sql1, p1)
        assert _attempt(engine, sql2, p2), \
            "uq_users_one_active_ceo_per_bu did not block a second active CEO"
        # An INACTIVE second CEO is outside the partial index and is fine.
        sql3, p3 = _user_sql("pg_ceo_c", "CEO", bu_id=90002, active=False)
        assert not _attempt(engine, sql3, p3), \
            "inactive CEO unexpectedly blocked by the partial index"
    finally:
        _exec(engine, "DELETE FROM users WHERE username IN ('pg_ceo_a', 'pg_ceo_b', 'pg_ceo_c')")
        _exec(engine, "DELETE FROM business_units WHERE id = 90002")


# ------------------------------------------------------- stock CHECK (ee078d0716ad)


# ------------------------------------------------------- targets CHECKs (b1c5)

def test_pg_check_rejects_non_first_of_month_target(engine):
    _exec(engine, "INSERT INTO business_units (id, code, name, created_at) "
                  "VALUES (90004, 'PG4', 'PG BU 4', CURRENT_TIMESTAMP)")
    try:
        assert _attempt(engine,
            "INSERT INTO monthly_targets (business_unit_id, target_month, "
            "monthly_revenue_target, monthly_cash_flow_target, monthly_max_receivable, "
            "monthly_max_payable, created_at, updated_at) "
            "VALUES (90004, '2026-09-15', 1000, 0, 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"), \
            "ck_monthly_targets_month_first did not reject target_month 2026-09-15"
        assert not _attempt(engine,
            "INSERT INTO monthly_targets (business_unit_id, target_month, "
            "monthly_revenue_target, monthly_cash_flow_target, monthly_max_receivable, "
            "monthly_max_payable, created_at, updated_at) "
            "VALUES (90004, '2026-09-01', 1000, 0, 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"), \
            "target_month 2026-09-01 unexpectedly rejected"
    finally:
        _exec(engine, "DELETE FROM monthly_targets WHERE business_unit_id = 90004")
        _exec(engine, "DELETE FROM business_units WHERE id = 90004")


# ------------------------------------------------------- vitals CHECK (b1c5)

def test_pg_check_rejects_out_of_range_heart_rate(engine):
    _exec(engine, "INSERT INTO business_units (id, code, name, created_at) "
                  "VALUES (90005, 'PG5', 'PG BU 5', CURRENT_TIMESTAMP)")
    try:
        assert _attempt(engine,
            "INSERT INTO health_statuses (business_unit_id, report_date, daily_steps, "
            "heart_rate, sleep_hours, bp_systolic, bp_diastolic, is_sick, created_at, updated_at) "
            "VALUES (90005, '2026-09-01', 8000, 999, 7.5, 120, 80, false, "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"), \
            "ck_health_statuses_vitals_range did not reject heart_rate = 999"
    finally:
        _exec(engine, "DELETE FROM health_statuses WHERE business_unit_id = 90005")
        _exec(engine, "DELETE FROM business_units WHERE id = 90005")


# ------------------------------------------------------- FK ON DELETE (c2d6e0f4b8a3)

def test_pg_fk_restrict_blocks_bu_delete_with_financials(engine):
    _exec(engine, "INSERT INTO business_units (id, code, name, created_at) "
                  "VALUES (90006, 'PG6', 'PG BU 6', CURRENT_TIMESTAMP)")
    _exec(engine, "INSERT INTO daily_financials (business_unit_id, report_date, revenue, "
                  "expenses, net_profit, cash_in_hand, created_at, updated_at) "
                  "VALUES (90006, '2026-09-01', 0, 0, 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)")
    try:
        assert _attempt(engine, "DELETE FROM business_units WHERE id = 90006"), \
            "FK ON DELETE RESTRICT did not block deleting a BU with daily_financials"
    finally:
        _exec(engine, "DELETE FROM daily_financials WHERE business_unit_id = 90006")
        _exec(engine, "DELETE FROM business_units WHERE id = 90006")


def test_pg_fk_set_null_keeps_audit_row_after_user_delete(engine):
    _exec(engine, "INSERT INTO users (id, username, hashed_password, role, business_unit_id, "
                  "require_password_change, is_profile_completed, is_active, token_version, created_at) "
                  "VALUES (90007, 'pg_gone', 'x', 'HO', NULL, false, true, true, 0, CURRENT_TIMESTAMP)")
    _exec(engine, "INSERT INTO audit_logs (id, user_id, username, action, details, timestamp) "
                  "VALUES (90007, 90007, 'pg_gone', 'LOGIN_SUCCESS', 'pg check', CURRENT_TIMESTAMP)")
    try:
        # NOTE: do NOT use _attempt() for this delete — it rolls its
        # transaction back either way, so the delete would never persist
        # and user_id could never become NULL. _exec() commits, letting
        # ON DELETE SET NULL take effect. (CI run 44037284247 failed
        # exactly here: 8 passed, this one failed with "user_id was not
        # SET NULL" only because the delete had been rolled back.)
        try:
            _exec(engine, "DELETE FROM users WHERE id = 90007")
        except IntegrityError:
            raise AssertionError(
                "deleting a user referenced by audit_logs unexpectedly failed")
        assert _scalar(engine,
            "SELECT user_id FROM audit_logs WHERE id = 90007") is None, \
            "audit_logs.user_id was not SET NULL on user delete"
        assert _scalar(engine,
            "SELECT count(*) FROM audit_logs WHERE id = 90007") == 1, \
            "audit row disappeared on user delete instead of surviving with user_id NULL"
    finally:
        _exec(engine, "DELETE FROM audit_logs WHERE id = 90007")
        _exec(engine, "DELETE FROM users WHERE id = 90007")
