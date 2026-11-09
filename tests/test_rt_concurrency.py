"""Regression tests for the BOT real-time risk review, Workstream A
(concurrency & data integrity): CC-1, CC-2, CM-1..CM-9, Cm-1..Cm-8,
plus SM-15, SM-12, Sm-1.

Two fixture styles, copied from the existing suites:
- `db`: in-memory SQLite, two BUs provisioned (retail + distribution),
  for sequential behaviour (tests/test_erp_completion.py pattern).
- `file_db`: file-backed SQLite in WAL mode so two threads can write
  in turn, with the `_run_two` barrier helper (tests/test_sec_concurrency.py
  T-1 pattern).

Run with: ENVIRONMENT=test DATABASE_URL=<redacted> .venv/bin/python -m pytest tests/test_rt_concurrency.py
"""
import threading
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from src.accounting_templates import template_accounts
from src.models import (
    Account, Base, BusinessUnit, InventoryItem, ItemSerial, JournalEntry,
    PosSale, SalesInvoice, SalesReturn, SalesReturnLine,
    StaffAdvance, StockBatch, Supplier, User,
)
from src.routers.accounting import (
    JournalIn, JournalLineIn, JournalRetryIn, PeriodAction, approve_journal,
    close_period, create_entry, create_journal, post_journal, reopen_period,
    retry_journal, year_close,
)
from src.routers.modules import (
    BatchIn, LeaseIn, SellUnitIn, StockTransferIn, TransferLineIn,
    bill_lease, create_lease, create_stock_transfer,
    create_unit, receive_batch, receive_stock_transfer, sell_unit,
    send_stock_transfer,
)
from src.routers.ops import (
    AdvanceIn, CustomerIn, InvoiceIn, InvoiceLineIn, MovementIn, POIn,
    POLineIn, POUpdate, PaymentIn, PayrollCreate, PayrollLineUpdate,
    PosLineIn, PosSaleIn, SerialAddIn, SupplierIn, add_serials,
    approve_advance, approve_payroll, create_advance, create_employee,
    create_invoice, create_payroll, create_pos_sale, create_po,
    create_sales_customer, create_supplier, pay_payroll, pay_po,
    record_movement, record_payment, receive_po,
    send_invoice, transfer_stock, update_payroll_line, update_po, void_pos_sale,
    EmployeeIn, TransferIn,
)
from src.utils import today_local

CHAIRMAN = SimpleNamespace(id=1, role="Chairman", business_unit_id=None, username="chairman")
HO = SimpleNamespace(id=2, role="HO", business_unit_id=None)
ACCOUNTANT = SimpleNamespace(id=77, role="Accountant", business_unit_id=None)


def _provision(session, bu, template):
    id_by_code = {}
    for code, name, acc_type, parent_code, is_header in template_accounts(template):
        acc = Account(business_unit_id=bu.id, code=code, name=name,
                      account_type=acc_type, is_header=is_header, is_system=True,
                      parent_id=id_by_code.get(parent_code) if parent_code else None)
        session.add(acc)
        session.flush()
        id_by_code[code] = acc.id
    session.commit()


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    bu1 = BusinessUnit(name="Shop", code="S1")
    bu2 = BusinessUnit(name="Warehouse", code="W1")
    session.add_all([bu1, bu2])
    session.commit()
    _provision(session, bu1, "retail")
    _provision(session, bu2, "distribution")
    session.test_bu = bu1
    session.test_bu2 = bu2
    yield session
    session.close()


@pytest.fixture()
def file_db(tmp_path):
    """File-backed SQLite DB with WAL so two threads can write in turn."""
    from src.routers.accounting import provision_bu
    db_path = tmp_path / "rt-conc.db"
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
    session.add(User(id=1, username="chairman", hashed_password="<redacted>", role="Chairman", is_active=True))
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


def _fresh(engine):
    return sessionmaker(bind=engine)()


def _expect_409(fn):
    """Run fn(); assert it raises HTTPException 409; roll the session
    back is the caller's job (the session is passed in via closure)."""
    with pytest.raises(HTTPException) as exc:
        fn()
    assert exc.value.status_code == 409, f"expected 409, got {exc.value.status_code}: {exc.value.detail}"
    return exc.value


def _add_item(db, bu_id, name="Phone", stock=20, cost=500, requires_expiry=False):
    item = InventoryItem(business_unit_id=bu_id, name=name,
                         current_stock=Decimal(str(stock)), unit_cost=Decimal(str(cost)),
                         requires_expiry=requires_expiry)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _stock_of(db, item_id):
    return float(db.query(InventoryItem).filter(InventoryItem.id == item_id).first().current_stock)


def _make_supplier(db):
    return create_supplier(SupplierIn(name="Vendor", business_unit_id=None), user=CHAIRMAN, db=db)


def _make_po(db, bu_id, supplier_id, item=None, qty=2, cost=100):
    lines = [POLineIn(item_name="Phone", inventory_item_id=item.id if item else None,
                      quantity=qty, unit_cost=cost)]
    return create_po(POIn(business_unit_id=bu_id, supplier_id=supplier_id,
                          order_date=date(2026, 10, 20), lines=lines),
                     user=CHAIRMAN, db=db)


def _make_customer(db, bu_id, name="Buyer"):
    return create_sales_customer(CustomerIn(business_unit_id=bu_id, name=name),
                                 user=CHAIRMAN, db=db)


