"""BOT review workstream D — migrations batch (M-D5, M-C5, M-D7, m23–m27).

Covers the new migration ee078d0716ad (per-BU unique document numbers,
invoice_payments.currency, inventory stock CHECK) and the review's
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
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from src.accounting_templates import template_accounts
from src.models import (
    Account, Base, BusinessUnit, Customer, InventoryItem, InvoicePayment,
    JournalEntry, JournalLine, PurchaseOrder, Quotation, SalesInvoice,
    StockTransfer,
)
from src.routers.ops import (
    CustomerIn, InvoiceIn, InvoiceLineIn, PaymentIn, POIn, POLineIn,
    create_invoice, create_po, create_sales_customer, create_supplier,
    record_payment, send_invoice, SupplierIn,
)
from src.routers.accounting import create_entry

ROOT = Path(__file__).resolve().parent.parent
VERSIONS = ROOT / "alembic" / "versions"
NEW_REVISION = "ee078d0716ad"
PREV_HEAD = "c9ec96d6b1e5"

from types import SimpleNamespace

ADMIN = SimpleNamespace(id=None, role="Chairman", business_unit_id=None)


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


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    bu = BusinessUnit(name="Distributor", code="D1")
    session.add(bu)
    session.commit()
    id_by_code = {}
    for code, name, acc_type, parent_code, is_header in template_accounts("distribution"):
        acc = Account(business_unit_id=bu.id, code=code, name=name,
                      account_type=acc_type, is_header=is_header, is_system=True,
                      parent_id=id_by_code.get(parent_code) if parent_code else None)
        session.add(acc)
        session.flush()
        id_by_code[code] = acc.id
    session.commit()
    session.test_bu = bu
    session.test_ids = id_by_code
    yield session
    session.close()


def _make_customer(db):
    return create_sales_customer(
        CustomerIn(business_unit_id=db.test_bu.id, name="Hotel"), user=ADMIN, db=db)


def _make_invoice(db, price=100000, qty=10):
    cust = _make_customer(db)
    return create_invoice(InvoiceIn(
        business_unit_id=db.test_bu.id, customer_id=cust["id"],
        invoice_date=date.today(), due_date=None, tax_rate=5,
        lines=[InvoiceLineIn(description="Goods", quantity=qty, unit_price=price)],
    ), user=ADMIN, db=db)


def _make_supplier(db):
    return create_supplier(
        SupplierIn(business_unit_id=db.test_bu.id, name="Supplier"), user=ADMIN, db=db)


# ------------------------------------------------------- new migration: chain

def test_new_migration_is_the_single_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(cfg)
    assert script.get_heads() == [NEW_REVISION], (
        f"expected single head {NEW_REVISION}, got {script.get_heads()}")
    rev = script.get_revision(NEW_REVISION)
    assert rev.down_revision == PREV_HEAD


def test_upgrade_head_then_downgrade_base_is_clean(tmp_path):
    db_path = tmp_path / "chain.db"
    url = f"sqlite:///{db_path}"
    up = _run_alembic(url, "upgrade", "head")
    assert up.returncode == 0, f"upgrade head failed:\n{up.stdout}\n{up.stderr}"
    conn = sqlite3.connect(db_path)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert not [t for t in tables if t.startswith("_alembic_tmp_")], (
        "leftover batch temp tables after upgrade head")
    assert "invoice_payments" in tables
    down = _run_alembic(url, "downgrade", "base")
    assert down.returncode == 0, f"downgrade base failed:\n{down.stdout}\n{down.stderr}"
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables == {"alembic_version"}, f"tables left after downgrade base: {tables}"
    assert not [t for t in tables if t.startswith("_alembic_tmp_")]
    conn.close()


# ------------------------------------------------------- M-D5: unique numbers

EXPECTED_UNIQUES = {
    "journal_entries": ("uq_journal_entries_bu_entry_no", ["business_unit_id", "entry_no"]),
    "purchase_orders": ("uq_purchase_orders_bu_po_no", ["business_unit_id", "po_no"]),
    "sales_invoices": ("uq_sales_invoices_bu_invoice_no", ["business_unit_id", "invoice_no"]),
    "quotations": ("uq_quotations_bu_quote_no", ["business_unit_id", "quote_no"]),
    "sales_returns": ("uq_sales_returns_bu_return_no", ["business_unit_id", "return_no"]),
    "purchase_returns": ("uq_purchase_returns_bu_return_no", ["business_unit_id", "return_no"]),
    "stock_transfers": ("uq_stock_transfers_from_bu_transfer_no",
                         ["from_business_unit_id", "transfer_no"]),
}


def test_migrated_schema_has_all_unique_constraints_and_currency(tmp_path):
    db_path, url = _stage_db(tmp_path, "head")
    engine = create_engine(url)
    try:
        inspector = inspect(engine)
        for table, (name, columns) in EXPECTED_UNIQUES.items():
            uniques = inspector.get_unique_constraints(table)
            match = [u for u in uniques if u["name"] == name]
            assert match, f"{table}: unique constraint {name} missing after migration (have {[u['name'] for u in uniques]})"
            assert match[0]["column_names"] == columns
        cols = {c["name"]: c for c in inspector.get_columns("invoice_payments")}
        assert "currency" in cols
        assert cols["currency"]["type"].length == 3
        assert cols["currency"]["default"] == "'MMK'"
    finally:
        engine.dispose()


def test_duplicate_numbers_rejected_on_migrated_db(tmp_path):
    db_path, url = _stage_db(tmp_path, "head")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("INSERT INTO business_units (id, code, name, created_at) "
                     "VALUES (1, 'B1', 'BU 1', '2026-01-01 00:00:00')")
        conn.execute("INSERT INTO business_units (id, code, name, created_at) "
                     "VALUES (2, 'B2', 'BU 2', '2026-01-01 00:00:00')")
        conn.execute("INSERT INTO customers (id, business_unit_id, name, is_active, created_at) "
                     "VALUES (1, 1, 'C', 1, '2026-01-01 00:00:00')")
        conn.execute("INSERT INTO sales_invoices (id, business_unit_id, customer_id, invoice_no, "
                     "status, invoice_date, tax_rate, currency, exchange_rate, created_at) "
                     "VALUES (1, 1, 1, 'INV-1-0001', 'draft', '2026-11-09', 5, 'MMK', 1, '2026-01-01 00:00:00')")
        conn.commit()
        # Same BU, same number -> rejected.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO sales_invoices (id, business_unit_id, customer_id, invoice_no, "
                         "status, invoice_date, tax_rate, currency, exchange_rate, created_at) "
                         "VALUES (2, 1, 1, 'INV-1-0001', 'draft', '2026-11-09', 5, 'MMK', 1, '2026-01-01 00:00:00')")
        conn.rollback()
        # A different BU may reuse the same number string.
        conn.execute("INSERT INTO sales_invoices (id, business_unit_id, customer_id, invoice_no, "
                     "status, invoice_date, tax_rate, currency, exchange_rate, created_at) "
                     "VALUES (3, 2, 1, 'INV-1-0001', 'draft', '2026-11-09', 5, 'MMK', 1, '2026-01-01 00:00:00')")
        conn.commit()
        # Journal entry_no: same BU + same number rejected.
        conn.execute("INSERT INTO journal_entries (id, business_unit_id, entry_no, entry_date, "
                     "source, status, approval_status, created_at) "
                     "VALUES (1, 1, 'JV-2026-11-001', '2026-11-09', 'manual', 'draft', 'not_required', '2026-01-01 00:00:00')")
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO journal_entries (id, business_unit_id, entry_no, entry_date, "
                         "source, status, approval_status, created_at) "
                         "VALUES (2, 1, 'JV-2026-11-001', '2026-11-09', 'manual', 'draft', 'not_required', '2026-01-01 00:00:00')")
        conn.rollback()
    finally:
        conn.close()


def test_model_unique_constraints_reject_duplicates(db):
    bu = db.test_bu
    db.add(JournalEntry(business_unit_id=bu.id, entry_no="JV-2026-11-001",
                        entry_date=date(2026, 11, 9), source="manual", status="draft"))
    db.commit()
    db.add(JournalEntry(business_unit_id=bu.id, entry_no="JV-2026-11-001",
                        entry_date=date(2026, 11, 9), source="manual", status="draft"))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()

    sup = _make_supplier(db)
    db.add(PurchaseOrder(business_unit_id=bu.id, supplier_id=sup["id"], po_no="PO-1-0001",
                         status="draft", order_date=date(2026, 11, 9)))
    db.commit()
    db.add(PurchaseOrder(business_unit_id=bu.id, supplier_id=sup["id"], po_no="PO-1-0001",
                         status="draft", order_date=date(2026, 11, 9)))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()

    bu2 = BusinessUnit(name="Second", code="D2")
    db.add(bu2)
    db.commit()
    # Same number string in a different BU is fine.
    db.add(PurchaseOrder(business_unit_id=bu2.id, supplier_id=sup["id"], po_no="PO-1-0001",
                         status="draft", order_date=date(2026, 11, 9)))
    db.commit()

    db.add(StockTransfer(from_business_unit_id=bu.id, to_business_unit_id=bu2.id,
                         transfer_no="TRF-1-0001", status="draft"))
    db.commit()
    db.add(StockTransfer(from_business_unit_id=bu.id, to_business_unit_id=bu2.id,
                         transfer_no="TRF-1-0001", status="draft"))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_duplicate_invoice_number_returns_409_not_500(db):
    # Two invoices exist; deleting the first makes count+1 collide with the
    # second — the endpoint must answer 409, never store a duplicate.
    inv1 = _make_invoice(db)
    inv2 = _make_invoice(db)
    assert inv1["invoice_no"] != inv2["invoice_no"]
    row = db.query(SalesInvoice).filter(SalesInvoice.id == inv1["id"]).one()
    db.delete(row)
    db.commit()
    cust = db.query(Customer).first()
    with pytest.raises(HTTPException) as exc:
        create_invoice(InvoiceIn(
            business_unit_id=db.test_bu.id, customer_id=cust.id,
            invoice_date=date.today(), due_date=None, tax_rate=5,
            lines=[InvoiceLineIn(description="Goods", quantity=1, unit_price=1000)],
        ), user=ADMIN, db=db)
    assert exc.value.status_code == 409
    assert inv2["invoice_no"] in exc.value.detail
    # The colliding row is untouched and the session is still usable.
    assert db.query(SalesInvoice).count() == 1


def test_duplicate_po_number_returns_409_not_500(db):
    sup = _make_supplier(db)
    payload = lambda: POIn(
        business_unit_id=db.test_bu.id, supplier_id=sup["id"],
        order_date=date.today(),
        lines=[POLineIn(item_name="Widget", quantity=1, unit_cost=1000)],
    )
    po1 = create_po(payload(), user=ADMIN, db=db)
    po2 = create_po(payload(), user=ADMIN, db=db)
    assert po1["po_no"] != po2["po_no"]
    row = db.query(PurchaseOrder).filter(PurchaseOrder.id == po1["id"]).one()
    db.delete(row)
    db.commit()
    with pytest.raises(HTTPException) as exc:
        create_po(payload(), user=ADMIN, db=db)
    assert exc.value.status_code == 409
    assert po2["po_no"] in exc.value.detail


def test_duplicate_journal_entry_number_returns_409_not_500(db):
    def line(code, debit=0, credit=0):
        return {"account_id": db.test_ids[code], "debit": Decimal(str(debit)),
                "credit": Decimal(str(credit))}

    e1 = create_entry(db, db.test_bu.id, date(2026, 11, 1), "first", "manual", None,
                      [line("1110", debit=1000), line("4100", credit=1000)], post=True)
    e2 = create_entry(db, db.test_bu.id, date(2026, 11, 1), "second", "manual", None,
                      [line("1110", debit=500), line("4100", credit=500)], post=True)
    db.commit()
    assert e1.entry_no != e2.entry_no
    db.delete(e1)
    db.commit()
    with pytest.raises(HTTPException) as exc:
        create_entry(db, db.test_bu.id, date(2026, 11, 1), "third", "manual", None,
                     [line("1110", debit=700), line("4100", credit=700)], post=True)
    assert exc.value.status_code == 409
    assert e2.entry_no in exc.value.detail


# ------------------------------------------------------- M-D7: payment currency

def test_payment_records_its_currency(db):
    inv = _make_invoice(db)
    send_invoice(inv["id"], user=ADMIN, db=db)
    out = record_payment(inv["id"], PaymentIn(amount=100000, payment_date=date.today(),
                                              method="cash"), user=ADMIN, db=db)
    assert out["status"] == "partial"
    pay = db.query(InvoicePayment).filter(InvoicePayment.invoice_id == inv["id"]).one()
    assert pay.currency == "MMK"

    # Explicit currency is stored as given (upper-cased).
    inv2 = _make_invoice(db)
    send_invoice(inv2["id"], user=ADMIN, db=db)
    record_payment(inv2["id"], PaymentIn(amount=100000, payment_date=date.today(),
                                         method="bank", currency="mmk"), user=ADMIN, db=db)
    pay2 = db.query(InvoicePayment).filter(InvoicePayment.invoice_id == inv2["id"]).one()
    assert pay2.currency == "MMK"


def test_payment_in_foreign_currency_records_it(db):
    cust = _make_customer(db)
    inv = create_invoice(InvoiceIn(
        business_unit_id=db.test_bu.id, customer_id=cust["id"],
        invoice_date=date.today(), due_date=None, tax_rate=5, currency="USD",
        exchange_rate=2100,
        lines=[InvoiceLineIn(description="Goods", quantity=1, unit_price=100)],
    ), user=ADMIN, db=db)
    send_invoice(inv["id"], user=ADMIN, db=db)
    record_payment(inv["id"], PaymentIn(amount=50, payment_date=date.today(),
                                        method="bank", currency="USD"), user=ADMIN, db=db)
    pay = db.query(InvoicePayment).filter(InvoicePayment.invoice_id == inv["id"]).one()
    assert pay.currency == "USD"
    # Mismatched currency is still rejected (workstream B's validation).
    with pytest.raises(HTTPException) as exc:
        record_payment(inv["id"], PaymentIn(amount=10, payment_date=date.today(),
                                            method="bank", currency="MMK"), user=ADMIN, db=db)
    assert exc.value.status_code == 400


def test_invoice_payment_currency_column_default(db):
    # ORM-side default matches the migration's server default.
    assert InvoicePayment.__table__.c.currency.default.arg == "MMK"
    assert InvoicePayment.__table__.c.currency.nullable is False


# ------------------------------------------------------- M-C5: stock CHECK

def test_stock_check_present_in_models_and_migration():
    from sqlalchemy import CheckConstraint

    checks = [c for c in InventoryItem.__table__.constraints
              if isinstance(c, CheckConstraint)]
    match = [c for c in checks if c.name == "ck_inventory_items_stock_non_negative"]
    assert match, f"stock CHECK missing from models (have {[c.name for c in checks]})"
    assert "current_stock >= 0" in str(match[0].sqltext)
    # Postgres-only by design (repo convention — see _pg_check): the DDL is
    # emitted for postgresql and skipped for sqlite, so the check protects
    # production (Neon) while local SQLite relies on the API validation.
    from sqlalchemy.dialects import postgresql, sqlite
    from sqlalchemy.schema import CreateTable

    ddl_pg = str(CreateTable(InventoryItem.__table__).compile(dialect=postgresql.dialect()))
    ddl_sqlite = str(CreateTable(InventoryItem.__table__).compile(dialect=sqlite.dialect()))
    assert "ck_inventory_items_stock_non_negative" in ddl_pg
    assert "CHECK (current_stock >= 0)" in ddl_pg
    assert "ck_inventory_items_stock_non_negative" not in ddl_sqlite
    text = _migration_text(NEW_REVISION)
    assert "ck_inventory_items_stock_non_negative" in text
    assert "current_stock >= 0" in text
    assert "create_check_constraint" in text


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
        "ck_inventory_items_stock_non_negative",
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
    # And the new stock CHECK's migration really carries it.
    assert "ck_inventory_items_stock_non_negative" in _migration_text(NEW_REVISION)


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
    # m23: a plain `alembic heads` with no ENVIRONMENT/DATABASE_URL crashes
    # (d3e7f1a5c9b4 imports src.crypto at module load -> config raises).
    # There is no README / ops doc in this repo to write the requirement
    # into, so the requirement is pinned here and documented in the
    # workstream report: always run alembic with
    # ENVIRONMENT=test DATABASE_URL=... MIGRATION_DATABASE_URL=... set.
    bare_env = {k: v for k, v in os.environ.items()
                if k not in ("ENVIRONMENT", "DATABASE_URL", "MIGRATION_DATABASE_URL")}
    bare = subprocess.run(
        [sys.executable, "-m", "alembic", "heads"],
        cwd=ROOT, env=bare_env, capture_output=True, text=True, timeout=120,
    )
    assert bare.returncode != 0
    assert "DATABASE_URL" in bare.stderr or "ENVIRONMENT" in bare.stderr
    url = f"sqlite:///{tmp_path / 'heads.db'}"
    with_vars = _run_alembic(url, "heads")
    assert with_vars.returncode == 0
    assert NEW_REVISION in with_vars.stdout
