"""Tests for ERP B/C/D additions: budgets, tax report, currency,
two-stage expense approval, intercompany (in-memory SQLite)."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.accounting_templates import template_accounts
from src.models import (
    Account, Base, BusinessUnit, ExpenseRequest, JournalEntry,
)
from src.routers.accounting import (
    BudgetIn, IntercompanyIn, create_intercompany, finance_summary,
    list_budgets, settle_intercompany, tax_report, upsert_budget,
)
from src.routers.ops import (
    CustomerIn, ExpenseIn, ExpenseReviewIn, InvoiceIn, InvoiceLineIn,
    create_invoice, create_sales_customer, review_expense, send_invoice,
    submit_expense,
)

CHAIRMAN = SimpleNamespace(id=None, role="Chairman", business_unit_id=None)
HO = SimpleNamespace(id=2, role="HO", business_unit_id=None)


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.test_bus = []
    for name, code, tmpl in [("Retail", "R1", "retail"), ("Logistics", "L1", "logistics")]:
        bu = BusinessUnit(name=name, code=code)
        session.add(bu)
        session.commit()
        id_by_code = {}
        for a_code, a_name, acc_type, parent, is_header in template_accounts(tmpl):
            acc = Account(business_unit_id=bu.id, code=a_code, name=a_name,
                          account_type=acc_type, is_header=is_header, is_system=True,
                          parent_id=id_by_code.get(parent) if parent else None)
            session.add(acc)
            session.flush()
            id_by_code[a_code] = acc.id
        session.commit()
        session.test_bus.append(bu)
    yield session
    session.close()


def balance(db, bu_id, code):
    acc = db.query(Account).filter(Account.business_unit_id == bu_id, Account.code == code).first()
    if not acc:
        return None
    total = Decimal("0")
    for e in db.query(JournalEntry).filter(JournalEntry.status == "posted",
                                           JournalEntry.business_unit_id == bu_id).all():
        for l in e.lines:
            if l.account_id == acc.id:
                total += (l.debit or 0) - (l.credit or 0)
    return total


def post_sale(db, bu_id, amount=100000):
    """Draft-free path: invoice -> send (posts with 5% tax)."""
    cust = create_sales_customer(CustomerIn(business_unit_id=bu_id, name="Cust"), user=CHAIRMAN, db=db)
    inv = create_invoice(InvoiceIn(
        business_unit_id=bu_id, customer_id=cust["id"], invoice_date=date(2026, 10, 5),
        tax_rate=5.0, lines=[InvoiceLineIn(description="Goods", quantity=1, unit_price=amount)],
    ), user=CHAIRMAN, db=db)
    send_invoice(inv["id"], user=CHAIRMAN, db=db)
    return inv


# ---------------------------------------------------------------- B5 budgets

def test_budget_variance(db):
    bu = db.test_bus[0]
    post_sale(db, bu.id, 100000)
    upsert_budget(BudgetIn(business_unit_id=bu.id, period="2026-10", account_code="4100", amount=80000),
                  user=CHAIRMAN, db=db)
    out = list_budgets(business_unit_id=bu.id, period="2026-10", user=CHAIRMAN, db=db)
    row = out["items"][0]
    assert row["budget"] == 80000 and row["actual"] == 100000
    assert row["variance"] == 20000 and row["variance_pct"] == 25.0


# ---------------------------------------------------------------- B6 tax

def test_tax_report(db):
    bu = db.test_bus[0]
    post_sale(db, bu.id, 100000)  # tax 5% of 100000 = 5000 charged
    out = tax_report(business_unit_id=bu.id, period="2026-10", user=CHAIRMAN, db=db)
    assert out["tax_charged"] == 5000 and out["closing_balance"] == 5000
    assert out["opening_balance"] == 0


# ---------------------------------------------------------------- B4 summary

def test_finance_summary(db):
    bu = db.test_bus[0]
    post_sale(db, bu.id, 100000)
    out = finance_summary(period="2026-10", user=CHAIRMAN, db=db)
    assert out["has_books"] is True
    assert out["income"] == 100000
    assert out["ar_outstanding"] == 105000  # gross incl. tax, unpaid
    assert out["tax_payable"] == 5000


# ---------------------------------------------------------------- B7 currency

def test_usd_invoice_posts_in_mmk(db):
    bu = db.test_bus[0]
    cust = create_sales_customer(CustomerIn(business_unit_id=bu.id, name="USD Cust"), user=CHAIRMAN, db=db)
    inv = create_invoice(InvoiceIn(
        business_unit_id=bu.id, customer_id=cust["id"], invoice_date=date(2026, 10, 5),
        tax_rate=0, currency="USD", exchange_rate=2000,
        lines=[InvoiceLineIn(description="Export", quantity=1, unit_price=100)],
    ), user=CHAIRMAN, db=db)
    assert inv["currency"] == "USD"
    send_invoice(inv["id"], user=CHAIRMAN, db=db)
    assert balance(db, bu.id, "4100") == Decimal("-200000")  # 100 USD x 2000, credit side
    assert balance(db, bu.id, "1130") == Decimal("200000")


# ---------------------------------------------------------------- C8 approval stages

def _submit(db, amount):
    ceo = SimpleNamespace(id=7, role="CEO", business_unit_id=db.test_bus[0].id)
    return submit_expense(ExpenseIn(title="Big purchase", amount=amount, category="supplies"),
                          user=ceo, db=db)


def test_small_expense_single_stage(db):
    out = _submit(db, 500000)
    assert out["approval_stage"] == "final"
    review_expense(out["id"], ExpenseReviewIn(decision="approved"), user=CHAIRMAN, db=db)
    e = db.query(ExpenseRequest).filter(ExpenseRequest.id == out["id"]).first()
    assert e.status == "approved"


def test_large_expense_two_stage(db):
    bu = db.test_bus[0]
    out = _submit(db, 5000000)
    assert out["approval_stage"] == "first"
    # Chairman cannot approve stage 1
    with pytest.raises(HTTPException):
        review_expense(out["id"], ExpenseReviewIn(decision="approved"), user=CHAIRMAN, db=db)
    # HO approves stage 1 -> still pending, moves to final
    mid = review_expense(out["id"], ExpenseReviewIn(decision="approved"), user=HO, db=db)
    assert mid["status"] == "pending" and mid["approval_stage"] == "final"
    # Chairman final approval posts the expense
    review_expense(out["id"], ExpenseReviewIn(decision="approved"), user=CHAIRMAN, db=db)
    e = db.query(ExpenseRequest).filter(ExpenseRequest.id == out["id"]).first()
    assert e.status == "approved"
    assert balance(db, bu.id, "6600") == Decimal("5000000")  # supplies -> default 6600


# ---------------------------------------------------------------- C9 intercompany

def test_intercompany_create_and_settle(db):
    seller, buyer = db.test_bus
    t = create_intercompany(IntercompanyIn(
        from_business_unit_id=seller.id, to_business_unit_id=buyer.id,
        amount=300000, description="Shared logistics", txn_date=date(2026, 10, 10),
        buyer_account_code="6400",
    ), user=CHAIRMAN, db=db)
    # seller: 1180 debited; buyer: 2180 credited
    assert balance(db, seller.id, "1180") == Decimal("300000")
    assert balance(db, buyer.id, "2180") == Decimal("-300000")
    assert balance(db, buyer.id, "6400") == Decimal("300000")
    out = settle_intercompany(t["id"], user=CHAIRMAN, db=db)
    assert out["status"] == "settled"
    assert balance(db, seller.id, "1180") == Decimal("0")
    assert balance(db, buyer.id, "2180") == Decimal("0")
    assert balance(db, seller.id, "1110") == Decimal("300000")