def _make_invoice(db, bu_id, customer_id, total=100000, serial_nos=None, item_id=None):
    return create_invoice(InvoiceIn(
        business_unit_id=bu_id, customer_id=customer_id,
        invoice_date=date(2026, 10, 20), tax_rate=0,
        lines=[InvoiceLineIn(description="Goods", quantity=1, unit_price=total,
                             inventory_item_id=item_id, serial_nos=serial_nos)],
    ), user=CHAIRMAN, db=db)


def _account_id(db, bu_id, code):
    return db.query(Account).filter(Account.business_unit_id == bu_id,
                                    Account.code == code).first().id


def _journal_lines(db, bu_id):
    return [{"account_id": _account_id(db, bu_id, "1110"), "debit": 100000, "credit": 0},
            {"account_id": _account_id(db, bu_id, "4100"), "debit": 0, "credit": 100000}]


# ------------------------------------------------------------------ CM-3: the five money guards

class TestCm3SequentialSecondCallIs409:
    def test_second_receive_po_is_409(self, db):
        bu = db.test_bu
        sup = _make_supplier(db)
        po = _make_po(db, bu.id, sup["id"])
        receive_po(po["id"], user=CHAIRMAN, db=db)
        _expect_409(lambda: receive_po(po["id"], user=CHAIRMAN, db=db))
        db.rollback()

    def test_second_pay_po_is_409(self, db):
        bu = db.test_bu
        sup = _make_supplier(db)
        po = _make_po(db, bu.id, sup["id"])
        receive_po(po["id"], user=CHAIRMAN, db=db)
        pay_po(po["id"], user=CHAIRMAN, db=db)
        _expect_409(lambda: pay_po(po["id"], user=CHAIRMAN, db=db))
        db.rollback()

    def test_second_send_invoice_is_409(self, db):
        bu = db.test_bu
        cust = _make_customer(db, bu.id)
        inv = _make_invoice(db, bu.id, cust["id"])
        send_invoice(inv["id"], user=CHAIRMAN, db=db)
        _expect_409(lambda: send_invoice(inv["id"], user=CHAIRMAN, db=db))
        db.rollback()

    def test_second_record_payment_is_409(self, db):
        bu = db.test_bu
        cust = _make_customer(db, bu.id)
        inv = _make_invoice(db, bu.id, cust["id"])
        send_invoice(inv["id"], user=CHAIRMAN, db=db)
        record_payment(inv["id"], PaymentIn(amount=100000, payment_date=date(2026, 10, 21)),
                       user=CHAIRMAN, db=db)
        _expect_409(lambda: record_payment(
            inv["id"], PaymentIn(amount=100000, payment_date=date(2026, 10, 21)),
            user=CHAIRMAN, db=db))
        db.rollback()

    def test_second_approve_advance_is_409(self, db):
        bu = db.test_bu
        emp = create_employee(EmployeeIn(business_unit_id=bu.id, name="Aung",
                                         base_salary=500000), user=CHAIRMAN, db=db)
        adv = create_advance(AdvanceIn(employee_id=emp["id"], amount=100000,
                                       monthly_deduction=50000), user=CHAIRMAN, db=db)
        approve_advance(adv["id"], user=CHAIRMAN, db=db)
        _expect_409(lambda: approve_advance(adv["id"], user=CHAIRMAN, db=db))
        db.rollback()


class TestCm3ThreadedGuards:
    def test_two_simultaneous_receives_post_exactly_one_journal(self, file_db):
        db, engine = file_db, file_db.test_engine
        sup = Supplier(name="Vendor", business_unit_id=1)
        db.add(sup)
        db.commit()
        po = create_po(POIn(business_unit_id=1, supplier_id=sup.id,
                            order_date=date(2026, 10, 20),
                            lines=[POLineIn(item_name="Generator", quantity=1,
                                            unit_cost=100000)]),
                       user=CHAIRMAN, db=db)
        outcomes = _run_two(engine, lambda s: receive_po(po["id"], user=CHAIRMAN, db=s))
        oks = [o for o in outcomes if o[0] == "ok"]
        errs = [o for o in outcomes if o[0] == "error"]
        assert len(oks) == 1 and len(errs) == 1
        assert isinstance(errs[0][1], HTTPException) and errs[0][1].status_code == 409
        check = _fresh(engine)
        journals = check.query(JournalEntry).filter(
            JournalEntry.business_unit_id == 1, JournalEntry.source == "purchase",
            JournalEntry.status == "posted").all()
        assert len(journals) == 1
        check.close()

    def test_two_simultaneous_payments_post_exactly_one_journal(self, file_db):
        db, engine = file_db, file_db.test_engine
        cust = create_sales_customer(CustomerIn(business_unit_id=1, name="Buyer"),
                                     user=CHAIRMAN, db=db)
        inv = create_invoice(InvoiceIn(
            business_unit_id=1, customer_id=cust["id"],
            invoice_date=date(2026, 10, 20), tax_rate=0,
            lines=[InvoiceLineIn(description="Goods", quantity=1, unit_price=100000)],
        ), user=CHAIRMAN, db=db)
        send_invoice(inv["id"], user=CHAIRMAN, db=db)
        outcomes = _run_two(
            engine,
            lambda s: record_payment(inv["id"], PaymentIn(amount=100000, payment_date=date(2026, 10, 21)),
                                     user=CHAIRMAN, db=s))
        oks = [o for o in outcomes if o[0] == "ok"]
        errs = [o for o in outcomes if o[0] == "error"]
        assert len(oks) == 1 and len(errs) == 1
        assert isinstance(errs[0][1], HTTPException) and errs[0][1].status_code == 409
        check = _fresh(engine)
        payments = check.query(SalesInvoice).filter(SalesInvoice.id == inv["id"]).first()
        assert payments.status == "paid"
        check.close()


