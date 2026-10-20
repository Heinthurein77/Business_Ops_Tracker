"""Tests for ERP Phase 4 sales invoices + aging (in-memory SQLite).

Run with: ENVIRONMENT=test .venv/bin/python -m pytest tests/test_sales.py
"""
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.accounting_templates import template_accounts
from src.models import (
    Account, Base, BusinessUnit, Customer, JournalEntry, SalesInvoice,
    SalesInvoiceLine,
)
from src.routers.ops import (
    CustomerIn, InvoiceIn, InvoiceLineIn, PaymentIn, aging_report,
    create_invoice, create_sales_customer, record_payment, send_invoice,
)

ADMIN = SimpleNamespace(id=None, role="Chairman", business_unit_id=None)


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


def make_invoice(db, due_days_ago=None, price=100000, qty=10):
    cust = create_sales_customer(
        CustomerIn(business_unit_id=db.test_bu.id, name="Hotel"), user=ADMIN, db=db)
    due = date.today() - timedelta(days=due_days_ago) if due_days_ago is not None else None
    inv = create_invoice(InvoiceIn(
        business_unit_id=db.test_bu.id, customer_id=cust["id"],
        invoice_date=date.today(), due_date=due, tax_rate=5,
        lines=[InvoiceLineIn(description="Goods", quantity=qty, unit_price=price)],
    ), user=ADMIN, db=db)
    return inv


def test_invoice_tax_and_send_posts_ar(db):
    inv = make_invoice(db)
    assert inv["subtotal"] == 1000000
    assert inv["tax_amount"] == 50000
    assert inv["total"] == 1050000
    send_invoice(inv["id"], user=ADMIN, db=db)
    assert balance(db, "1130") == 1050000   # AR gross
    assert balance(db, "4100") == -1000000  # sales net
    assert balance(db, "2130") == -50000    # tax payable


def test_partial_then_full_payment(db):
    inv = make_invoice(db)
    send_invoice(inv["id"], user=ADMIN, db=db)
    out = record_payment(inv["id"], PaymentIn(amount=400000, payment_date=date.today()),
                         user=ADMIN, db=db)
    assert out["status"] == "partial"
    assert out["balance"] == 650000
    out = record_payment(inv["id"], PaymentIn(amount=650000, payment_date=date.today()),
                         user=ADMIN, db=db)
    assert out["status"] == "paid"
    assert balance(db, "1130") == 0
    assert balance(db, "1110") == 1050000


def test_aging_buckets(db):
    inv = make_invoice(db, due_days_ago=45)
    send_invoice(inv["id"], user=ADMIN, db=db)
    report = aging_report(business_unit_id=db.test_bu.id, user=ADMIN, db=db)
    assert report["ar"]["buckets"]["31-60"] == 1050000
    assert report["ar"]["total"] == 1050000
    assert report["ar"]["items"][0]["bucket"] == "31-60"
