"""BOT review workstream D — migrations batch (m23–m27).

The ERP constraint tests from this batch (M-D5 unique document numbers,
M-D7 payment currency, M-C5 stock CHECK) were deleted with the ERP
removal (2026-11-17): those tables and constraints no longer exist.
What remains is the chain assertions plus the review's
migrations/tests minors:

- m24: Postgres-only constraints are never executed by the suite (no
  Postgres here) — static assertions on the constraint SQL plus unit
  tests of the pure policy/scrub helpers those migrations carry.
- m27: data-migration paths on non-empty DBs — the pre-flight abort and
  the idempotent rewrites, staged on scratch DBs at the parent revision.
- m25/m26: documented, not changed (irreversible scrub downgrade;
  Boolean server_default style) — pinned by static tests so the
  decisions stay visible.
- m23: bare `alembic` fails without env vars — pinned by a test that
  documents the footgun (no README / ops doc exists in this repo to
  write the requirement into; see the workstream report).

Run with: ENVIRONMENT=test DATABASE_URL=sqlite:// \
    .venv/bin/python -m pytest tests/test_bot_review_migrations_batch.py
"""
import ast
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VERSIONS = ROOT / "alembic" / "versions"
# Current head after the PM-6 sticky-anomaly review columns (2026-11-18):
# 9c4d2e7a1f08, on top of the idempotency_keys table 621509460777.
CURRENT_HEAD = "9c4d2e7a1f08"
CURRENT_HEAD_PARENT = "621509460777"
# The one-way ERP-removal drop migration: a downgrade to base stops here.
ONE_WAY_REVISION = "a720b6b7c616"


# ------------------------------------------------------------------ helpers

def _alembic_env(db_url, extra=None):
    env = {
        **os.environ,
        "ENVIRONMENT": "test",
        "DATABASE_URL": db_url,
        "MIGRATION_DATABASE_URL": db_url,
    }
    if extra:
        env.update(extra)
    return env


def _run_alembic(db_url, *args, extra=None):
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT, env=_alembic_env(db_url, extra),
        capture_output=True, text=True, timeout=600,
    )


def _stage_db(tmp_path, revision, extra=None):
    """Scratch DB upgraded to `revision`; returns (path, url)."""
    db_path = tmp_path / f"stage_{revision[:8]}.db"
    url = f"sqlite:///{db_path}"
    result = _run_alembic(url, "upgrade", revision, extra=extra)
    assert result.returncode == 0, (
        f"alembic upgrade {revision} failed:\n{result.stdout}\n{result.stderr}"
    )
    return db_path, url


def _migration_text(revision):
    matches = list(VERSIONS.glob(f"{revision}_*.py"))
    assert len(matches) == 1, f"expected one migration file for {revision}, got {matches}"
    return matches[0].read_text()


def _load_migration_module(revision):
    matches = list(VERSIONS.glob(f"{revision}_*.py"))
    assert len(matches) == 1
    spec = importlib.util.spec_from_file_location(f"_mig_{revision}", matches[0])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _balanced(expr):
    depth = 0
    in_single = False
    for ch in expr:
        if ch == "'" and not in_single:
            in_single = True
        elif ch == "'" and in_single:
            in_single = False
        elif not in_single:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth < 0:
                    return False
    return depth == 0 and not in_single


# ------------------------------------------------------- new migration: chain

def test_new_migration_is_the_single_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(cfg)
    assert script.get_heads() == [CURRENT_HEAD], (
        f"expected single head {CURRENT_HEAD}, got {script.get_heads()}")
    rev = script.get_revision(CURRENT_HEAD)
    assert rev.down_revision == CURRENT_HEAD_PARENT


