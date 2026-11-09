"""CM-3 Postgres proof (Workstream C): the guarded money transitions
hold on Postgres, not just SQLite.

Same T-1 style as tests/test_sec_concurrency.py (two threads, two
sessions, a Barrier so the calls truly race), but skipped unless
DATABASE_URL is Postgres (the T-m4 pattern) — the local suite runs on
SQLite, so these tests execute in CI (.github/workflows/postgres-check.yml).

Depends on workstream A's CM-3 contract fix (already in the tree):
no 400 pre-check before the claim; the conditional UPDATE
(`... WHERE id=? AND status IN (...)`) is the arbiter, and the loser
gets HTTP 409 with an "already been ..." message, on both dialects.
Each test asserts exactly one success and a 409 loser for
receive_po / send_invoice / record_payment.

NOT verified locally: no Postgres is available in this VM. Live
Postgres execution awaits the first CI run — do NOT claim it as
verified here.

Scratch ids are in the 91000 range so they cannot collide with the
T-m4 file's 90000-range rows or dev seed data; each test cleans its
rows up afterwards.
"""

import os
import threading
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from src.database import Base
from src.models import (BusinessUnit, InventoryItem, InvoicePayment,
                        JournalEntry, StockMovement, Supplier, User)
from src.routers.accounting import provision_bu
from src.routers.ops import (CustomerIn, InvoiceIn, InvoiceLineIn,
                             PaymentIn, POIn, POLineIn, create_invoice,
                             create_po, create_sales_customer, receive_po,
                             record_payment, send_invoice)

BU_ID = 91001
USER_ID = 91001
CHAIRMAN = SimpleNamespace(id=USER_ID, role="Chairman", business_unit_id=None,
                           username="rt-proof-chairman")


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
    reason="Postgres-only proof: run under the postgres-check CI workflow "
           "(DATABASE_URL must point at Postgres)",
)


@pytest.fixture()
def pg_db():
    """Postgres engine + seeded BU/CoA/chairman, cleaned up afterwards.

    NullPool: each thread gets its own connection, like two app workers.
    lock_timeout/statement_timeout mirror the production connect options
    so a loser waiting on the winner's row lock fails fast instead of
    hanging the CI job.
    """
    engine = create_engine(
        os.environ["DATABASE_URL"],
        poolclass=NullPool,
        pool_pre_ping=True,
        connect_args={"options": "-c lock_timeout=5000 -c statement_timeout=15000"},
    )
    # Idempotent on the CI-migrated DB (create_all skips existing tables).
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(User(id=USER_ID, username="rt-proof-chairman", hashed_password="x",
                     role="Chairman", is_active=True, token_version=0))
    session.add(BusinessUnit(id=BU_ID, code="BU91001", name="RT Proof BU",
                             nature="retail"))
    session.commit()
    provision_bu(BU_ID, "retail", CHAIRMAN, session)
    session.commit()
    session.test_engine = engine
    yield session
    _cleanup(session)
    session.close()
    engine.dispose()


def _cleanup(session):
    """Delete every scratch row for BU 91001 (FK-safe order: children
    first, via subqueries, before their parent tables are touched)."""
    bu = BU_ID
    session.execute(text(
        "DELETE FROM journal_lines WHERE entry_id IN "
        "(SELECT id FROM journal_entries WHERE business_unit_id = :bu)"), {"bu": bu})
    session.execute(text(
        "DELETE FROM invoice_payments WHERE invoice_id IN "
        "(SELECT id FROM sales_invoices WHERE business_unit_id = :bu)"), {"bu": bu})
    session.execute(text(
        "DELETE FROM sales_invoice_lines WHERE invoice_id IN "
        "(SELECT id FROM sales_invoices WHERE business_unit_id = :bu)"), {"bu": bu})
    session.execute(text(
        "DELETE FROM purchase_order_lines WHERE po_id IN "
        "(SELECT id FROM purchase_orders WHERE business_unit_id = :bu)"), {"bu": bu})
    session.execute(text(
        "DELETE FROM stock_movements WHERE item_id IN "
        "(SELECT id FROM inventory_items WHERE business_unit_id = :bu)"), {"bu": bu})
    for table in ("journal_entries", "sales_invoices", "purchase_orders",
                  "inventory_items", "suppliers", "customers", "accounts",
                  "fiscal_periods"):
        session.execute(text(f"DELETE FROM {table} WHERE business_unit_id = :bu"),
                        {"bu": bu})
    session.execute(text("DELETE FROM business_units WHERE id = :bu"), {"bu": bu})
    # Rows that reference the scratch chairman by user id.
    for table in ("audit_logs", "notifications", "idempotency_keys"):
        session.execute(text(f"DELETE FROM {table} WHERE user_id = :u"), {"u": USER_ID})
    session.execute(text("DELETE FROM users WHERE id = :u"), {"u": USER_ID})
    session.commit()