# ------------------------------------------------------------------ CM-6 idempotency

class TestCm6Idempotency:
    def test_receive_po_replay_returns_stored_response_once(self, db):
        bu = db.test_bu
        sup = _make_supplier(db)
        item = _add_item(db, bu.id, stock=10, cost=100)
        po = _make_po(db, bu.id, sup["id"], item=item, qty=2, cost=100)
        out1 = receive_po(po["id"], user=CHAIRMAN, db=db, idempotency_key="recv-1")
        out2 = receive_po(po["id"], user=CHAIRMAN, db=db, idempotency_key="recv-1")
        assert out1["id"] == out2["id"] == po["id"]
        assert out1["status"] == out2["status"] == "received"
        assert _stock_of(db, item.id) == 12  # stock moved exactly once
        journals = db.query(JournalEntry).filter(
            JournalEntry.source == "purchase", JournalEntry.source_id == po["id"],
            JournalEntry.status == "posted").all()
        assert len(journals) == 1

    def test_different_keys_are_different_requests(self, db):
        bu = db.test_bu
        sup = _make_supplier(db)
        po = _make_po(db, bu.id, sup["id"])
        receive_po(po["id"], user=CHAIRMAN, db=db, idempotency_key="key-a")
        # A different key is a fresh request on an already-received PO:
        # the CM-3 guard answers, not the idempotency store.
        _expect_409(lambda: receive_po(po["id"], user=CHAIRMAN, db=db,
                                        idempotency_key="key-b"))
        db.rollback()

# ------------------------------------------------------------------ CM-1: void + returns

class TestCm1VoidAndReturns:
    def test_second_void_pos_sale_is_409_and_stock_restored_once(self, db):
        bu = db.test_bu
        item = _add_item(db, bu.id, stock=20, cost=500)
        sale = create_pos_sale(PosSaleIn(
            business_unit_id=bu.id, payment_method="cash",
            lines=[PosLineIn(inventory_item_id=item.id, quantity=4, unit_price=1000)],
        ), user=CHAIRMAN, db=db)
        assert _stock_of(db, item.id) == 16
        out = void_pos_sale(sale["id"], user=CHAIRMAN, db=db)
        assert out["status"] == "void"
        assert _stock_of(db, item.id) == 20
        _expect_409(lambda: void_pos_sale(sale["id"], user=CHAIRMAN, db=db))
        db.rollback()
        assert _stock_of(db, item.id) == 20  # no second restock

    def test_two_simultaneous_voids_restore_stock_once(self, file_db):
        db, engine = file_db, file_db.test_engine
        item = InventoryItem(business_unit_id=1, name="Phone",
                             current_stock=Decimal("20"), unit_cost=Decimal("500"))
        db.add(item)
        db.commit()
        sale = create_pos_sale(PosSaleIn(
            business_unit_id=1, payment_method="cash",
            lines=[PosLineIn(inventory_item_id=item.id, quantity=4, unit_price=1000)],
        ), user=CHAIRMAN, db=db)
        outcomes = _run_two(engine, lambda s: void_pos_sale(sale["id"], user=CHAIRMAN, db=s))
        oks = [o for o in outcomes if o[0] == "ok"]
        errs = [o for o in outcomes if o[0] == "error"]
        assert len(oks) == 1 and len(errs) == 1
        assert isinstance(errs[0][1], HTTPException) and errs[0][1].status_code == 409
        check = _fresh(engine)
        assert float(check.query(InventoryItem).filter(
            InventoryItem.id == item.id).first().current_stock) == 20
        check.close()


# ------------------------------------------------------------------ CM-4: journal + payroll claims

