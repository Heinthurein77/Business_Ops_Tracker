"""Tests for the ERP Phase 1 accounting core (in-memory SQLite).

Run with: ENVIRONMENT=test .venv/bin/python -m pytest tests/test_accounting.py
"""
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.accounting_templates import template_accounts
from src.models import Account, Base, BusinessUnit, JournalEntry
from src.routers.accounting import (
    auto_post_daily_financial, create_entry, validate_lines,
    _reverse_entry,
)
from src.models import DailyFinancial


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    bu = BusinessUnit(name="Test BU", code="TBU")
    session.add(bu)
    session.commit()
    # provision a retail CoA by hand (same shape as the endpoint)
    id_by_code = {}
    for code, name, acc_type, parent_code, is_header in template_accounts("retail"):
        acc = Account(business_unit_id=bu.id, code=code, name=name,
                      account_type=acc_type, is_header=is_header, is_system=True,
                      parent_id=id_by_code.get(parent_code) if parent_code else None)
        session.add(acc)
        session.flush()
        id_by_code[code] = acc.id
    session.commit()
    session.test_bu_id = bu.id
    session.test_ids = id_by_code
    yield session
    session.close()


def line(db, code, debit=0, credit=0):
    return {"account_id": db.test_ids[code], "debit": Decimal(str(debit)),
            "credit": Decimal(str(credit))}


def test_provision_creates_standard_codes(db):
    codes = {a.code for a in db.query(Account).all()}
    assert {"1110", "1120", "4100", "5100", "6100", "6200"}.issubset(codes)
    assert len(codes) == len(template_accounts("retail"))


def test_balanced_entry_posts(db):
    e = create_entry(db, db.test_bu_id, date(2026, 10, 1), "sale", "manual", None,
                     [line(db, "1110", debit=1000), line(db, "4100", credit=1000)],
                     post=True)
    db.commit()
    assert e.status == "posted"
    assert e.entry_no.startswith("JV-2026-10-")


def test_unbalanced_entry_rejected(db):
    with pytest.raises(HTTPException) as exc:
        create_entry(db, db.test_bu_id, date(2026, 10, 1), "bad", "manual", None,
                     [line(db, "1110", debit=1000), line(db, "4100", credit=900)],
                     post=True)
    assert "does not balance" in exc.value.detail


def test_header_account_cannot_be_posted(db):
    with pytest.raises(HTTPException) as exc:
        create_entry(db, db.test_bu_id, date(2026, 10, 1), "bad", "manual", None,
                     [line(db, "1100", debit=1000), line(db, "4100", credit=1000)],
                     post=True)
    assert "header" in exc.value.detail


def test_two_sided_line_rejected(db):
    bad = {"account_id": db.test_ids["1110"], "debit": Decimal("5"), "credit": Decimal("5")}
    with pytest.raises(HTTPException):
        validate_lines(db, db.test_bu_id, [bad, line(db, "4100", credit=10)])


def test_reversal_zeroes_balance(db):
    e = create_entry(db, db.test_bu_id, date(2026, 10, 1), "sale", "manual", None,
                     [line(db, "1110", debit=1000), line(db, "4100", credit=1000)],
                     post=True)
    db.commit()
    rev = _reverse_entry(db, e, None)
    db.commit()
    assert rev.status == "posted"
    assert e.reversed_by_id == rev.id
    total_dr = sum(float(l.debit) for en in db.query(JournalEntry).all() for l in en.lines)
    total_cr = sum(float(l.credit) for en in db.query(JournalEntry).all() for l in en.lines)
    cash = db.query(Account).filter(Account.code == "1110").first()
    cash_bal = sum(float(l.debit) - float(l.credit)
                   for en in db.query(JournalEntry).filter(JournalEntry.status == "posted").all()
                   for l in en.lines if l.account_id == cash.id)
    assert cash_bal == 0
    assert total_dr == total_cr == 2000