def _run_two(engine, fn):
    """Run fn(session) in two threads at the same time; returns the two
    outcomes as ("ok", value) or ("error", exception)."""
    barrier = threading.Barrier(2, timeout=30)
    outcomes = [None, None]

    def _worker(slot):
        session = sessionmaker(bind=engine)()
        try:
            barrier.wait(timeout=30)
            outcomes[slot] = ("ok", fn(session))
        except Exception as exc:  # noqa: BLE001 - the outcome IS the assertion
            outcomes[slot] = ("error", exc)
        finally:
            session.close()

    threads = [threading.Thread(target=_worker, args=(i,)) for i in (0, 1)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not any(t.is_alive() for t in threads), "concurrency worker hung"
    return outcomes


def _assert_one_winner_one_409(outcomes, message):
    assert [kind for kind, _ in outcomes].count("ok") == 1, outcomes
    assert [kind for kind, _ in outcomes].count("error") == 1, outcomes
    err = [exc for kind, exc in outcomes if kind == "error"][0]
    assert isinstance(err, HTTPException), err
    assert err.status_code == 409, err
    assert message in err.detail, err.detail


class TestPostgresReceivePo:
    def test_two_simultaneous_receives_one_409(self, pg_db):
        db, engine = pg_db, pg_db.test_engine
        supplier = Supplier(name="Vendor", business_unit_id=BU_ID)
        db.add(supplier)
        db.commit()
        # Under the 1,000,000 two-stage threshold: no approval stage, so a
        # plain draft PO can be received straight away.
        po = create_po(POIn(business_unit_id=BU_ID, supplier_id=supplier.id,
                            order_date=date(2026, 10, 20),
                            lines=[POLineIn(item_name="Generator", quantity=1,
                                            unit_cost=100000)]),
                       user=CHAIRMAN, db=db)
        po_id = po["id"]

        outcomes = _run_two(engine, lambda s: receive_po(po_id, user=CHAIRMAN, db=s))
        _assert_one_winner_one_409(outcomes, "has already been received or moved")

        fresh = sessionmaker(bind=engine)()
        try:
            assert fresh.query(JournalEntry).filter(
                JournalEntry.business_unit_id == BU_ID,
                JournalEntry.status == "posted").count() == 1
            item = fresh.query(InventoryItem).filter(
                InventoryItem.business_unit_id == BU_ID,
                InventoryItem.name == "Generator").one()
            assert float(item.current_stock) == 1  # received once, not twice
            assert fresh.query(StockMovement).filter(
                StockMovement.item_id == item.id).count() == 1
        finally:
            fresh.close()


class TestPostgresSendInvoice:
    def test_two_simultaneous_sends_one_409(self, pg_db):
        db, engine = pg_db, pg_db.test_engine
        cust = create_sales_customer(CustomerIn(business_unit_id=BU_ID, name="Buyer"),
                                     user=CHAIRMAN, db=db)
        inv = create_invoice(InvoiceIn(
            business_unit_id=BU_ID, customer_id=cust["id"], invoice_date=date(2026, 10, 5),
            tax_rate=0, lines=[InvoiceLineIn(description="Sale", quantity=1, unit_price=200000)]),
            user=CHAIRMAN, db=db)
        inv_id = inv["id"]

        outcomes = _run_two(engine, lambda s: send_invoice(inv_id, user=CHAIRMAN, db=s))
        _assert_one_winner_one_409(outcomes, "has already been sent or moved")

        fresh = sessionmaker(bind=engine)()
        try:
            assert fresh.query(JournalEntry).filter(
                JournalEntry.business_unit_id == BU_ID,
                JournalEntry.source == "invoice", JournalEntry.source_id == inv_id,
                JournalEntry.entry_date == date(2026, 10, 5),
                JournalEntry.status == "posted").count() == 1
        finally:
            fresh.close()


class TestPostgresRecordPayment:
    def test_two_simultaneous_payments_one_409(self, pg_db):
        db, engine = pg_db, pg_db.test_engine
        cust = create_sales_customer(CustomerIn(business_unit_id=BU_ID, name="Buyer"),
                                     user=CHAIRMAN, db=db)
        inv = create_invoice(InvoiceIn(
            business_unit_id=BU_ID, customer_id=cust["id"], invoice_date=date(2026, 10, 5),
            tax_rate=0, lines=[InvoiceLineIn(description="Sale", quantity=1, unit_price=200000)]),
            user=CHAIRMAN, db=db)
        send_invoice(inv["id"], user=CHAIRMAN, db=db)
        inv_id = inv["id"]

        outcomes = _run_two(
            engine,
            lambda s: record_payment(inv_id,
                                     PaymentIn(amount=200000, payment_date=date(2026, 10, 7)),
                                     user=CHAIRMAN, db=s))
        _assert_one_winner_one_409(outcomes, "has already been paid or moved")

        fresh = sessionmaker(bind=engine)()
        try:
            assert fresh.query(InvoicePayment).filter(
                InvoicePayment.invoice_id == inv_id).count() == 1
            assert fresh.query(JournalEntry).filter(
                JournalEntry.source == "invoice", JournalEntry.source_id == inv_id,
                JournalEntry.entry_date == date(2026, 10, 7),
                JournalEntry.status == "posted").count() == 1
        finally:
            fresh.close()