class TestCm4JournalAndPayrollClaims:
    def test_second_post_journal_is_409(self, db):
        bu = db.test_bu
        e = create_journal(JournalIn(
            business_unit_id=bu.id, entry_date=date(2026, 10, 20),
            description="manual", lines=[
                JournalLineIn(account_id=_account_id(db, bu.id, "1110"), debit=1000, credit=0),
                JournalLineIn(account_id=_account_id(db, bu.id, "4100"), debit=0, credit=1000),
            ]), user=CHAIRMAN, db=db)
        post_journal(e["id"], user=CHAIRMAN, db=db)
        _expect_409(lambda: post_journal(e["id"], user=CHAIRMAN, db=db))
        db.rollback()

    def test_second_approve_journal_is_409(self, db):
        bu = db.test_bu
        # An Accountant-written entry over the 1,000,000 threshold needs
        # Chairman/HO approval before it can post.
        e = create_journal(JournalIn(
            business_unit_id=bu.id, entry_date=date(2026, 10, 20),
            description="large manual", lines=[
                JournalLineIn(account_id=_account_id(db, bu.id, "1110"), debit=2000000, credit=0),
                JournalLineIn(account_id=_account_id(db, bu.id, "4100"), debit=0, credit=2000000),
            ]), user=ACCOUNTANT, db=db)
        assert e["approval_status"] == "pending"
        approve_journal(e["id"], user=CHAIRMAN, db=db)
        _expect_409(lambda: approve_journal(e["id"], user=CHAIRMAN, db=db))
        db.rollback()

    def test_second_approve_payroll_is_409(self, db):
        bu = db.test_bu
        create_employee(EmployeeIn(business_unit_id=bu.id, name="Aung",
                                   base_salary=500000), user=CHAIRMAN, db=db)
        run = create_payroll(PayrollCreate(business_unit_id=bu.id, period="2026-10"),
                             user=CHAIRMAN, db=db)
        approve_payroll(run["id"], user=CHAIRMAN, db=db)
        _expect_409(lambda: approve_payroll(run["id"], user=CHAIRMAN, db=db))
        db.rollback()

    def test_duplicate_receive_batch_sequential_400(self, db):
        bu = db.test_bu
        item = _add_item(db, bu.id, stock=0, cost=10, requires_expiry=True)
        receive_batch(item.id, BatchIn(batch_no="B1", quantity=5, unit_cost=10),
                      user=CHAIRMAN, db=db)
        with pytest.raises(HTTPException) as exc:
            receive_batch(item.id, BatchIn(batch_no="B1", quantity=5, unit_cost=10),
                          user=CHAIRMAN, db=db)
        assert exc.value.status_code == 400
        db.rollback()
        assert db.query(StockBatch).filter(StockBatch.item_id == item.id).count() == 1

    def test_two_simultaneous_receive_batch_creates_one_batch(self, file_db):
        db, engine = file_db, file_db.test_engine
        item = InventoryItem(business_unit_id=1, name="Milk",
                             current_stock=Decimal("0"), unit_cost=Decimal("10"),
                             requires_expiry=True)
        db.add(item)
        db.commit()
        # Capture the id on the main thread: reading an attribute of an
        # ORM object bound to the fixture session from two worker threads
        # would use that session concurrently (InvalidRequestError).
        item_id = item.id
        outcomes = _run_two(
            engine,
            lambda s: receive_batch(item_id, BatchIn(batch_no="B1", quantity=5, unit_cost=10),
                                    user=CHAIRMAN, db=s))
        oks = [o for o in outcomes if o[0] == "ok"]
        errs = [o for o in outcomes if o[0] == "error"]
        assert len(oks) == 1 and len(errs) == 1
        assert isinstance(errs[0][1], HTTPException) and errs[0][1].status_code in (400, 409)
        check = _fresh(engine)
        assert check.query(StockBatch).filter(StockBatch.item_id == item_id).count() == 1
        check.close()


# ------------------------------------------------------------------ CC-2 / CM-4: property sell + lease + bill

