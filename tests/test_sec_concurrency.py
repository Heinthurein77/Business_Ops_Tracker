"""Concurrency tests (T-1): two simultaneous money-moving calls must not
both succeed.

The prior review's M-D5 fix (per-BU unique document numbers,
ee078d0716ad) makes a *sequential* duplicate fail, but nothing ever
issued two concurrent requests. These tests fire two threads with
separate sessions against a file-backed SQLite DB (in-memory SQLite
cannot be shared across threads) and assert exactly one call succeeds
and exactly one journal set is posted.

The loser surfaces as a 409 (already moved) — what matters is that it
does NOT succeed and does NOT post a second journal.

NOTE: these tests previously proved the race is real (both calls
succeeded and double-posted; the unique entry_no does NOT backstop it,
because the transactions serialise at the FIRST flush, which happens
BEFORE journal creation). The guard has since landed in
src/routers/ops.py — a conditional status claim (`UPDATE ... WHERE id=?
AND status IN (...)`, loser aborts with 409 when the rowcount is 0) in
receive_po / record_payment, and the same pattern in pay_po,
send_invoice and approve_advance — so these tests now assert the guard
holds instead of xfail-ing.
"""
import threading
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from src.models import (
    Base, BusinessUnit, InventoryItem, InvoicePayment, JournalEntry,
    StockMovement, Supplier, User,
)
from src.routers.accounting import provision_bu
from src.routers.ops import (
    CustomerIn, InvoiceIn, InvoiceLineIn, PaymentIn, POIn, POLineIn,
    create_invoice, create_po, create_sales_customer, receive_po,
    record_payment, send_invoice,
)

CHAIRMAN = SimpleNamespace(id=1, role="Chairman", business_unit_id=None, username="chairman")


@pytest.fixture()
def file_db(tmp_path):
    """File-backed SQLite DB with WAL so two threads can write in turn.

    NullPool + check_same_thread=False: each thread gets its own
    connection through its own session, like two app workers would.
    """
    db_path = tmp_path / "conc.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=NullPool,
    )
    with engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
        conn.commit()
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(User(id=1, username="chairman", hashed_password="x", role="Chairman", is_active=True))
    bu = BusinessUnit(id=1, code="BU1", name="Shop One", nature="retail")
    session.add(bu)
    session.commit()
    provision_bu(1, "retail", CHAIRMAN, session)
    session.commit()
    session.test_engine = engine
    yield session
    session.close()
    engine.dispose()


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


def _posted_journals(session, bu_id=1):
    return session.query(JournalEntry).filter(
        JournalEntry.business_unit_id == bu_id, JournalEntry.status == "posted").all()


class TestConcurrentReceivePo:
    def test_two_simultaneous_receives_post_exactly_one_journal(self, file_db):
        db, engine = file_db, file_db.test_engine
        supplier = Supplier(name="Vendor", business_unit_id=1)
        db.add(supplier)
        db.commit()
        # Under the 1,000,000 two-stage threshold: no approval stage, so a
        # plain draft PO can be received straight away.
        po = create_po(POIn(business_unit_id=1, supplier_id=supplier.id,
                            order_date=date(2026, 10, 20),
                            lines=[POLineIn(item_name="Generator", quantity=1,
                                            unit_cost=100000)]),
                       user=CHAIRMAN, db=db)
        po_id = po["id"]

        outcomes = _run_two(engine, lambda s: receive_po(po_id, user=CHAIRMAN, db=s))
        assert [kind for kind, _ in outcomes].count("ok") == 1, outcomes
        assert [kind for kind, _ in outcomes].count("error") == 1, outcomes

        fresh = sessionmaker(bind=engine)()
        try:
            assert len(_posted_journals(fresh)) == 1
            item = fresh.query(InventoryItem).filter(
                InventoryItem.business_unit_id == 1, InventoryItem.name == "Generator").one()
            assert float(item.current_stock) == 1  # received once, not twice
            assert fresh.query(StockMovement).count() == 1
        finally:
            fresh.close()

    def test_two_simultaneous_payments_post_exactly_one_payment(self, file_db):
        db, engine = file_db, file_db.test_engine
        cust = create_sales_customer(CustomerIn(business_unit_id=1, name="Buyer"),
                                     user=CHAIRMAN, db=db)
        inv = create_invoice(InvoiceIn(
            business_unit_id=1, customer_id=cust["id"], invoice_date=date(2026, 10, 5),
            tax_rate=0, lines=[InvoiceLineIn(description="Sale", quantity=1, unit_price=200000)]),
            user=CHAIRMAN, db=db)
        send_invoice(inv["id"], user=CHAIRMAN, db=db)
        inv_id = inv["id"]

        outcomes = _run_two(
            engine,
            lambda s: record_payment(inv_id,
                                     PaymentIn(amount=200000, payment_date=date(2026, 10, 7)),
                                     user=CHAIRMAN, db=s))
        assert [kind for kind, _ in outcomes].count("ok") == 1, outcomes
        assert [kind for kind, _ in outcomes].count("error") == 1, outcomes

        fresh = sessionmaker(bind=engine)()
        try:
            assert fresh.query(InvoicePayment).filter(
                InvoicePayment.invoice_id == inv_id).count() == 1
            # One payment journal (entry_date = payment date); the send
            # journal (invoice date) is separate and must still be exactly one.
            payment_entries = fresh.query(JournalEntry).filter(
                JournalEntry.source == "invoice", JournalEntry.source_id == inv_id,
                JournalEntry.entry_date == date(2026, 10, 7),
                JournalEntry.status == "posted").all()
            assert len(payment_entries) == 1
            send_entries = fresh.query(JournalEntry).filter(
                JournalEntry.source == "invoice", JournalEntry.source_id == inv_id,
                JournalEntry.entry_date == date(2026, 10, 5),
                JournalEntry.status == "posted").all()
            assert len(send_entries) == 1
        finally:
            fresh.close()