def test_upgrade_head_then_downgrade_base_is_clean(tmp_path):
    # The ERP-removal drop migration (a720b6b7c616) is one-way by design
    # (plan §4.2): downgrade() refuses instead of re-creating 48 tables
    # whose data is gone. So "upgrade head, downgrade base" no longer
    # empties the DB — the downgrade reverses only the reversible
    # migrations above it, then fails at the drop migration and leaves
    # the schema intact one step below head.
    db_path = tmp_path / "chain.db"
    url = f"sqlite:///{db_path}"
    up = _run_alembic(url, "upgrade", "head")
    assert up.returncode == 0, f"upgrade head failed:\n{up.stdout}\n{up.stderr}"
    conn = sqlite3.connect(db_path)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert not [t for t in tables if t.startswith("_alembic_tmp_")], (
        "leftover batch temp tables after upgrade head")
    # 23 tracker data tables + alembic_version (48 ERP tables dropped).
    assert tables == {
        "action_items", "activity_logs", "alembic_version", "audit_logs",
        "business_units", "ceo_profiles", "customer_metrics",
        "daily_financials", "financial_notes", "health_statuses",
        "ho_activity_logs", "ho_daily_financials", "ho_health_statuses",
        "ho_monthly_targets", "ho_social_logs", "idempotency_keys",
        "job_heartbeats",
        "license_documents", "monthly_targets", "notifications",
        "password_history", "password_reset_requests", "social_logs", "staff_records", "users",
    }, f"unexpected tables after upgrade head: {sorted(tables)}"
    assert "invoice_payments" not in tables
    assert "journal_entries" not in tables
    assert _query(db_path, "SELECT version_num FROM alembic_version") == [
        (CURRENT_HEAD,)]
    down = _run_alembic(url, "downgrade", "base")
    assert down.returncode != 0, "downgrade past the ERP-removal migration must refuse"
    assert "one-way" in (down.stdout + down.stderr)
    # Nothing else was dropped: the downgrade peeled off all the
    # reversible migrations above the one-way drop migration (the
    # idempotency_keys table, the composite indexes, password_history
    # + the TOTP columns, and the reminder column), then refused
    # there — so the DB sits at the one-way revision, minus the two
    # dropped tables only.
    assert _query(db_path, "SELECT version_num FROM alembic_version") == [
        (ONE_WAY_REVISION,)]
    tables_after = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables_after == tables - {"password_history", "idempotency_keys"}
    assert not [t for t in tables_after if t.startswith("_alembic_tmp_")]
    conn.close()


# ------------------------------------------------------- m24: Postgres-only rules

PG_CONSTRAINT_EXPECTATIONS = [
    # (migration revision, constraint name/pattern, expression fragment).
    # Several b1c5d9e3a7f2 constraints are built with f-strings over table
    # lists, so the pattern (not the expanded name) is what's in the file;
    # test_every_pg_check_in_models_has_a_migration expands them.
    ("b1c5d9e3a7f2", "ck_users_role", "role IN ('Chairman', 'HO', 'CEO')"),
    ("b1c5d9e3a7f2", "uq_users_one_active_ceo_per_bu", "role = 'CEO' AND is_active"),
    ("b1c5d9e3a7f2", "ck_{table}_amount_range", "BETWEEN -{MONEY_LIMIT} AND {MONEY_LIMIT}"),
    ("b1c5d9e3a7f2", "ck_{table}_month_first", "extract(day from target_month) = 1"),
    ("b1c5d9e3a7f2", "ck_{table}_vitals_range", "bp_diastolic < bp_systolic"),
    ("c8e2a4b6d1f3", "ck_users_bu_assignment",
     "(role = 'CEO' AND business_unit_id IS NOT NULL)"),
    ("c8e2a4b6d1f3", "ck_users_bu_assignment",
     "(role IN ('Chairman', 'HO') AND business_unit_id IS NULL)"),
    ("bc3f5347dc23", "ck_users_role", "role IN ('Chairman', 'HO', 'CEO', 'Accountant')"),
]


@pytest.mark.parametrize("revision,name,expr", PG_CONSTRAINT_EXPECTATIONS)
def test_postgres_constraint_sql_exists_and_is_well_formed(revision, name, expr):
    text = _migration_text(revision)
    assert name in text, f"{name} missing from migration {revision}"
    assert expr in text, f"expression {expr!r} missing from migration {revision}"
    assert _balanced(expr), f"unbalanced parentheses/quotes in {expr!r}"


def test_postgres_fk_ondelete_policy_present():
    text = _migration_text("c2d6e0f4b8a3")
    for fragment in ("SET_NULL_COLUMNS", "CASCADE", '"RESTRICT"', '"SET NULL"',
                     '"CASCADE"', "ondelete"):
        assert fragment in text, f"{fragment!r} missing from c2d6e0f4b8a3"


