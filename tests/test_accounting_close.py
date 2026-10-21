"""Tests for cash-flow statement, year-end close and credit limits."""
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models import Base, BusinessUnit
from src.models import JournalLine
from src.routers.accounting import (
    YearCloseIn, account_by_code, cash_flow_report, provision_bu, year_close,
)
from src.routers.ops import (
    CustomerIn, InvoiceIn, InvoiceLineIn, PaymentIn, create_invoice,
    create_sales_customer, record_payment, send_invoice,
)

ADMIN = SimpleNamespace(id=None, role="Chairman", business_unit_id=None)


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    bu = BusinessUnit(name="Shop", code="S1")
    session.add(bu)
    session.commit()
    provision_bu(bu.id, template="retail", user=ADMIN, db=session)
    session.test_bu = bu
    yield session
    session.close()


def _sale(db, amount=100000, limit=None):
    cust = create_sales_customer(CustomerIn(business_unit_id=db.test_bu.id, name="Buyer",
                                            credit_limit=limit), user=ADMIN, db=db)
    inv = create_invoice(InvoiceIn(
        business_unit_id=db.test_bu.id, customer_id=cust["id"], invoice_date=date(2026, 10, 5),
        tax_rate=0, lines=[InvoiceLineIn(description="Sale", quantity=1, unit_price=amount)]),
        user=ADMIN, db=db)
    return cust, inv


def test_credit_limit_blocks_send(db):
    _, inv1 = _sale(db, 80000, limit=100000)
    send_invoice(inv1["id"], user=ADMIN, db=db)  # within limit
    cust2 = create_sales_customer(CustomerIn(business_unit_id=db.test_bu.id, name="Buyer"),
                                  user=ADMIN, db=db)
    # same customer object used by inv1 — create a second invoice for them
    inv2 = create_invoice(InvoiceIn(
        business_unit_id=db.test_bu.id, customer_id=inv1["customer_id"],
        invoice_date=date(2026, 10, 6), tax_rate=0,
        lines=[InvoiceLineIn(description="More", quantity=1, unit_price=30000)]),
        user=ADMIN, db=db)
    with pytest.raises(HTTPException) as exc:
        send_invoice(inv2["id"], user=ADMIN, db=db)
    assert exc.value.status_code == 400
    assert "Credit limit" in str(exc.value.detail)


def test_cash_flow_and_year_close(db):
    _, inv = _sale(db, 200000)
    send_invoice(inv["id"], user=ADMIN, db=db)
    record_payment(inv["id"], PaymentIn(amount=200000, payment_date=date(2026, 10, 7)),
                   user=ADMIN, db=db)
    cf = cash_flow_report(db.test_bu.id, "2026-10", user=ADMIN, db=db)
    assert cf["operating"] == 200000 and cf["closing_cash"] == 200000
    out = year_close(YearCloseIn(business_unit_id=db.test_bu.id, year=2026), user=ADMIN, db=db)
    assert out["total_income"] == 200000 and out["net_profit"] == 200000
    re_acc = account_by_code(db, db.test_bu.id, "3200")
    from sqlalchemy import func as _f
    bal = db.query(_f.coalesce(_f.sum(JournalLine.debit - JournalLine.credit), 0)).filter(
        JournalLine.account_id == re_acc.id).scalar()
    assert float(bal) == -200000  # credit balance in retained earnings
    with pytest.raises(HTTPException):
        year_close(YearCloseIn(business_unit_id=db.test_bu.id, year=2026), user=ADMIN, db=db)
