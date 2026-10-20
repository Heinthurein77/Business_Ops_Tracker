"""Tests for ERP Phase 6 fixed assets + bank reconciliation (in-memory SQLite).

Run with: ENVIRONMENT=test .venv/bin/python -m pytest tests/test_assets_bank.py
"""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.accounting_templates import template_accounts
from src.models import Account, Base, BusinessUnit, JournalEntry
from src.routers.accounting import (
    BankAccountIn, BankTxnIn, DepreciateIn, FixedAssetIn,
    add_bank_txn, bank_reconciliation, create_bank_account,
    create_fixed_asset, run_depreciation,
)

ACCT = SimpleNamespace(id=None, role="Accountant", business_unit_id=None)


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    bu = BusinessUnit(name="Logistics", code="L1")
    session.add(bu)
    session.commit()
    id_by_code = {}
    for code, name, acc_type, parent_code, is_header in template_accounts("logistics"):
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


def balance(db, code):
    acc = db.query(Account).filter(Account.code == code).first()
    total = Decimal("0")
    for e in db.query(JournalEntry).filter(JournalEntry.status == "posted").all():
        for l in e.lines:
            if l.account_id == acc.id:
                total += (l.debit or 0) - (l.credit or 0)
    return total


def test_straight_line_depreciation_and_idempotency(db):
    # 12,000,000 cost, 60-month life -> 200,000/month
    create_fixed_asset(FixedAssetIn(business_unit_id=db.test_bu.id, name="Truck",
                                    purchase_date=date(2026, 1, 15), cost=12000000,
                                    useful_life_months=60), user=ACCT, db=db)
    out = run_depreciation(DepreciateIn(business_unit_id=db.test_bu.id, period="2026-10"),
                           user=ACCT, db=db)
    assert out["assets_depreciated"] == 1
    assert out["total"] == 200000
    assert out["journal_posted"] is True
    assert balance(db, "6900") == 200000
    assert balance(db, "1290") == -200000  # contra-asset: credit balance
    # running the same period again must not double-post
    out2 = run_depreciation(DepreciateIn(business_unit_id=db.test_bu.id, period="2026-10"),
                            user=ACCT, db=db)
    assert out2["assets_depreciated"] == 0
    assert balance(db, "6900") == 200000


def test_bank_reconciliation_difference(db):
    bank = create_bank_account(BankAccountIn(
        business_unit_id=db.test_bu.id, bank_name="KBZ",
        account_id=db.test_ids["1120"]), user=ACCT, db=db)
    add_bank_txn(bank["id"], BankTxnIn(txn_date=date(2026, 10, 5), description="Deposit",
                                      deposit=500000), user=ACCT, db=db)
    add_bank_txn(bank["id"], BankTxnIn(txn_date=date(2026, 10, 6), description="Fees",
                                      withdrawal=5000), user=ACCT, db=db)
    recon = bank_reconciliation(bank["id"], user=ACCT, db=db)
    assert recon["statement_balance"] == 495000
    assert recon["ledger_balance"] == 0  # nothing posted to 1120 yet
    assert recon["difference"] == -495000
    assert recon["unreconciled_count"] == 2