def test_ondelete_policy_function_unit():
    # The Postgres-only rewrite itself can't run here, but its pure policy
    # function can: a typo in the mapping would otherwise surface only on Neon.
    mod = _load_migration_module("c2d6e0f4b8a3")
    assert mod._policy("daily_financials", "business_unit_id", "business_units") == "RESTRICT"
    assert mod._policy("daily_financials", "submitted_by", "users") == "SET NULL"
    assert mod._policy("monthly_targets", "set_by", "users") == "SET NULL"
    assert mod._policy("audit_logs", "user_id", "users") == "SET NULL"
    assert mod._policy("ceo_profiles", "user_id", "users") == "CASCADE"
    assert mod._policy("password_reset_requests", "user_id", "users") == "CASCADE"
    assert mod._policy("journal_entries", "business_unit_id", "business_units") == "RESTRICT"
    assert mod._policy("journal_lines", "account_id", "accounts") is None


def test_every_pg_check_in_models_has_a_migration():
    # A model-side CHECK with no migration behind it is exactly the bug
    # class test_migrations_chain guards for tables/columns. Several
    # b1c5d9e3a7f2 constraints are built with f-strings over table lists,
    # so expand those patterns here and compare against models.py.
    import src.models as models

    b1c5 = _load_migration_module("b1c5d9e3a7f2")
    expected = {
        "ck_users_role", "ck_users_bu_assignment", "ck_users_username_lower",
        "ck_financial_notes_text", "ck_pwreset_status",
        # (The ERP CHECKs — ck_inventory_items_stock_non_negative and
        # ck_stock_batches_qty_non_negative — died with the ERP removal,
        # 2026-11-17, so they no longer belong in this set.)
    }
    expected |= {f"ck_{t}_percent_range" for t in b1c5.ACTIVITY_TABLES}
    expected |= {f"ck_{t}_vitals_range" for t in b1c5.HEALTH_TABLES}
    expected |= {f"ck_{t}_amount_range" for t in b1c5.FINANCIAL_TABLES}
    expected |= {f"ck_{t}_targets_range" for t in b1c5.TARGET_TABLES}
    expected |= {f"ck_{t}_month_first" for t in b1c5.TARGET_TABLES}

    models_text = Path(models.__file__).read_text()
    names = set(re.findall(r'name="(ck_[a-z0-9_]+)"', models_text))
    assert names, "no ck_* constraints found in models.py"
    assert names <= expected, (
        f"model CHECKs with no migration behind them: {sorted(names - expected)}")


def test_b1c5_integrity_constants_sane():
    mod = _load_migration_module("b1c5d9e3a7f2")
    assert mod.MONEY_LIMIT == "10000000000000"
    assert mod.VITAL_RANGES["heart_rate"] == (20, 300)
    assert mod.VITAL_RANGES["daily_steps"] == (0, 200000)
    assert "daily_financials" in mod.FINANCIAL_TABLES
    assert "monthly_targets" in mod.TARGET_TABLES


# ------------------------------------------------------- m27: data migrations

def _raw(db_path, sql, params=()):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _query(db_path, sql, params=()):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def test_b1c5_duplicate_active_ceo_aborts_upgrade(tmp_path):
    db_path, url = _stage_db(tmp_path, "a7d4e1c9b2f6")
    _raw(db_path, "INSERT INTO business_units (id, code, name, created_at) "
                  "VALUES (1, 'B1', 'BU 1', '2026-01-01 00:00:00')")
    for i, username in enumerate(("ceo_one", "ceo_two")):
        _raw(db_path,
             "INSERT INTO users (username, hashed_password, role, business_unit_id, "
             "require_password_change, is_active, is_profile_completed, token_version, created_at) "
             "VALUES (?, 'x', 'CEO', 1, 0, 1, 1, 0, '2026-01-01 00:00:00')", (username,))
    result = _run_alembic(url, "upgrade", "b1c5d9e3a7f2")
    assert result.returncode != 0
    assert "more than one ACTIVE CEO" in result.stderr