class TestPropertySellLeaseBill:
    def test_second_sell_unit_is_409_and_one_invoice(self, db):
        bu = db.test_bu
        from src.routers.modules import UnitIn
        unit = create_unit(UnitIn(business_unit_id=bu.id, development="Sky Tower",
                                  unit_no="12A", asking_price=100000000),
                           user=CHAIRMAN, db=db)
        buyer = _make_customer(db, bu.id)
        sell_unit(unit["id"], SellUnitIn(customer_id=buyer["id"]), user=CHAIRMAN, db=db)
        _expect_409(lambda: sell_unit(unit["id"], SellUnitIn(customer_id=buyer["id"]),
                                       user=CHAIRMAN, db=db))
        db.rollback()
        assert db.query(SalesInvoice).filter(
            SalesInvoice.business_unit_id == bu.id).count() == 1

    def test_two_simultaneous_sells_create_one_invoice(self, file_db):
        db, engine = file_db, file_db.test_engine
        from src.routers.modules import UnitIn
        unit = create_unit(UnitIn(business_unit_id=1, development="Sky Tower",
                                  unit_no="12A", asking_price=100000000),
                           user=CHAIRMAN, db=db)
        buyer = create_sales_customer(CustomerIn(business_unit_id=1, name="Buyer"),
                                      user=CHAIRMAN, db=db)
        outcomes = _run_two(
            engine,
            lambda s: sell_unit(unit["id"], SellUnitIn(customer_id=buyer["id"]),
                                user=CHAIRMAN, db=s))
        oks = [o for o in outcomes if o[0] == "ok"]
        errs = [o for o in outcomes if o[0] == "error"]
        assert len(oks) == 1 and len(errs) == 1
        assert isinstance(errs[0][1], HTTPException) and errs[0][1].status_code in (400, 409)
        check = _fresh(engine)
        assert check.query(SalesInvoice).filter(
            SalesInvoice.business_unit_id == 1).count() == 1
        check.close()

    def test_second_create_lease_sequential_400(self, db):
        # Pinned by test_mc16: the sequential second lease is a 400.
        bu = db.test_bu
        from src.routers.modules import UnitIn
        unit = create_unit(UnitIn(business_unit_id=bu.id, unit_no="3B",
                                  asking_price=50000000), user=CHAIRMAN, db=db)
        buyer = _make_customer(db, bu.id)
        create_lease(LeaseIn(business_unit_id=bu.id, unit_id=unit["id"],
                             tenant_id=buyer["id"], monthly_rent=800000,
                             start_date=date(2026, 10, 1)), user=CHAIRMAN, db=db)
        with pytest.raises(HTTPException) as exc:
            create_lease(LeaseIn(business_unit_id=bu.id, unit_id=unit["id"],
                                 tenant_id=buyer["id"], monthly_rent=800000,
                                 start_date=date(2026, 10, 1)), user=CHAIRMAN, db=db)
        assert exc.value.status_code == 400
        db.rollback()

    def test_second_bill_lease_is_409_and_one_invoice(self, db):
        bu = db.test_bu
        from src.routers.modules import UnitIn
        unit = create_unit(UnitIn(business_unit_id=bu.id, unit_no="3B",
                                  asking_price=50000000), user=CHAIRMAN, db=db)
        buyer = _make_customer(db, bu.id)
        lease = create_lease(LeaseIn(business_unit_id=bu.id, unit_id=unit["id"],
                                     tenant_id=buyer["id"], monthly_rent=800000,
                                     start_date=date(2026, 10, 1)), user=CHAIRMAN, db=db)
        bill_lease(lease["id"], period="2026-10", user=CHAIRMAN, db=db)
        # Sequential second bill: the 400 pre-check (the claim is the
        # concurrent backstop — see the threaded test below).
        with pytest.raises(HTTPException) as exc:
            bill_lease(lease["id"], period="2026-10", user=CHAIRMAN, db=db)
        assert exc.value.status_code == 400
        db.rollback()
        assert db.query(SalesInvoice).filter(
            SalesInvoice.business_unit_id == bu.id).count() == 1

    def test_two_simultaneous_bill_lease_create_one_invoice(self, file_db):
        db, engine = file_db, file_db.test_engine
        from src.routers.modules import UnitIn
        unit = create_unit(UnitIn(business_unit_id=1, unit_no="3B",
                                  asking_price=50000000), user=CHAIRMAN, db=db)
        buyer = create_sales_customer(CustomerIn(business_unit_id=1, name="Buyer"),
                                      user=CHAIRMAN, db=db)
        lease = create_lease(LeaseIn(business_unit_id=1, unit_id=unit["id"],
                                     tenant_id=buyer["id"], monthly_rent=800000,
                                     start_date=date(2026, 10, 1)), user=CHAIRMAN, db=db)
        outcomes = _run_two(
            engine,
            lambda s: bill_lease(lease["id"], period="2026-10", user=CHAIRMAN, db=s))
        oks = [o for o in outcomes if o[0] == "ok"]
        errs = [o for o in outcomes if o[0] == "error"]
        assert len(oks) == 1 and len(errs) == 1
        assert isinstance(errs[0][1], HTTPException) and errs[0][1].status_code in (400, 409)
        check = _fresh(engine)
        assert check.query(SalesInvoice).filter(
            SalesInvoice.business_unit_id == 1).count() == 1
        check.close()


# ------------------------------------------------------------------ CM-8: optimistic locking + movement deltas

class TestCm8VersionsAndDeltas:
    def test_update_po_stale_version_409_correct_version_ok(self, db):
        bu = db.test_bu
        sup = _make_supplier(db)
        po = _make_po(db, bu.id, sup["id"])
        assert po["version"] == 0
        _expect_409(lambda: update_po(po["id"], POUpdate(notes="x", expected_version=99),
                                       user=CHAIRMAN, db=db))
        db.rollback()
        out = update_po(po["id"], POUpdate(notes="x", expected_version=0),
                        user=CHAIRMAN, db=db)
        assert out["version"] == 1 and out["notes"] == "x"

    def test_update_payroll_line_stale_version_409(self, db):
        bu = db.test_bu
        create_employee(EmployeeIn(business_unit_id=bu.id, name="Aung",
                                   base_salary=500000), user=CHAIRMAN, db=db)
        run = create_payroll(PayrollCreate(business_unit_id=bu.id, period="2026-10"),
                             user=CHAIRMAN, db=db)
        line = run["lines"][0]
        assert run["version"] == 0
        _expect_409(lambda: update_payroll_line(
            run["id"], line["id"], PayrollLineUpdate(deductions=1000, expected_version=7),
            user=CHAIRMAN, db=db))
        db.rollback()
        out = update_payroll_line(
            run["id"], line["id"], PayrollLineUpdate(deductions=1000, expected_version=0),
            user=CHAIRMAN, db=db)
        assert out["version"] == 1

    def test_movement_delta_applies_on_top_of_current_stock(self, db):
        bu = db.test_bu
        item = _add_item(db, bu.id, stock=10, cost=100)
        record_movement(item.id, MovementIn(movement_type="adjustment", quantity=0,
                                            delta=5, reason="stocktake"),
                        user=CHAIRMAN, db=db)
        assert _stock_of(db, item.id) == 15
        record_movement(item.id, MovementIn(movement_type="adjustment", quantity=0,
                                            delta=-3, reason="damage"),
                        user=CHAIRMAN, db=db)
        assert _stock_of(db, item.id) == 12

    def test_movement_expected_stock_mismatch_is_409(self, db):
        bu = db.test_bu
        item = _add_item(db, bu.id, stock=10, cost=100)
        _expect_409(lambda: record_movement(
            item.id, MovementIn(movement_type="adjustment", quantity=20,
                                expected_stock=99, reason="set"),
            user=CHAIRMAN, db=db))
        db.rollback()
        assert _stock_of(db, item.id) == 10
        record_movement(item.id, MovementIn(movement_type="adjustment", quantity=20,
                                            expected_stock=10, reason="set"),
                        user=CHAIRMAN, db=db)
        assert _stock_of(db, item.id) == 20