def test_auto_post_daily_financial_and_resubmit(db):
    df = DailyFinancial(business_unit_id=db.test_bu_id, report_date=date(2026, 10, 20),
                        revenue=Decimal("500000"), cash_flow=Decimal("0"),
                        receivable=Decimal("0"), payable=Decimal("0"),
                        expenses=Decimal("0"), submitted_by=None)
    db.add(df)
    db.commit()
    auto_post_daily_financial(db, df, None)
    entries = db.query(JournalEntry).filter(JournalEntry.source == "daily_financial").all()
    assert len(entries) == 1
    assert entries[0].status == "posted"
    # resubmit with a corrected figure: old reversed, new posted
    df.revenue = Decimal("600000")
    db.commit()
    auto_post_daily_financial(db, df, None)
    entries = db.query(JournalEntry).filter(
        JournalEntry.source == "daily_financial").order_by(JournalEntry.id).all()
    auto = [e for e in entries if e.source_id == df.id]
    assert auto[0].reversed_by_id is not None
    live = [e for e in auto if e.status == "posted" and e.reversed_by_id is None
            and e.source != "reversal"]
    assert len(live) == 1
    assert float(sum(l.debit for l in live[0].lines)) == 600000


def test_closed_period_blocks_posting(db):
    from src.models import FiscalPeriod
    db.add(FiscalPeriod(business_unit_id=db.test_bu_id, period="2026-10", status="closed"))
    db.commit()
    with pytest.raises(HTTPException) as exc:
        create_entry(db, db.test_bu_id, date(2026, 10, 15), "x", "manual", None,
                     [line(db, "1110", debit=1), line(db, "4100", credit=1)], post=True)
    assert "closed" in exc.value.detail


# ---- go-live additions: exports + opening balance upload ----

def test_export_rows_trial_balance(db):
    from src.routers.accounting import _export_rows
    create_entry(db, db.test_bu_id, date(2026, 10, 1), "sale", "manual", None,
                 [line(db, "1110", debit=500), line(db, "4100", credit=500)], post=True)
    db.commit()
    user = type("U", (), {"role": "Chairman", "business_unit_id": None})()
    title, header, rows = _export_rows("tb", db, user, db.test_bu_id, "2026-10")
    assert title == "Trial Balance"
    assert rows[-1][1] == "TOTAL"
    assert rows[-1][2] == rows[-1][3] == 500.0


def test_opening_balance_upload_accepts_template_layout(db):
    import asyncio
    from io import BytesIO

    from openpyxl import Workbook

    from src.routers.accounting import opening_balance

    wb = Workbook()
    ws = wb.active
    ws.append(["account_code", "account_name", "debit", "credit"])
    ws.append(["1110", "Cash on Hand", 2000000, None])
    ws.append(["3100", "Owner Capital", None, 2000000])
    buf = BytesIO()
    wb.save(buf)

    class FakeUpload:
        filename = "ob.xlsx"
        async def read(self):
            return buf.getvalue()

    user = type("U", (), {"id": None, "role": "Accountant", "business_unit_id": None})()
    out = asyncio.get_event_loop().run_until_complete(
        opening_balance(business_unit_id=db.test_bu_id, as_of=date(2026, 10, 1),
                        file=FakeUpload(), user=user, db=db))
    assert out["status"] == "posted"
    assert out["total_debit"] == 2000000


def test_opening_balance_rejects_unbalanced(db):
    import asyncio
    from io import BytesIO

    from fastapi import HTTPException
    from openpyxl import Workbook

    from src.routers.accounting import opening_balance

    wb = Workbook()
    ws = wb.active
    ws.append(["account_code", "debit", "credit"])
    ws.append(["1110", 1000, None])
    ws.append(["3100", None, 900])
    buf = BytesIO()
    wb.save(buf)

    class FakeUpload:
        filename = "ob.xlsx"
        async def read(self):
            return buf.getvalue()

    user = type("U", (), {"id": None, "role": "Accountant", "business_unit_id": None})()
    with pytest.raises(HTTPException) as exc:
        asyncio.get_event_loop().run_until_complete(
            opening_balance(business_unit_id=db.test_bu_id, as_of=date(2026, 10, 1),
                            file=FakeUpload(), user=user, db=db))
    assert "do not balance" in exc.value.detail