def test_b1c5_single_ceo_upgrades_cleanly(tmp_path):
    db_path, url = _stage_db(tmp_path, "a7d4e1c9b2f6")
    _raw(db_path, "INSERT INTO business_units (id, code, name, created_at) "
                  "VALUES (1, 'B1', 'BU 1', '2026-01-01 00:00:00')")
    _raw(db_path,
         "INSERT INTO users (username, hashed_password, role, business_unit_id, "
         "require_password_change, is_active, is_profile_completed, token_version, created_at) "
         "VALUES ('ceo_one', 'x', 'CEO', 1, 0, 1, 1, 0, '2026-01-01 00:00:00')")
    result = _run_alembic(url, "upgrade", "b1c5d9e3a7f2")
    assert result.returncode == 0, f"upgrade failed:\n{result.stdout}\n{result.stderr}"
    idx = _query(db_path, "SELECT name FROM sqlite_master WHERE type='index' "
                          "AND name='uq_users_one_active_ceo_per_bu'")
    assert idx, "partial unique CEO index missing after upgrade"


def test_a7d4e1c9b2f6_rejects_all_but_newest_pending_reset(tmp_path):
    db_path, url = _stage_db(tmp_path, "f9c2b6a1d4e8")
    _raw(db_path,
         "INSERT INTO users (id, username, hashed_password, role, business_unit_id, "
         "require_password_change, is_active, is_profile_completed, token_version, created_at) "
         "VALUES (1, 'alice', 'x', 'HO', NULL, 0, 1, 1, 0, '2026-01-01 00:00:00')")
    for i in (1, 2):
        _raw(db_path,
             "INSERT INTO password_reset_requests (id, user_id, username, status, requested_at) "
             "VALUES (?, 1, 'alice', 'Pending', '2026-09-01 00:00:00')", (i,))
    result = _run_alembic(url, "upgrade", "a7d4e1c9b2f6")
    assert result.returncode == 0, f"upgrade failed:\n{result.stdout}\n{result.stderr}"
    rows = _query(db_path, "SELECT id, status FROM password_reset_requests ORDER BY id")
    assert rows == [(1, "Rejected"), (2, "Pending")]
    # The partial unique index now enforces one Pending per user.
    with pytest.raises(sqlite3.IntegrityError):
        _raw(db_path,
             "INSERT INTO password_reset_requests (id, user_id, username, status, requested_at) "
             "VALUES (3, 1, 'alice', 'Pending', '2026-09-02 00:00:00')")


def test_d3e7f1a5c9b4_encrypts_plaintext_backfill(tmp_path):
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    db_path, url = _stage_db(tmp_path, "c2d6e0f4b8a3")
    _raw(db_path,
         "INSERT INTO users (id, username, hashed_password, role, business_unit_id, "
         "require_password_change, is_active, is_profile_completed, token_version, created_at) "
         "VALUES (1, 'ceo1', 'x', 'CEO', 1, 0, 1, 1, 0, '2026-01-01 00:00:00')")
    _raw(db_path, "INSERT INTO business_units (id, code, name, created_at) "
                  "VALUES (1, 'B1', 'BU 1', '2026-01-01 00:00:00')")
    _raw(db_path,
         "INSERT INTO ceo_profiles (id, user_id, executive_title, phone_number, email, "
         "date_of_birth, address, created_at, updated_at) VALUES "
         "(1, 1, 'Chief Executive Officer', '+959123456', 'ceo@example.com', "
         "'2001-11-23', 'Yangon', '2026-01-01 00:00:00', '2026-01-01 00:00:00')")
    _raw(db_path,
         "INSERT INTO health_statuses (id, business_unit_id, report_date, other_notes, "
         "created_at, updated_at) VALUES "
         "(1, 1, '2026-09-01', 'feeling fine', '2026-01-01 00:00:00', '2026-01-01 00:00:00')")
    result = _run_alembic(url, "upgrade", "d3e7f1a5c9b4",
                          extra={"DATA_ENCRYPTION_KEY": key})
    assert result.returncode == 0, f"upgrade failed:\n{result.stdout}\n{result.stderr}"
    assert "encrypted" in result.stdout
    row = _query(db_path, "SELECT phone_number, email, date_of_birth, address "
                          "FROM ceo_profiles WHERE id = 1")[0]
    assert all(v.startswith("enc:v1:") for v in row), f"not all columns encrypted: {row}"
    note = _query(db_path, "SELECT other_notes FROM health_statuses WHERE id = 1")[0][0]
    assert note.startswith("enc:v1:")

    # The ciphertext decrypts back to the originals under the same key.
    import src.crypto as crypto
    from cryptography.fernet import MultiFernet

    fernet = MultiFernet([Fernet(key.encode())])
    old_fernet, old_primary = crypto._FERNET, crypto._PRIMARY
    crypto._FERNET, crypto._PRIMARY = fernet, Fernet(key.encode())
    try:
        assert crypto.decrypt_text(row[0]) == "+959123456"
        assert crypto.decrypt_text(row[1]) == "ceo@example.com"
        assert crypto.decrypt_text(row[2]) == "2001-11-23"
        assert crypto.decrypt_text(row[3]) == "Yangon"
        assert crypto.decrypt_text(note) == "feeling fine"
    finally:
        crypto._FERNET, crypto._PRIMARY = old_fernet, old_primary

    # Re-running the backfill (downgrade decrypts, upgrade re-encrypts)
    # round-trips instead of double-encrypting.
    down = _run_alembic(url, "downgrade", "c2d6e0f4b8a3", extra={"DATA_ENCRYPTION_KEY": key})
    assert down.returncode == 0, f"downgrade failed:\n{down.stdout}\n{down.stderr}"
    plain = _query(db_path, "SELECT phone_number FROM ceo_profiles WHERE id = 1")[0][0]
    assert plain == "+959123456"
    up2 = _run_alembic(url, "upgrade", "d3e7f1a5c9b4", extra={"DATA_ENCRYPTION_KEY": key})
    assert up2.returncode == 0
    again = _query(db_path, "SELECT phone_number FROM ceo_profiles WHERE id = 1")[0][0]
    assert again.startswith("enc:v1:") and not again.startswith("enc:v1:enc:v1:")