# ------------------------------------------------------------------ CM-9: serial claim

class TestCm9SerialClaim:
    def test_same_serial_cannot_be_sold_twice(self, db):
        bu = db.test_bu
        item = _add_item(db, bu.id, stock=5, cost=100)
        add_serials(item.id, SerialAddIn(serial_nos=["SN-1", "SN-2"]),
                    user=CHAIRMAN, db=db)
        cust = _make_customer(db, bu.id)
        inv1 = _make_invoice(db, bu.id, cust["id"], total=1000,
                             serial_nos=["SN-1"], item_id=item.id)
        send_invoice(inv1["id"], user=CHAIRMAN, db=db)
        inv2 = _make_invoice(db, bu.id, cust["id"], total=1000,
                             serial_nos=["SN-1"], item_id=item.id)
        # Sequential second sell of the same serial: the friendly read
        # path answers 400 ("not in stock"); the 409 claim is the
        # concurrent backstop (two sends racing before either commits).
        with pytest.raises(HTTPException) as exc:
            send_invoice(inv2["id"], user=CHAIRMAN, db=db)
        assert exc.value.status_code == 400
        db.rollback()
        check = db.query(SalesInvoice).filter(SalesInvoice.id == inv2["id"]).first()
        assert check.status == "draft"
        serial = db.query(ItemSerial).filter(ItemSerial.serial_no == "SN-1").first()
        assert serial.status == "sold" and serial.sales_invoice_id == inv1["id"]


# ------------------------------------------------------------------ SM-15: transfer receipt scope

class TestSm15TransferReceiptScope:
    def test_source_bu_cannot_receive_destination_bu_can(self, db):
        bu1, bu2 = db.test_bu, db.test_bu2
        src_ceo = SimpleNamespace(id=10, role="CEO", business_unit_id=bu1.id)
        dst_ceo = SimpleNamespace(id=11, role="CEO", business_unit_id=bu2.id)
        item = _add_item(db, bu1.id, name="Rice", stock=10, cost=100)
        t = create_stock_transfer(StockTransferIn(
            from_business_unit_id=bu1.id, to_business_unit_id=bu2.id,
            lines=[TransferLineIn(inventory_item_id=item.id, quantity=4)]),
            user=src_ceo, db=db)
        send_stock_transfer(t["id"], user=src_ceo, db=db)
        with pytest.raises(HTTPException) as exc:
            receive_stock_transfer(t["id"], user=src_ceo, db=db)
        assert exc.value.status_code == 403
        db.rollback()
        out = receive_stock_transfer(t["id"], user=dst_ceo, db=db)
        assert out["status"] == "received"
        dst_item = db.query(InventoryItem).filter(
            InventoryItem.business_unit_id == bu2.id, InventoryItem.name == "Rice").first()
        assert dst_item is not None
        assert _stock_of(db, dst_item.id) == 4


# ------------------------------------------------------------------ Cm-8: advance deduction across runs

class TestCm8AdvanceDeductionRemainder:
    def test_second_payroll_run_deducts_only_the_remainder(self, db):
        bu = db.test_bu
        emp = create_employee(EmployeeIn(business_unit_id=bu.id, name="Aung Min",
                                         base_salary=500000), user=CHAIRMAN, db=db)
        adv = create_advance(AdvanceIn(employee_id=emp["id"], amount=150000,
                                       monthly_deduction=100000), user=CHAIRMAN, db=db)
        approve_advance(adv["id"], user=CHAIRMAN, db=db)
        run1 = create_payroll(PayrollCreate(business_unit_id=bu.id, period="2026-10"),
                              user=CHAIRMAN, db=db)
        approve_payroll(run1["id"], user=CHAIRMAN, db=db)
        run1 = pay_payroll(run1["id"], user=CHAIRMAN, db=db)
        assert run1["lines"][0]["advance_deduction"] == 100000
        run2 = create_payroll(PayrollCreate(business_unit_id=bu.id, period="2026-11"),
                              user=CHAIRMAN, db=db)
        approve_payroll(run2["id"], user=CHAIRMAN, db=db)
        run2 = pay_payroll(run2["id"], user=CHAIRMAN, db=db)
        assert run2["lines"][0]["advance_deduction"] == 50000  # remainder only
        adv_now = db.query(StaffAdvance).first()
        assert float(adv_now.remaining_amount) == 0 and adv_now.status == "settled"

# ------------------------------------------------------------------ CM-5 / Cm-6 / Cm-7: periods + year close

