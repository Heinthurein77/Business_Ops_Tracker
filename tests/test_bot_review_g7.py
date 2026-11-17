"""Regression tests for the 2026-11-17 review finding G-7 (Minor).

Five create endpoints never read Idempotency-Key, so a lost-response
retry inserted a duplicate row. They now accept and store the key with
the same _idempotency_lookup/_idempotency_store helpers the other
money endpoints use, and the frontend already sends a fresh key on
every call via postJson (verified for all five callers).

Run with: ENVIRONMENT=test DATABASE_URL=<redacted> .venv/bin/python -m pytest tests/test_bot_review_g7.py
"""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.accounting_templates import template_accounts
from src.models import (
    Account, BankAccount, BankTransaction, Base, BusinessUnit, InventoryItem,
    JournalEntry, Quotation, StaffAdvance, StockTransfer,
)
from src.routers.accounting import (
    BankTxnIn, JournalIn, JournalLineIn, add_bank_txn, create_journal,
)
from src.routers.modules import (
    StockTransferIn, TransferLineIn, create_stock_transfer,
)
from src.routers.ops import (
    AdvanceIn, CustomerIn, EmployeeIn, InvoiceLineIn, QuotationIn,
    create_advance, create_employee, create_quotation, create_sales_customer,
)

CHAIRMAN = SimpleNamespace(id=1, role="Chairman", business_unit_id=None, username="chairman")


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


def _account_id(db, bu_id, code):
    return db.query(Account).filter(Account.business_unit_id == bu_id,
                                    Account.code == code).first().id


# ------------------------------------------------------------------ G-7: creates

class TestG7CreateIdempotency:
    def test_create_advance_same_key_replays(self, db):
        bu = db.test_bu
        emp = create_employee(EmployeeIn(business_unit_id=bu.id, name="Aung",
                                         base_salary=500000), user=CHAIRMAN, db=db)
        payload = AdvanceIn(employee_id=emp["id"], amount=100000, monthly_deduction=50000)
        out1 = create_advance(payload, user=CHAIRMAN, db=db, idempotency_key="adv-1")
        out2 = create_advance(payload, user=CHAIRMAN, db=db, idempotency_key="adv-1")
        assert out1["id"] == out2["id"]
        assert db.query(StaffAdvance).filter(
            StaffAdvance.employee_id == emp["id"]).count() == 1

    def test_create_advance_different_key_is_a_new_advance(self, db):
        bu = db.test_bu
        emp = create_employee(EmployeeIn(business_unit_id=bu.id, name="Aung",
                                         base_salary=500000), user=CHAIRMAN, db=db)
        payload = AdvanceIn(employee_id=emp["id"], amount=100000, monthly_deduction=50000)
        create_advance(payload, user=CHAIRMAN, db=db, idempotency_key="adv-a")
        create_advance(payload, user=CHAIRMAN, db=db, idempotency_key="adv-b")
        assert db.query(StaffAdvance).filter(
            StaffAdvance.employee_id == emp["id"]).count() == 2

    def test_add_bank_txn_same_key_replays(self, db):
        bu = db.test_bu
        bank = BankAccount(business_unit_id=bu.id, bank_name="KBZ")
        db.add(bank)
        db.commit()
        payload = BankTxnIn(txn_date=date(2026, 10, 20), description="deposit",
                            deposit=Decimal("50000"))
        out1 = add_bank_txn(bank.id, payload, user=CHAIRMAN, db=db, idempotency_key="bank-1")
        out2 = add_bank_txn(bank.id, payload, user=CHAIRMAN, db=db, idempotency_key="bank-1")
        assert out1["id"] == out2["id"]
        assert db.query(BankTransaction).filter(
            BankTransaction.bank_account_id == bank.id).count() == 1

    def test_create_quotation_same_key_replays(self, db):
        bu = db.test_bu
        cust = create_sales_customer(CustomerIn(business_unit_id=bu.id, name="Buyer"),
                                     user=CHAIRMAN, db=db)
        payload = QuotationIn(
            business_unit_id=bu.id, customer_id=cust["id"],
            quote_date=date(2026, 10, 20), tax_rate=0,
            lines=[InvoiceLineIn(description="Goods", quantity=1, unit_price=100000)],
        )
        out1 = create_quotation(payload, user=CHAIRMAN, db=db, idempotency_key="quo-1")
        out2 = create_quotation(payload, user=CHAIRMAN, db=db, idempotency_key="quo-1")
        assert out1["id"] == out2["id"]
        assert out1["quote_no"] == out2["quote_no"]
        assert db.query(Quotation).filter(
            Quotation.business_unit_id == bu.id).count() == 1

    def test_create_journal_same_key_replays(self, db):
        bu = db.test_bu
        payload = JournalIn(
            business_unit_id=bu.id, entry_date=date(2026, 10, 20),
            description="manual", lines=[
                JournalLineIn(account_id=_account_id(db, bu.id, "1110"), debit=1000, credit=0),
                JournalLineIn(account_id=_account_id(db, bu.id, "4100"), debit=0, credit=1000),
            ])
        out1 = create_journal(payload, user=CHAIRMAN, db=db, idempotency_key="jour-1")
        out2 = create_journal(payload, user=CHAIRMAN, db=db, idempotency_key="jour-1")
        assert out1["id"] == out2["id"]
        assert out1["entry_no"] == out2["entry_no"]
        assert db.query(JournalEntry).filter(
            JournalEntry.business_unit_id == bu.id,
            JournalEntry.source == "manual").count() == 1

    def test_create_stock_transfer_same_key_replays(self, db):
        bu1, bu2 = db.test_bu, db.test_bu2
        item = InventoryItem(business_unit_id=bu1.id, name="Rice",
                             current_stock=Decimal("10"), unit_cost=Decimal("100"))
        db.add(item)
        db.commit()
        payload = StockTransferIn(
            from_business_unit_id=bu1.id, to_business_unit_id=bu2.id,
            lines=[TransferLineIn(inventory_item_id=item.id, quantity=4)])
        out1 = create_stock_transfer(payload, user=CHAIRMAN, db=db, idempotency_key="trf-1")
        out2 = create_stock_transfer(payload, user=CHAIRMAN, db=db, idempotency_key="trf-1")
        assert out1["id"] == out2["id"]
        assert out1["transfer_no"] == out2["transfer_no"]
        assert db.query(StockTransfer).filter(
            StockTransfer.from_business_unit_id == bu1.id).count() == 1