def test_d3e7f1a5c9b4_skips_backfill_without_key(tmp_path):
    db_path, url = _stage_db(tmp_path, "c2d6e0f4b8a3")
    _raw(db_path,
         "INSERT INTO users (id, username, hashed_password, role, business_unit_id, "
         "require_password_change, is_active, is_profile_completed, token_version, created_at) "
         "VALUES (1, 'ceo1', 'x', 'CEO', 1, 0, 1, 1, 0, '2026-01-01 00:00:00')")
    _raw(db_path,
         "INSERT INTO ceo_profiles (id, user_id, executive_title, phone_number, "
         "date_of_birth, address, created_at, updated_at) VALUES "
         "(1, 1, 'Chief Executive Officer', '+959123456', "
         "'2001-11-23', 'Yangon', '2026-01-01 00:00:00', '2026-01-01 00:00:00')")
    result = _run_alembic(url, "upgrade", "d3e7f1a5c9b4")
    assert result.returncode == 0, f"upgrade failed:\n{result.stdout}\n{result.stderr}"
    assert "leaving existing values as plaintext" in result.stdout
    row = _query(db_path, "SELECT phone_number FROM ceo_profiles WHERE id = 1")[0][0]
    assert row == "+959123456"


def test_e4f8a2b6d0c5_scrubs_audit_details(tmp_path):
    db_path, url = _stage_db(tmp_path, "d3e7f1a5c9b4")
    details = (
        "resubmit 2026-09-01 resubmit_changes_json=" + json.dumps([
            {"field": "Heart Rate", "old": "80", "new": "90"},
            {"field": "Revenue", "old": "100", "new": "200"},
        ])
    )
    _raw(db_path,
         "INSERT INTO audit_logs (id, action, details, timestamp) "
         "VALUES (1, 'REPORT_RESUBMITTED', ?, '2026-09-02 00:00:00')", (details,))
    result = _run_alembic(url, "upgrade", "e4f8a2b6d0c5")
    assert result.returncode == 0, f"upgrade failed:\n{result.stdout}\n{result.stderr}"
    assert "redacted 1 of 1" in result.stdout
    new_details = _query(db_path, "SELECT details FROM audit_logs WHERE id = 1")[0][0]
    head, _, raw = new_details.partition("resubmit_changes_json=")
    changes = json.loads(raw)
    by_field = {c["field"]: c for c in changes}
    assert by_field["Heart Rate"] == {"field": "Heart Rate", "old": "(hidden)", "new": "(hidden)"}
    # Money is the tamper trail and is deliberately left alone.
    assert by_field["Revenue"] == {"field": "Revenue", "old": "100", "new": "200"}