class TestPeriodLocks:
    def test_close_with_draft_is_400_then_post_into_closed_is_400(self, db):
        bu = db.test_bu
        e = create_journal(JournalIn(
            business_unit_id=bu.id, entry_date=date(2026, 9, 10),
            description="draft", lines=[
                JournalLineIn(account_id=_account_id(db, bu.id, "1110"), debit=1000, credit=0),
                JournalLineIn(account_id=_account_id(db, bu.id, "4100"), debit=0, credit=1000),
            ]), user=CHAIRMAN, db=db)
        with pytest.raises(HTTPException) as exc:
            close_period(PeriodAction(business_unit_id=bu.id, period="2026-09"),
                         user=CHAIRMAN, db=db)
        assert exc.value.status_code == 400
        db.rollback()
        post_journal(e["id"], user=CHAIRMAN, db=db)
        out = close_period(PeriodAction(business_unit_id=bu.id, period="2026-09"),
                           user=CHAIRMAN, db=db)
        assert out["status"] == "closed"
        with pytest.raises(HTTPException) as exc:
            create_entry(db, bu.id, date(2026, 9, 15), "late", "manual", 1,
                         _journal_lines(db, bu.id), post=True)
        assert exc.value.status_code == 400
        db.rollback()

    def test_reopen_allows_posting_again(self, db):
        bu = db.test_bu
        close_period(PeriodAction(business_unit_id=bu.id, period="2026-09"),
                     user=CHAIRMAN, db=db)
        reopen_period(PeriodAction(business_unit_id=bu.id, period="2026-09"),
                      user=CHAIRMAN, db=db)
        e = create_entry(db, bu.id, date(2026, 9, 15), "after reopen", "manual", 1,
                         _journal_lines(db, bu.id), post=True)
        db.commit()
        assert e.status == "posted"

    def test_void_into_closed_period_is_409_with_reopen_guidance(self, db):
        # Cm-6: a reversal into a closed period is a 409 naming the
        # period and the reopen step, not a bare 400.
        bu = db.test_bu
        item = _add_item(db, bu.id, stock=20, cost=500)
        sale = create_pos_sale(PosSaleIn(
            business_unit_id=bu.id, payment_method="cash",
            sale_date=date(2026, 9, 10),
            lines=[PosLineIn(inventory_item_id=item.id, quantity=4, unit_price=1000)],
        ), user=CHAIRMAN, db=db)
        close_period(PeriodAction(business_unit_id=bu.id, period="2026-09"),
                     user=CHAIRMAN, db=db)
        with pytest.raises(HTTPException) as exc:
            void_pos_sale(sale["id"], user=CHAIRMAN, db=db)
        assert exc.value.status_code == 409
        assert "2026-09" in exc.value.detail and "reopen" in exc.value.detail.lower()
        db.rollback()
        check = db.query(PosSale).filter(PosSale.id == sale["id"]).first()
        assert check.status == "completed"
        assert _stock_of(db, item.id) == 16  # no restock happened

    def test_two_simultaneous_year_closes_post_exactly_one_entry(self, file_db):
        db, engine = file_db, file_db.test_engine
        entry = create_entry(db, 1, date(2026, 6, 15), "sale", "manual", 1,
                             [{"account_id": _account_id(db, 1, "1110"), "debit": 100000, "credit": 0},
                              {"account_id": _account_id(db, 1, "4100"), "debit": 0, "credit": 100000}],
                             post=True)
        db.commit()
        from src.routers.accounting import YearCloseIn
        outcomes = _run_two(
            engine,
            lambda s: year_close(YearCloseIn(business_unit_id=1, year=2026),
                                 user=CHAIRMAN, db=s))
        oks = [o for o in outcomes if o[0] == "ok"]
        errs = [o for o in outcomes if o[0] == "error"]
        assert len(oks) == 1 and len(errs) == 1
        assert isinstance(errs[0][1], HTTPException) and errs[0][1].status_code in (400, 409)
        check = _fresh(engine)
        closes = check.query(JournalEntry).filter(
            JournalEntry.business_unit_id == 1, JournalEntry.source == "year_close",
            JournalEntry.status == "posted").all()
        assert len(closes) == 1
        check.close()


# ------------------------------------------------------------------ CM-7: journal retry endpoint

class TestCm7JournalRetry:
    def test_retry_already_posted_skips(self, db):
        bu = db.test_bu
        e = create_entry(db, bu.id, date(2026, 10, 20), "expense journal", "expense",
                         1, _journal_lines(db, bu.id), source_id=4242, post=True)
        db.commit()
        out = retry_journal(JournalRetryIn(source="expense", source_id=4242),
                            user=CHAIRMAN, db=db)
        assert out["already_posted"] is True and out["entry_no"] == e.entry_no
        assert db.query(JournalEntry).filter(
            JournalEntry.source == "expense", JournalEntry.source_id == 4242,
            JournalEntry.status == "posted").count() == 1

    def test_retry_unknown_record_is_404(self, db):
        with pytest.raises(HTTPException) as exc:
            retry_journal(JournalRetryIn(source="pos_sale", source_id=999999),
                          user=CHAIRMAN, db=db)
        assert exc.value.status_code == 404
        db.rollback()
        with pytest.raises(HTTPException) as exc:
            retry_journal(JournalRetryIn(source="bogus_source", source_id=1),
                          user=CHAIRMAN, db=db)
        assert exc.value.status_code == 404
        db.rollback()

    def test_retry_fail_hard_source_is_409(self, db):
        bu = db.test_bu
        cust = _make_customer(db, bu.id)
        inv = _make_invoice(db, bu.id, cust["id"])
        with pytest.raises(HTTPException) as exc:
            retry_journal(JournalRetryIn(source="invoice", source_id=inv["id"]),
                          user=CHAIRMAN, db=db)
        assert exc.value.status_code == 409
        db.rollback()

    def test_retry_quick_transfer_stock_is_409(self, db):
        bu = db.test_bu
        item = _add_item(db, bu.id, stock=10, cost=100)
        with pytest.raises(HTTPException) as exc:
            retry_journal(JournalRetryIn(source="stock_transfer", source_id=item.id),
                          user=CHAIRMAN, db=db)
        assert exc.value.status_code == 409
        db.rollback()

    def test_retry_reposts_a_legacy_sales_return_journal(self, db):
        # A sales return committed without its journal (legacy flag-only
        # row): the retry endpoint posts it and reports journal_posted.
        bu = db.test_bu
        cust = _make_customer(db, bu.id)
        inv = _make_invoice(db, bu.id, cust["id"])
        send_invoice(inv["id"], user=CHAIRMAN, db=db)
        line = db.query(SalesInvoice).filter(SalesInvoice.id == inv["id"]).first().lines[0]
        r = SalesReturn(business_unit_id=bu.id, invoice_id=inv["id"],
                        return_no="SR-1-0001", status="posted",
                        return_date=date(2026, 10, 21), created_by_id=1)
        db.add(r)
        db.flush()
        db.add(SalesReturnLine(return_id=r.id, invoice_line_id=line.id,
                               quantity=Decimal("1"), unit_price=line.unit_price))
        db.commit()
        assert db.query(JournalEntry).filter(
            JournalEntry.source == "sales_return", JournalEntry.source_id == r.id).count() == 0
        out = retry_journal(JournalRetryIn(source="sales_return", source_id=r.id),
                            user=CHAIRMAN, db=db)
        assert out["already_posted"] is False and out["journal_posted"] is True
        assert db.query(JournalEntry).filter(
            JournalEntry.source == "sales_return", JournalEntry.source_id == r.id,
            JournalEntry.status == "posted").count() == 1
        # A second retry is now a skip, not a duplicate posting.
        out2 = retry_journal(JournalRetryIn(source="sales_return", source_id=r.id),
                             user=CHAIRMAN, db=db)
        assert out2["already_posted"] is True