def test_scrub_helper_unit():
    mod = _load_migration_module("e4f8a2b6d0c5")
    assert mod._scrub("no json here") is None
    assert mod._scrub("resubmit_changes_json=not-json") is None
    assert mod._scrub('resubmit_changes_json={"a": 1}') is None
    already = 'x resubmit_changes_json=[{"field": "Steps", "old": "(hidden)", "new": "(hidden)"}]'
    assert mod._scrub(already) is None  # idempotent: nothing left to change
    dirty = ('x resubmit_changes_json=[{"field": "Steps", "old": "1000", "new": "2000"}, '
             '{"field": "Revenue", "old": "5", "new": "6"}]')
    out = mod._scrub(dirty)
    changes = json.loads(out.partition("resubmit_changes_json=")[2])
    assert changes[0] == {"field": "Steps", "old": "(hidden)", "new": "(hidden)"}
    assert changes[1] == {"field": "Revenue", "old": "5", "new": "6"}


def test_e4f8a2b6d0c5_downgrade_cannot_restore_scrubbed_data(tmp_path):
    # m25: the irreversible downgrade, by design — pinned here so a future
    # rollback past this revision is a conscious decision, not a surprise.
    db_path, url = _stage_db(tmp_path, "d3e7f1a5c9b4")
    details = "resubmit_changes_json=" + json.dumps(
        [{"field": "Sleep Hours", "old": "7", "new": "8"}])
    _raw(db_path,
         "INSERT INTO audit_logs (id, action, details, timestamp) "
         "VALUES (1, 'REPORT_RESUBMITTED', ?, '2026-09-02 00:00:00')", (details,))
    assert _run_alembic(url, "upgrade", "e4f8a2b6d0c5").returncode == 0
    scrubbed = _query(db_path, "SELECT details FROM audit_logs WHERE id = 1")[0][0]
    assert "(hidden)" in scrubbed
    down = _run_alembic(url, "downgrade", "d3e7f1a5c9b4")
    assert down.returncode == 0
    assert _query(db_path, "SELECT details FROM audit_logs WHERE id = 1")[0][0] == scrubbed

    tree = ast.parse(_migration_text("e4f8a2b6d0c5"))
    downgrade = next(n for n in ast.walk(tree)
                     if isinstance(n, ast.FunctionDef) and n.name == "downgrade")
    non_docstring = [s for s in downgrade.body
                     if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    assert non_docstring == [], "e4f8a2b6d0c5 downgrade is meant to be a docstring-only no-op"


def test_m26_boolean_server_default_style_noted_not_changed():
    # m26: e633899b1fb0 uses server_default='0' (string) where the head
    # migration uses sa.false(). Cosmetic; editing an already-applied
    # migration is worse than the inconsistency, so this pins the known
    # state instead of "fixing" it.
    text = _migration_text("e633899b1fb0")
    assert "server_default='0'" in text
    assert "requires_expiry" in text


# ------------------------------------------------------- m23: bare alembic CLI

def test_bare_alembic_cli_requires_env_vars(tmp_path):
    # m23, as fixed by C-1 (PM review 2026-11-18): a plain `alembic heads`
    # with no ENVIRONMENT/DATABASE_URL used to crash (d3e7f1a5c9b4 imports
    # src.crypto at module load -> config raises). That coupling is gone:
    # `heads` is a static graph query that needs no database at all, so it
    # now succeeds bare. What still needs env is a command that touches a
    # database - `upgrade head` without DATABASE_URL must still fail loud
    # naming DATABASE_URL (the migrate job's only required var).
    bare_env = {k: v for k, v in os.environ.items()
                if k not in ("ENVIRONMENT", "DATABASE_URL", "MIGRATION_DATABASE_URL")}
    bare_heads = subprocess.run(
        [sys.executable, "-m", "alembic", "heads"],
        cwd=ROOT, env=bare_env, capture_output=True, text=True, timeout=120,
    )
    assert bare_heads.returncode == 0, bare_heads.stderr
    assert CURRENT_HEAD in bare_heads.stdout
    bare_upgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT, env=bare_env, capture_output=True, text=True, timeout=120,
    )
    assert bare_upgrade.returncode != 0
    assert "DATABASE_URL" in bare_upgrade.stderr
    url = f"sqlite:///{tmp_path / 'heads.db'}"
    with_vars = _run_alembic(url, "heads")
    assert with_vars.returncode == 0
    assert CURRENT_HEAD in with_vars.stdout