# ------------------------------------------------------------------ Cm-3: entry-number retry

class TestCm3EntryNumberRetry:
    def test_create_entry_retries_number_collision_once(self, db, monkeypatch):
        import src.routers.accounting as acc
        bu = db.test_bu
        lines = _journal_lines(db, bu.id)
        first = acc.create_entry(db, bu.id, date(2026, 10, 20), "first", "manual",
                                 1, lines, post=True)
        db.commit()
        real = acc.next_entry_no
        calls = {"n": 0}

        def _once(db_, bu_id, d):
            calls["n"] += 1
            if calls["n"] == 1:
                return first.entry_no  # duplicate: forces the collision
            return real(db_, bu_id, d)

        monkeypatch.setattr(acc, "next_entry_no", _once)
        second = acc.create_entry(db, bu.id, date(2026, 10, 20), "second", "manual",
                                  1, lines, post=True)
        db.commit()
        assert second.entry_no != first.entry_no
        assert calls["n"] == 2
        assert db.query(JournalEntry).filter(
            JournalEntry.business_unit_id == bu.id,
            JournalEntry.entry_date == date(2026, 10, 20)).count() == 2


# ------------------------------------------------------------------ SM-12 / Sm-1 helpers

class TestSm12AndSm1Helpers:
    def test_today_local_is_yangon_date(self):
        from src.models import _today_local
        assert _today_local() == today_local()

    def test_escape_like_escapes_wildcards(self):
        from src.routers.ops import _escape_like
        assert _escape_like("a_b%c\\d") == "a\\_b\\%c\\\\d"
        assert _escape_like("plain") == "plain"

# ------------------------------------------------------------------ SM-9: quick transfer flag-only case

class TestSm9QuickTransferFlagOnly:
    def test_quick_transfer_to_unprovisioned_bu_moves_stock_and_flags_journal(self, db):
        # The destination BU has no chart at all (no 1140/1180/2180):
        # the stock move itself is real and must not be blocked on
        # bookkeeping provisioning — it commits with journal_posted=false.
        bu1 = db.test_bu
        bare = BusinessUnit(name="Bare", code="B3")
        db.add(bare)
        db.commit()
        item = _add_item(db, bu1.id, name="Soap", stock=30, cost=500)
        out = transfer_stock(item.id, TransferIn(to_business_unit_id=bare.id, quantity=12),
                             user=CHAIRMAN, db=db)
        assert out["source_stock"] == 18
        assert out["journal_posted"] is False
        dst = db.query(InventoryItem).filter(
            InventoryItem.business_unit_id == bare.id, InventoryItem.name == "Soap").first()
        assert dst is not None and float(dst.current_stock) == 12

    def test_quick_transfer_into_closed_period_rolls_back(self, db):
        # Accounts exist in both BUs here, so a posting failure is
        # fail-hard: the whole transfer rolls back instead of moving
        # stock with no journal.
        bu1, bu2 = db.test_bu, db.test_bu2
        close_period(PeriodAction(business_unit_id=bu1.id,
                                  period=today_local().strftime("%Y-%m")),
                     user=CHAIRMAN, db=db)
        item = _add_item(db, bu1.id, name="Soap", stock=30, cost=500)
        with pytest.raises(HTTPException):
            transfer_stock(item.id, TransferIn(to_business_unit_id=bu2.id, quantity=12),
                           user=CHAIRMAN, db=db)
        db.rollback()
        assert _stock_of(db, item.id) == 30
        assert db.query(InventoryItem).filter(
            InventoryItem.business_unit_id == bu2.id, InventoryItem.name == "Soap").first() is None
