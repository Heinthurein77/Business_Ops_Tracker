"""Workstream C (BOT real-time risk review): PM-1/PM-2/PM-3/PM-4/PM-6,
Pm-1/Pm-2/Pm-3/Pm-4, Cm-5, SM-14, PM-7, TZ regression tests.

The most valuable tests here are the report-equivalence tests: the
fixture seeds a deterministic two-BU ledger (two periods, an
intercompany sale + a partial reversal, budgets, tax lines, invoices
with payments/returns, POs with returns), and each test compares the
new SQL-aggregated endpoint against a reference implementation of the
OLD pre-fix Python aggregation, written in this file from the old
code. Identical figures on the same rows proves the PM-1/PM-3
rewrites did not change what the reports say; hand-computed spot
values pin the absolute figures too.

Other tests pin the caps/defaults (PM-2/PM-4), the batch helpers
(PM-3), the plain-def receipt upload (PM-6), the crontab flock guards
(Pm-1), the retention heartbeat (Pm-2), the audit-gap check (SM-14),
the pool-wait counter (Cm-5), the long-report timeout (Pm-3), the
utcnow cleanup (Pm-4), the deploy script + TZ (PM-7/TZ), and the
pool settings (PM-5).
"""

import asyncio
import inspect
import os
import stat
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, func
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("ENVIRONMENT", "test")

from src.accounting_templates import template_accounts
from src.config import settings
from src.database import Base
from src.models import (Account, ActionItem, AuditLog, BankAccount,
                        BankTransaction, Budget, BusinessUnit, Customer,
                        Employee, ExpenseRequest, InvoicePayment,
                        JobHeartbeat, JournalEntry, JournalLine, PurchaseOrder,
                        PurchaseOrderLine, PurchaseReturn, PurchaseReturnLine,
                        SalesInvoice, SalesInvoiceLine, SalesReturn,
                        SalesReturnLine, StaffAdvance, Supplier, User)
from src.routers.accounting import (_ensure_intercompany_accounts,
                                    _export_rows, _long_report_timeout,
                                    _posted_totals, balance_sheet,
                                    bank_reconciliation, bu_comparison,
                                    cash_flow_report, consolidated_bs,
                                    consolidated_pl, create_entry,
                                    export_report, finance_summary,
                                    list_budgets, profit_loss, tax_report,
                                    trial_balance, fmt, ZERO)
from src.routers.ops import (aging_report, customer_statement, export_excel,
                             invoice_amounts, invoice_amounts_batch,
                             po_net_totals_batch, po_total,
                             upload_expense_receipt)
from src.jobs.log_scan import (check_audit_gaps, check_heartbeats,
                               count_pool_signals, count_signals, stale_jobs)
from src.utils import today_local, utcnow

CHAIRMAN = SimpleNamespace(id=1, role="Chairman", business_unit_id=None,
                           username="chairman")


def _drain(resp):
    """Body bytes of a StreamingResponse (Starlette wraps sync bodies in
    an async iterator, so a plain b"".join(resp.body_iterator) fails)."""
    async def _collect():
        chunks = []
        async for chunk in resp.body_iterator:
            chunks.append(chunk)
        return b"".join(chunks)
    return asyncio.run(_collect())
D = Decimal


def _line(db_ids, code, debit=0, credit=0):
    return {"account_id": db_ids[code], "debit": D(str(debit)),
            "credit": D(str(credit))}


def _provision(db, bu_id):
    """Retail CoA by hand (same shape as the endpoint) + IC accounts."""
    id_by_code = {}
    for code, name, acc_type, parent_code, is_header in template_accounts("retail"):
        acc = Account(business_unit_id=bu_id, code=code, name=name,
                      account_type=acc_type, is_header=is_header,
                      is_system=True,
                      parent_id=id_by_code.get(parent_code) if parent_code else None)
        db.add(acc)
        db.flush()
        id_by_code[code] = acc.id
    _ensure_intercompany_accounts(db, bu_id)
    db.commit()
    for acc in db.query(Account).filter(Account.business_unit_id == bu_id).all():
        id_by_code[acc.code] = acc.id
    return id_by_code


@pytest.fixture
def db():
    """Deterministic two-BU fixture: journals + documents (see module docstring).

    Layer A — posted journals via create_entry (post=True):
      BU1 Sep: E1 sales 100000, E2 expense 40000, E3 tax charged 5000,
               E8 IC sale 20000, E10 partial reversal of E8 (5000),
               E12 tax paid 2000;  BU1 Oct: E4 sales 60000, E5 expense 25000;
      BU1 Aug: E11 sales 70000 (prior-period opening for Sep reports).
      BU2 Sep: E6 sales 80000, E7 expense 30000, E9 IC charge 20000.
    Layer B — documents (direct ORM rows, no journals): invoices with
    payments/returns, POs with returns (see _seed_documents).
    """
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([
        User(id=1, username="chairman", hashed_password="x", role="Chairman",
             is_active=True, token_version=0),
        User(id=2, username="aye", hashed_password="x", full_name="Aye Aye",
             role="CEO", business_unit_id=1, is_active=True, token_version=0),
    ])
    bu1 = BusinessUnit(id=1, code="BU1", name="Shop One")
    bu2 = BusinessUnit(id=2, code="BU2", name="Shop Two")
    session.add_all([bu1, bu2])
    session.commit()
    ids1 = _provision(session, 1)
    ids2 = _provision(session, 2)
    session.test_ids = {1: ids1, 2: ids2}
    session.test_engine = engine

    def post(bu_id, d, desc, source, lines, **kw):
        e = create_entry(session, bu_id, d, desc, source, None, lines,
                         post=True, **kw)
        session.commit()
        return e

    # BU1 Aug (prior period) — opening balances for the Sep reports.
    post(1, date(2026, 8, 15), "Aug sale", "manual",
         [_line(ids1, "1110", debit=70000), _line(ids1, "4100", credit=70000)])
    # BU1 Sep.
    post(1, date(2026, 9, 5), "Sep sale", "manual",
         [_line(ids1, "1110", debit=100000), _line(ids1, "4100", credit=100000)])
    post(1, date(2026, 9, 10), "Sep expense", "manual",
         [_line(ids1, "6100", debit=40000), _line(ids1, "1110", credit=40000)])
    post(1, date(2026, 9, 12), "Tax charged", "manual",
         [_line(ids1, "1110", debit=5000), _line(ids1, "2130", credit=5000)])
    # BU2 Sep.
    post(2, date(2026, 9, 8), "Sep sale", "manual",
         [_line(ids2, "1110", debit=80000), _line(ids2, "4100", credit=80000)])
    post(2, date(2026, 9, 15), "Sep expense", "manual",
         [_line(ids2, "6300", debit=30000), _line(ids2, "1110", credit=30000)])
    # Intercompany sale BU1 -> BU2 (20,000), buyer expenses it on 6600.
    e_ic = post(1, date(2026, 9, 20), "IC sale to BU2", "intercompany",
                [_line(ids1, "1180", debit=20000), _line(ids1, "4100", credit=20000)])
    post(2, date(2026, 9, 20), "IC charge from BU1", "intercompany",
         [_line(ids2, "6600", debit=20000), _line(ids2, "2180", credit=20000)])
    # Partial reversal of the IC sale (5,000) inside the same period.
    post(1, date(2026, 9, 25), "Reversal of IC sale", "reversal",
         [_line(ids1, "4100", debit=5000), _line(ids1, "1180", credit=5000)],
         reversal_of_id=e_ic.id)
    # BU1 Sep tax paid.
    post(1, date(2026, 9, 18), "Tax paid", "manual",
         [_line(ids1, "2130", debit=2000), _line(ids1, "1110", credit=2000)])
    # BU1 Oct.
    post(1, date(2026, 10, 3), "Oct sale", "manual",
         [_line(ids1, "1110", debit=60000), _line(ids1, "4100", credit=60000)])
    post(1, date(2026, 10, 7), "Oct expense", "manual",
         [_line(ids1, "6200", debit=25000), _line(ids1, "1110", credit=25000)])

    session.add_all([
        Budget(business_unit_id=1, period="2026-09", account_code="4100",
               amount=D("90000"), created_by_id=1),
        Budget(business_unit_id=1, period="2026-09", account_code="6100",
               amount=D("50000"), created_by_id=1),
    ])
    bank_acc = session.query(Account).filter(
        Account.business_unit_id == 1, Account.code == "1110").one()
    bank = BankAccount(business_unit_id=1, bank_name="KBZ",
                       account_name="Main", account_id=bank_acc.id)
    session.add(bank)
    session.flush()
    # Statement balance is the sum of statement transactions (deposits -
    # withdrawals), not a stored column: one deposit of 168500.
    session.add(BankTransaction(bank_account_id=bank.id, txn_date=date(2026, 9, 30),
                                description="Sep statement", deposit=D("168500"),
                                withdrawal=D("0")))
    _seed_documents(session)
    session.commit()
    yield session
    session.close()
    engine.dispose()


def _seed_documents(session):
    """Layer B: invoices / POs with payments and posted returns."""
    c1 = Customer(business_unit_id=1, name="Cust One")
    c2 = Customer(business_unit_id=2, name="Cust Two")
    s1 = Supplier(business_unit_id=1, name="Sup One")
    s2 = Supplier(business_unit_id=2, name="Sup Two")
    session.add_all([c1, c2, s1, s2])
    session.flush()
    # INV-001: 2 x 10000 + 5% tax = 21000; paid 5000; posted return
    # 1 x 10000 (+5% tax = 10500) -> balance 5500.
    i1 = SalesInvoice(business_unit_id=1, customer_id=c1.id, invoice_no="INV-001",
                      status="sent", invoice_date=date(2026, 9, 6),
                      due_date=date(2026, 9, 20), tax_rate=D("5"),
                      sent_at=datetime(2026, 9, 6, 10, 0))
    session.add(i1)
    session.flush()
    session.add(SalesInvoiceLine(invoice_id=i1.id, description="Goods",
                                 quantity=D("2"), unit_price=D("10000")))
    session.add(InvoicePayment(invoice_id=i1.id, amount=D("5000"),
                               payment_date=date(2026, 9, 20)))
    r1 = SalesReturn(business_unit_id=1, invoice_id=i1.id, return_no="SR-001",
                     status="posted", return_date=date(2026, 9, 22))
    session.add(r1)
    session.flush()
    session.add(SalesReturnLine(return_id=r1.id, invoice_line_id=1,
                                quantity=D("1"), unit_price=D("10000")))
    # INV-002: 1 x 30000, tax 0, unpaid -> balance 30000 (oldest, overdue).
    i2 = SalesInvoice(business_unit_id=1, customer_id=c1.id, invoice_no="INV-002",
                      status="sent", invoice_date=date(2026, 8, 5),
                      due_date=date(2026, 8, 1), tax_rate=D("0"),
                      sent_at=datetime(2026, 8, 5, 10, 0))
    session.add(i2)
    session.flush()
    session.add(SalesInvoiceLine(invoice_id=i2.id, description="Goods",
                                 quantity=D("1"), unit_price=D("30000")))
    # INV-003: draft (excluded from aging, included in the statement).
    i3 = SalesInvoice(business_unit_id=1, customer_id=c2.id, invoice_no="INV-003",
                      status="draft", invoice_date=date(2026, 9, 10),
                      due_date=date(2026, 9, 25), tax_rate=D("5"))
    session.add(i3)
    session.flush()
    session.add(SalesInvoiceLine(invoice_id=i3.id, description="Goods",
                                 quantity=D("1"), unit_price=D("5000")))
    # INV-004 (BU2): 1 x 10000 + 10% tax = 11000; paid 1000 -> balance 10000.
    i4 = SalesInvoice(business_unit_id=2, customer_id=c2.id, invoice_no="INV-004",
                      status="partial", invoice_date=date(2026, 10, 20),
                      due_date=date(2026, 11, 5), tax_rate=D("10"),
                      sent_at=datetime(2026, 10, 20, 10, 0))
    session.add(i4)
    session.flush()
    session.add(SalesInvoiceLine(invoice_id=i4.id, description="Goods",
                                 quantity=D("1"), unit_price=D("10000")))
    session.add(InvoicePayment(invoice_id=i4.id, amount=D("1000"),
                               payment_date=date(2026, 10, 25)))
    # PO-001: 5 x 2000 = 10000; posted return 1 x 2000 -> net 8000.
    p1 = PurchaseOrder(business_unit_id=1, supplier_id=s1.id, po_no="PO-001",
                       status="received", order_date=date(2026, 9, 5),
                       received_at=datetime(2026, 9, 10, 10, 0))
    session.add(p1)
    session.flush()
    session.add(PurchaseOrderLine(po_id=p1.id, item_name="Widget",
                                  quantity=D("5"), unit_cost=D("2000")))
    pr1 = PurchaseReturn(business_unit_id=1, po_id=p1.id, return_no="PR-001",
                         status="posted", return_date=date(2026, 9, 12))
    session.add(pr1)
    session.flush()
    session.add(PurchaseReturnLine(return_id=pr1.id, po_line_id=1,
                                   quantity=D("1"), unit_cost=D("2000")))
    # PO-002: 2 x 15000 = 30000, no returns -> net 30000.
    p2 = PurchaseOrder(business_unit_id=1, supplier_id=s1.id, po_no="PO-002",
                       status="received", order_date=date(2026, 10, 15),
                       received_at=datetime(2026, 10, 20, 10, 0))
    session.add(p2)
    session.flush()
    session.add(PurchaseOrderLine(po_id=p2.id, item_name="Widget",
                                  quantity=D("2"), unit_cost=D("15000")))
    # PO-003: draft (excluded from AP aging).
    p3 = PurchaseOrder(business_unit_id=1, supplier_id=s1.id, po_no="PO-003",
                       status="draft", order_date=date(2026, 10, 1))
    session.add(p3)
    session.flush()
    session.add(PurchaseOrderLine(po_id=p3.id, item_name="Widget",
                                  quantity=D("1"), unit_cost=D("9999")))
    # PO-004 (BU2): 1 x 5000 -> net 5000.
    p4 = PurchaseOrder(business_unit_id=2, supplier_id=s2.id, po_no="PO-004",
                       status="received", order_date=date(2026, 8, 28),
                       received_at=datetime(2026, 9, 1, 10, 0))
    session.add(p4)
    session.flush()
    session.add(PurchaseOrderLine(po_id=p4.id, item_name="Widget",
                                  quantity=D("1"), unit_cost=D("5000")))


# ------------------------------------------------------------------ old references
# Faithful copies of the pre-PM-1 / pre-PM-3 logic: load every posted
# line and filter/aggregate in Python. The endpoints must return the
# same dicts as these references on the same rows.

def _old_posted_lines(db, bu_id=None, start=None, end=None):
    q = (db.query(JournalLine, Account, JournalEntry)
         .join(Account, JournalLine.account_id == Account.id)
         .join(JournalEntry, JournalLine.entry_id == JournalEntry.id)
         .filter(JournalEntry.status == "posted"))
    if bu_id is not None:
        q = q.filter(JournalEntry.business_unit_id == bu_id)
    out = []
    for line, acc, e in q.all():
        if start is not None and e.entry_date < start:
            continue
        if end is not None and e.entry_date > end:
            continue
        out.append((line, acc, e))
    return out


def _pstart(period):
    if not period:
        return None
    y, m = map(int, period.split("-"))
    return date(y, m, 1)


def _pend(period):
    if not period:
        return None
    import calendar as _cal
    y, m = map(int, period.split("-"))
    return date(y, m, _cal.monthrange(y, m)[1])


def _old_trial_balance(db, bu_id, period=None):
    agg = {}
    td = tc = ZERO
    for line, acc, e in _old_posted_lines(db, bu_id, end=_pend(period)):
        d, c = agg.get(acc.code, (ZERO, ZERO))
        agg[acc.code] = (d + D(str(line.debit or 0)), c + D(str(line.credit or 0)))
    items = []
    for code in sorted(agg):
        d, c = agg[code]
        acc = db.query(Account).filter(Account.business_unit_id == bu_id,
                                       Account.code == code).one()
        bal = d - c
        items.append({"account_id": acc.id, "code": code, "name": acc.name,
                      "account_type": acc.account_type, "debit": fmt(d),
                      "credit": fmt(c), "balance": fmt(bal),
                      "balance_side": "debit" if bal >= 0 else "credit"})
        td += d
        tc += c
    return {"items": items, "total_debit": fmt(td), "total_credit": fmt(tc),
            "balanced": td == tc}


def _old_pl_parts(db, bu_id, period=None, group=False):
    income, expense = {}, {}
    for line, acc, e in _old_posted_lines(
            db, None if group else bu_id, start=_pstart(period), end=_pend(period)):
        if acc.account_type == "income":
            income[acc.code] = income.get(acc.code, ZERO) + D(str(line.credit or 0)) - D(str(line.debit or 0))
        elif acc.account_type == "expense":
            expense[acc.code] = expense.get(acc.code, ZERO) + D(str(line.debit or 0)) - D(str(line.credit or 0))
    return income, expense


def _old_profit_loss(db, bu_id, period=None):
    income, expense = _old_pl_parts(db, bu_id, period)
    names = {a.code: a.name for a in db.query(Account).filter(
        Account.business_unit_id == bu_id).all()}
    ti = sum(income.values(), ZERO)
    te = sum(expense.values(), ZERO)
    return {
        "income": [{"code": c, "name": names.get(c, c), "amount": fmt(v)} for c, v in sorted(income.items())],
        "expenses": [{"code": c, "name": names.get(c, c), "amount": fmt(v)} for c, v in sorted(expense.items())],
        "total_income": fmt(ti), "total_expenses": fmt(te), "net_profit": fmt(ti - te),
    }


def _old_balance_sheet(db, bu_id, period=None):
    groups = {"asset": {}, "liability": {}, "equity": {}}
    retained = ZERO
    for line, acc, e in _old_posted_lines(db, bu_id, end=_pend(period)):
        if acc.account_type in groups:
            bal = D(str(line.debit or 0)) - D(str(line.credit or 0))
            if acc.account_type != "asset":
                bal = -bal
            groups[acc.account_type][acc.code] = groups[acc.account_type].get(acc.code, ZERO) + bal
        elif acc.account_type == "income":
            retained += D(str(line.credit or 0)) - D(str(line.debit or 0))
        elif acc.account_type == "expense":
            retained -= D(str(line.debit or 0)) - D(str(line.credit or 0))
    names = {a.code: a.name for a in db.query(Account).filter(
        Account.business_unit_id == bu_id).all()}

    def section(kind):
        items = [{"code": c, "name": names.get(c, c), "amount": fmt(v)}
                 for c, v in sorted(groups[kind].items()) if v != 0]
        return items, sum(groups[kind].values(), ZERO)

    assets, ta = section("asset")
    liabs, tl = section("liability")
    equity, te = section("equity")
    te += retained
    equity.append({"code": "—", "name": "Retained Earnings (current result)",
                   "amount": fmt(retained)})
    return {"assets": assets, "total_assets": fmt(ta),
            "liabilities": liabs, "total_liabilities": fmt(tl),
            "equity": equity, "total_equity": fmt(te),
            "total_liabilities_equity": fmt(tl + te),
            "balanced": ta == tl + te}


def _old_consolidated_pl(db, period=None):
    income, expense = _old_pl_parts(db, None, period, group=True)
    names = {}
    for line, acc, e in _old_posted_lines(db, end=_pend(period)):
        names.setdefault(acc.code, acc.name)
    # Old code: the IC id set came from the full-history load.
    ic_ids = {e.id for line, acc, e in _old_posted_lines(db)
              if e.source == "intercompany"}
    ic_income, ic_expense = {}, {}
    for line, acc, e in _old_posted_lines(db, start=_pstart(period), end=_pend(period)):
        if e.source == "intercompany" or (e.source == "reversal" and e.reversal_of_id in ic_ids):
            if acc.account_type == "income":
                ic_income[acc.code] = ic_income.get(acc.code, ZERO) + D(str(line.credit or 0)) - D(str(line.debit or 0))
            elif acc.account_type == "expense":
                ic_expense[acc.code] = ic_expense.get(acc.code, ZERO) + D(str(line.debit or 0)) - D(str(line.credit or 0))
    eliminated = min(sum(ic_income.values(), ZERO), sum(ic_expense.values(), ZERO))
    if eliminated > 0:
        remaining = eliminated
        for code in sorted(ic_income, key=lambda c: -ic_income[c]):
            take = min(ic_income[code], remaining)
            income[code] = income.get(code, ZERO) - take
            remaining -= take
            if remaining <= 0:
                break
        remaining = eliminated
        for code in sorted(ic_expense, key=lambda c: -ic_expense[c]):
            take = min(ic_expense[code], remaining)
            expense[code] = expense.get(code, ZERO) - take
            remaining -= take
            if remaining <= 0:
                break
    ti = sum(income.values(), ZERO)
    te = sum(expense.values(), ZERO)
    return {
        "income": [{"code": c, "name": names.get(c, c), "amount": fmt(v)} for c, v in sorted(income.items())],
        "expenses": [{"code": c, "name": names.get(c, c), "amount": fmt(v)} for c, v in sorted(expense.items())],
        "total_income": fmt(ti), "total_expenses": fmt(te), "net_profit": fmt(ti - te),
        "intercompany_eliminated": fmt(eliminated),
    }


def _old_consolidated_bs(db, period=None):
    groups = {"asset": {}, "liability": {}, "equity": {}}
    names = {}
    retained = ZERO
    for line, acc, e in _old_posted_lines(db, end=_pend(period)):
        names.setdefault(acc.code, acc.name)
        if acc.account_type in groups:
            bal = D(str(line.debit or 0)) - D(str(line.credit or 0))
            if acc.account_type != "asset":
                bal = -bal
            groups[acc.account_type][acc.code] = groups[acc.account_type].get(acc.code, ZERO) + bal
        elif acc.account_type == "income":
            retained += D(str(line.credit or 0)) - D(str(line.debit or 0))
        elif acc.account_type == "expense":
            retained -= D(str(line.debit or 0)) - D(str(line.credit or 0))
    ic_asset = groups["asset"].get("1180", ZERO)
    ic_liab = groups["liability"].get("2180", ZERO)
    elimination = min(ic_asset, ic_liab)
    if elimination > 0:
        groups["asset"]["1180"] = ic_asset - elimination
        groups["liability"]["2180"] = ic_liab - elimination

    def section(kind):
        items = [{"code": c, "name": names.get(c, c), "amount": fmt(v)}
                 for c, v in sorted(groups[kind].items()) if v != 0]
        return items, sum(groups[kind].values(), ZERO)

    assets, ta = section("asset")
    liabs, tl = section("liability")
    equity, te = section("equity")
    te += retained
    equity.append({"code": "—", "name": "Retained Earnings (group result)",
                   "amount": fmt(retained)})
    return {"assets": assets, "total_assets": fmt(ta),
            "liabilities": liabs, "total_liabilities": fmt(tl),
            "equity": equity, "total_equity": fmt(te),
            "total_liabilities_equity": fmt(tl + te),
            "intercompany_eliminated": fmt(elimination),
            "balanced": ta == tl + te}


def _old_bu_comparison(db, period=None):
    per_bu = {}
    for line, acc, e in _old_posted_lines(db, start=_pstart(period), end=_pend(period)):
        if acc.account_type not in ("income", "expense"):
            continue
        row = per_bu.setdefault(e.business_unit_id, {"income": ZERO, "expense": ZERO})
        if acc.account_type == "income":
            row["income"] += D(str(line.credit or 0)) - D(str(line.debit or 0))
        else:
            row["expense"] += D(str(line.debit or 0)) - D(str(line.credit or 0))
    names = {bu.id: bu.name for bu in db.query(BusinessUnit).all()}
    items = [{"business_unit_id": bu_id, "bu_name": names.get(bu_id, str(bu_id)),
              "income": fmt(v["income"]), "expenses": fmt(v["expense"]),
              "net_profit": fmt(v["income"] - v["expense"])}
             for bu_id, v in per_bu.items()]
    items.sort(key=lambda x: -x["net_profit"])
    return {"items": items}


def _old_tax_report(db, bu_id, period):
    acc = db.query(Account).filter(Account.business_unit_id == bu_id,
                                   Account.code == "2130").first()
    opening = charged = paid = ZERO
    items = []
    if acc:
        start, end = _pstart(period), _pend(period)
        for line, a, e in _old_posted_lines(db, bu_id):
            if a.id != acc.id:
                continue
            cr, dr = D(str(line.credit or 0)), D(str(line.debit or 0))
            if e.entry_date < start:
                opening += cr - dr
            elif start <= e.entry_date <= end:
                charged += cr
                paid += dr
                items.append({"date": e.entry_date.isoformat(), "entry_no": e.entry_no,
                              "description": e.description,
                              "charged": fmt(cr), "paid": fmt(dr)})
        items.sort(key=lambda i: (i["date"], i["entry_no"]))
    return {"period": period, "account_code": "2130",
            "opening_balance": fmt(opening), "tax_charged": fmt(charged),
            "tax_paid": fmt(paid), "closing_balance": fmt(opening + charged - paid),
            "items": items}


def _old_list_budgets(db, bu_id, period):
    actuals = {}
    types = {}
    names = {}
    for a in db.query(Account).filter(Account.business_unit_id == bu_id).all():
        types[a.code] = a.account_type
        names[a.code] = a.name
    for line, acc, e in _old_posted_lines(db, bu_id, start=_pstart(period), end=_pend(period)):
        if acc.account_type == "income":
            actuals[acc.code] = actuals.get(acc.code, ZERO) + D(str(line.credit or 0)) - D(str(line.debit or 0))
        elif acc.account_type == "expense":
            actuals[acc.code] = actuals.get(acc.code, ZERO) + D(str(line.debit or 0)) - D(str(line.credit or 0))
    items = []
    for b in db.query(Budget).filter(Budget.business_unit_id == bu_id,
                                     Budget.period == period).order_by(Budget.account_code).all():
        actual = actuals.get(b.account_code, ZERO)
        budget = D(str(b.amount or 0))
        var = actual - budget
        items.append({"id": b.id, "account_code": b.account_code,
                      "name": names.get(b.account_code, b.account_code),
                      "account_type": types.get(b.account_code, ""), "budget": fmt(budget),
                      "actual": fmt(actual), "variance": fmt(var),
                      "variance_pct": fmt(var / budget * 100) if budget else None})
    return {"items": items,
            "total_budget": fmt(sum(D(str(i["budget"])) for i in items)),
            "total_actual": fmt(sum(D(str(i["actual"])) for i in items))}


def _old_bank_reconciliation(db, bank_id):
    from src.routers.accounting import serialize_bank_account
    b = db.query(BankAccount).filter(BankAccount.id == bank_id).first()
    info = serialize_bank_account(db, b)
    ledger_balance = None
    if b.account_id:
        total = ZERO
        for line, a, e in _old_posted_lines(db, b.business_unit_id):
            if a.id == b.account_id:
                total += D(str(line.debit or 0)) - D(str(line.credit or 0))
        ledger_balance = fmt(total)
    return {**info, "ledger_balance": ledger_balance,
            "difference": (fmt(D(str(ledger_balance)) - D(str(info["statement_balance"])))
                           if ledger_balance is not None else None)}


def _old_po_net(db, po):
    total = po_total(po)
    ret = db.query(func.coalesce(func.sum(PurchaseReturnLine.quantity * PurchaseReturnLine.unit_cost), 0)).join(
        PurchaseReturn, PurchaseReturnLine.return_id == PurchaseReturn.id).filter(
        PurchaseReturn.po_id == po.id, PurchaseReturn.status == "posted").scalar()
    return total - D(str(ret or 0))


def _old_finance_summary(db, period):
    start, end = _pstart(period), _pend(period)
    income = expense = ZERO
    for line, acc, e in _old_posted_lines(db, start=start, end=end):
        if acc.account_type == "income":
            income += D(str(line.credit or 0)) - D(str(line.debit or 0))
        elif acc.account_type == "expense":
            expense += D(str(line.debit or 0)) - D(str(line.credit or 0))
    cash = tax_payable = ZERO
    for line, acc, e in _old_posted_lines(db):
        if acc.code in ("1110", "1120"):
            cash += D(str(line.debit or 0)) - D(str(line.credit or 0))
        elif acc.code == "2130":
            tax_payable += D(str(line.credit or 0)) - D(str(line.debit or 0))
    provisioned = {e.business_unit_id for e in
                   db.query(JournalEntry).filter(JournalEntry.status == "posted").all()}
    ar = ZERO
    for inv in db.query(SalesInvoice).filter(SalesInvoice.status.in_(("sent", "partial"))).all():
        ar += invoice_amounts(db, inv)["balance"] * D(str(getattr(inv, "exchange_rate", 1) or 1))
    ap = ZERO
    for po in db.query(PurchaseOrder).filter(PurchaseOrder.status == "received").all():
        ap += _old_po_net(db, po) * D(str(getattr(po, "exchange_rate", 1) or 1))
    return {"period": period, "has_books": len(provisioned) > 0,
            "income": fmt(income), "expenses": fmt(expense), "net_profit": fmt(income - expense),
            "cash_position": fmt(cash), "ar_outstanding": fmt(ar), "ap_outstanding": fmt(ap),
            "tax_payable": fmt(tax_payable)}


def _old_cash_flow(db, bu_id, period):
    y, m = map(int, period.split("-"))
    import calendar as _cal
    start, end = date(y, m, 1), date(y, m, _cal.monthrange(y, m)[1])
    cash_ids = {a.id for a in db.query(Account).filter(
        Account.business_unit_id == bu_id, Account.code.in_(("1110", "1120"))).all()}
    accounts = {a.id: a for a in db.query(Account).filter(
        Account.business_unit_id == bu_id).all()}

    def cash_sum(upto=None, frm=None):
        total = ZERO
        for line, a, e in _old_posted_lines(db, bu_id):
            if a.id not in cash_ids:
                continue
            if upto and e.entry_date > upto:
                continue
            if frm and e.entry_date < frm:
                continue
            total += D(str(line.debit or 0)) - D(str(line.credit or 0))
        return total

    opening = cash_sum(upto=start - timedelta(days=1))
    entries = db.query(JournalEntry).filter(
        JournalEntry.business_unit_id == bu_id, JournalEntry.status == "posted",
        JournalEntry.entry_date >= start, JournalEntry.entry_date <= end).order_by(
        JournalEntry.entry_date, JournalEntry.id).all()
    sections = {"operating": ZERO, "investing": ZERO, "financing": ZERO}
    detail = []
    for e in entries:
        # Old code: one query per entry for its lines.
        lines = db.query(JournalLine).filter(JournalLine.entry_id == e.id).all()
        delta = sum((D(str(l.debit or 0)) - D(str(l.credit or 0)))
                    for l in lines if l.account_id in cash_ids)
        if delta == 0:
            continue
        scores = {"operating": ZERO, "investing": ZERO, "financing": ZERO}
        for l in lines:
            if l.account_id in cash_ids:
                continue
            acc = accounts.get(l.account_id)
            if not acc:
                continue
            amt = abs(D(str(l.debit or 0)) - D(str(l.credit or 0)))
            if acc.code.startswith("12") or acc.code == "1530":
                scores["investing"] += amt
            elif acc.code in ("2140", "2200") or acc.account_type == "equity":
                scores["financing"] += amt
            else:
                scores["operating"] += amt
        category = "operating"
        if scores["investing"] > scores["operating"] and scores["investing"] >= scores["financing"]:
            category = "investing"
        elif scores["financing"] > scores["operating"] and scores["financing"] > scores["investing"]:
            category = "financing"
        sections[category] += delta
        detail.append({"date": e.entry_date.isoformat(), "entry_no": e.entry_no,
                       "description": e.description, "category": category,
                       "amount": fmt(delta)})
    net = sum(sections.values())
    return {"period": period, "opening_cash": fmt(opening),
            "operating": fmt(sections["operating"]), "investing": fmt(sections["investing"]),
            "financing": fmt(sections["financing"]), "net_change": fmt(net),
            "closing_cash": fmt(opening + net), "items": detail}


def _bucket(days):
    if days <= 0:
        return "current"
    if days <= 30:
        return "1-30"
    if days <= 60:
        return "31-60"
    if days <= 90:
        return "61-90"
    return "90+"


def _old_aging(db, business_unit_id=None):
    today_d = today_local()
    ar_buckets = {k: D("0") for k in ("current", "1-30", "31-60", "61-90", "90+")}
    ar_items = []
    q = db.query(SalesInvoice).filter(SalesInvoice.status.in_(("sent", "partial")))
    if business_unit_id:
        q = q.filter(SalesInvoice.business_unit_id == business_unit_id)
    for inv in q.all():
        a = invoice_amounts(db, inv)  # old: per-invoice helper (2 queries + lazy lines)
        fx = D(str(getattr(inv, "exchange_rate", 1) or 1))
        balance_mmk = a["balance"] * fx
        if balance_mmk <= 0:
            continue
        days = (today_d - inv.due_date).days if inv.due_date else 0
        b = _bucket(days)
        ar_buckets[b] += balance_mmk
        ar_items.append({"invoice_no": inv.invoice_no, "customer": inv.customer.name if inv.customer else None,
                         "bu_name": inv.business_unit.name if inv.business_unit else None,
                         "due_date": inv.due_date.isoformat() if inv.due_date else None,
                         "days_overdue": max(days, 0), "bucket": b, "balance": float(balance_mmk)})
    ap_buckets = {k: D("0") for k in ("current", "1-30", "31-60", "61-90", "90+")}
    ap_items = []
    qp = db.query(PurchaseOrder).filter(PurchaseOrder.status == "received")
    if business_unit_id:
        qp = qp.filter(PurchaseOrder.business_unit_id == business_unit_id)
    for po in qp.all():
        fx = D(str(getattr(po, "exchange_rate", 1) or 1))
        total = _old_po_net(db, po) * fx  # old: po_total + per-PO returns query
        if total <= 0:
            continue
        ref = po.received_at.date() if po.received_at else po.order_date
        days = (today_d - ref).days
        b = _bucket(days)
        ap_buckets[b] += total
        ap_items.append({"po_no": po.po_no, "supplier": po.supplier.name if po.supplier else None,
                         "bu_name": po.business_unit.name if po.business_unit else None,
                         "received_date": ref.isoformat(), "days": max(days, 0),
                         "bucket": b, "balance": float(total)})
    return {"ar": {"buckets": {k: float(v) for k, v in ar_buckets.items()},
                   "total": float(sum(ar_buckets.values(), D("0"))), "items": ar_items},
            "ap": {"buckets": {k: float(v) for k, v in ap_buckets.items()},
                   "total": float(sum(ap_buckets.values(), D("0"))), "items": ap_items}}


def _old_statement_rows(db, customer_id):
    invs = db.query(SalesInvoice).filter(SalesInvoice.customer_id == customer_id).order_by(
        SalesInvoice.invoice_date, SalesInvoice.id).all()
    rows = []
    total_billed = total_paid = total_returns = D("0")
    for inv in invs:
        a = invoice_amounts(db, inv)
        total_billed += a["total"]
        total_paid += a["paid"]
        total_returns += a.get("returns") or D("0")
        overdue = (inv.status in ("sent", "partial") and inv.due_date is not None
                   and inv.due_date < today_local() and a["balance"] > 0)
        rows.append({"date": inv.invoice_date.isoformat(), "invoice_no": inv.invoice_no,
                     "status": "overdue" if overdue else inv.status,
                     "total": float(a["total"]), "paid": float(a["paid"]),
                     "balance": float(a["balance"]),
                     "currency": getattr(inv, "currency", "MMK") or "MMK"})
    return rows, total_billed, total_paid, total_returns, total_billed - total_paid - total_returns


# ------------------------------------------------------------------ PM-1 equivalence


class TestReportEquivalence:
    def test_trial_balance_matches_old(self, db):
        for period in (None, "2026-09", "2026-10", "2026-08"):
            assert trial_balance(business_unit_id=1, period=period, user=CHAIRMAN, db=db) == \
                _old_trial_balance(db, 1, period)
            assert trial_balance(business_unit_id=2, period=period, user=CHAIRMAN, db=db) == \
                _old_trial_balance(db, 2, period)

    def test_trial_balance_spot_values(self, db):
        # Hand-computed from the fixture journals (see fixture docstring).
        d = trial_balance(business_unit_id=1, period=None, user=CHAIRMAN, db=db)
        assert d["total_debit"] == 327000.0
        assert d["total_credit"] == 327000.0
        assert d["balanced"] is True
        by_code = {i["code"]: i for i in d["items"]}
        assert by_code["1110"]["balance"] == 168000.0
        assert by_code["4100"]["balance"] == -245000.0
        assert by_code["1180"]["balance"] == 15000.0
        assert by_code["2130"]["balance"] == -3000.0
        d = trial_balance(business_unit_id=1, period="2026-09", user=CHAIRMAN, db=db)
        assert d["total_debit"] == 242000.0
        assert d["total_credit"] == 242000.0
        by_code = {i["code"]: i for i in d["items"]}
        assert by_code["1110"]["balance"] == 133000.0
        assert by_code["4100"]["balance"] == -185000.0

    def test_profit_loss_matches_old(self, db):
        for period in (None, "2026-09", "2026-10"):
            assert profit_loss(business_unit_id=1, period=period, user=CHAIRMAN, db=db) == \
                _old_profit_loss(db, 1, period)

    def test_profit_loss_spot_values(self, db):
        d = profit_loss(business_unit_id=1, period="2026-09", user=CHAIRMAN, db=db)
        assert d["total_income"] == 115000.0  # 100000 sales + 20000 IC - 5000 reversal
        assert d["total_expenses"] == 40000.0
        assert d["net_profit"] == 75000.0
        d = profit_loss(business_unit_id=1, period="2026-10", user=CHAIRMAN, db=db)
        assert (d["total_income"], d["total_expenses"], d["net_profit"]) == (60000.0, 25000.0, 35000.0)
        d = profit_loss(business_unit_id=1, period=None, user=CHAIRMAN, db=db)
        assert (d["total_income"], d["total_expenses"], d["net_profit"]) == (245000.0, 65000.0, 180000.0)

    def test_balance_sheet_matches_old(self, db):
        for period in (None, "2026-09"):
            assert balance_sheet(business_unit_id=1, period=period, user=CHAIRMAN, db=db) == \
                _old_balance_sheet(db, 1, period)
            assert balance_sheet(business_unit_id=2, period=period, user=CHAIRMAN, db=db) == \
                _old_balance_sheet(db, 2, period)

    def test_balance_sheet_spot_values(self, db):
        d = balance_sheet(business_unit_id=1, period="2026-09", user=CHAIRMAN, db=db)
        assert d["total_assets"] == 148000.0  # 1110 133000 + 1180 15000
        assert d["total_liabilities"] == 3000.0
        assert d["total_equity"] == 145000.0  # retained: 185000 income - 40000 expense
        assert d["balanced"] is True

    def test_consolidated_pl_matches_old(self, db):
        for period in (None, "2026-09", "2026-10"):
            assert consolidated_pl(period=period, user=CHAIRMAN, db=db) == \
                _old_consolidated_pl(db, period)

    def test_consolidated_pl_spot_values(self, db):
        d = consolidated_pl(period="2026-09", user=CHAIRMAN, db=db)
        # IC sale 20000 minus 5000 reversal = 15000 eliminated on both sides.
        assert d["intercompany_eliminated"] == 15000.0
        assert d["total_income"] == 180000.0  # 195000 gross - 15000
        assert d["total_expenses"] == 75000.0  # 90000 gross - 15000 (6600)
        assert d["net_profit"] == 105000.0
        d = consolidated_pl(period="2026-10", user=CHAIRMAN, db=db)
        assert d["intercompany_eliminated"] == 0.0
        assert (d["total_income"], d["total_expenses"]) == (60000.0, 25000.0)

    def test_consolidated_bs_matches_old(self, db):
        for period in (None, "2026-09"):
            assert consolidated_bs(period=period, user=CHAIRMAN, db=db) == \
                _old_consolidated_bs(db, period)

    def test_consolidated_bs_spot_values(self, db):
        d = consolidated_bs(period="2026-09", user=CHAIRMAN, db=db)
        # 1180 (15000) eliminated against 2180 (20000) up to the smaller side.
        assert d["intercompany_eliminated"] == 15000.0
        assert d["total_assets"] == 183000.0
        assert d["total_liabilities"] == 8000.0  # 2130 3000 + 2180 5000
        assert d["total_equity"] == 175000.0  # retained BU1 145000 + BU2 30000
        assert d["balanced"] is True

    def test_bu_comparison_matches_old(self, db):
        for period in (None, "2026-09", "2026-10"):
            assert bu_comparison(period=period, user=CHAIRMAN, db=db) == \
                _old_bu_comparison(db, period)

    def test_bu_comparison_spot_values(self, db):
        d = bu_comparison(period="2026-09", user=CHAIRMAN, db=db)
        assert [(i["bu_name"], i["income"], i["expenses"], i["net_profit"]) for i in d["items"]] == [
            ("Shop One", 115000.0, 40000.0, 75000.0),
            ("Shop Two", 80000.0, 50000.0, 30000.0),
        ]

    def test_tax_report_matches_old(self, db):
        assert tax_report(business_unit_id=1, period="2026-09", user=CHAIRMAN, db=db) == \
            _old_tax_report(db, 1, "2026-09")
        assert tax_report(business_unit_id=1, period="2026-10", user=CHAIRMAN, db=db) == \
            _old_tax_report(db, 1, "2026-10")

    def test_tax_report_spot_values(self, db):
        d = tax_report(business_unit_id=1, period="2026-09", user=CHAIRMAN, db=db)
        assert d["opening_balance"] == 0.0
        assert d["tax_charged"] == 5000.0
        assert d["tax_paid"] == 2000.0
        assert d["closing_balance"] == 3000.0
        assert len(d["items"]) == 2

    def test_budgets_match_old(self, db):
        assert list_budgets(business_unit_id=1, period="2026-09", user=CHAIRMAN, db=db) == \
            _old_list_budgets(db, 1, "2026-09")

    def test_budgets_spot_values(self, db):
        d = list_budgets(business_unit_id=1, period="2026-09", user=CHAIRMAN, db=db)
        by_code = {i["account_code"]: i for i in d["items"]}
        assert by_code["4100"]["actual"] == 115000.0
        assert by_code["4100"]["variance"] == 25000.0
        assert by_code["6100"]["actual"] == 40000.0
        assert by_code["6100"]["variance"] == -10000.0
        assert d["total_budget"] == 140000.0
        assert d["total_actual"] == 155000.0

    def test_bank_reconciliation_matches_old(self, db):
        bank = db.query(BankAccount).filter(BankAccount.business_unit_id == 1).one()
        assert bank_reconciliation(bank_id=bank.id, user=CHAIRMAN, db=db) == \
            _old_bank_reconciliation(db, bank.id)

    def test_bank_reconciliation_spot_values(self, db):
        bank = db.query(BankAccount).filter(BankAccount.business_unit_id == 1).one()
        d = bank_reconciliation(bank_id=bank.id, user=CHAIRMAN, db=db)
        assert d["ledger_balance"] == 168000.0
        assert d["difference"] == -500.0  # ledger 168000 vs statement 168500

    def test_finance_summary_matches_old(self, db):
        assert finance_summary(period="2026-09", user=CHAIRMAN, db=db) == \
            _old_finance_summary(db, "2026-09")
        assert finance_summary(period="2026-10", user=CHAIRMAN, db=db) == \
            _old_finance_summary(db, "2026-10")

    def test_finance_summary_spot_values(self, db):
        d = finance_summary(period="2026-09", user=CHAIRMAN, db=db)
        assert d["income"] == 195000.0  # gross group income, no IC elimination
        assert d["expenses"] == 90000.0
        assert d["net_profit"] == 105000.0
        assert d["cash_position"] == 218000.0  # BU1 168000 + BU2 50000
        assert d["ar_outstanding"] == 45500.0  # 5500 + 30000 + 10000
        assert d["ap_outstanding"] == 43000.0  # 8000 + 30000 + 5000
        assert d["tax_payable"] == 3000.0
        assert d["has_books"] is True

    def test_cash_flow_matches_old(self, db):
        assert cash_flow_report(business_unit_id=1, period="2026-09", user=CHAIRMAN, db=db) == \
            _old_cash_flow(db, 1, "2026-09")
        assert cash_flow_report(business_unit_id=2, period="2026-09", user=CHAIRMAN, db=db) == \
            _old_cash_flow(db, 2, "2026-09")

    def test_cash_flow_spot_values(self, db):
        d = cash_flow_report(business_unit_id=1, period="2026-09", user=CHAIRMAN, db=db)
        assert d["opening_cash"] == 70000.0  # Aug sale
        assert d["operating"] == 63000.0  # +100000 -40000 +5000 -2000
        assert d["investing"] == 0.0
        assert d["financing"] == 0.0
        assert d["net_change"] == 63000.0
        assert d["closing_cash"] == 133000.0
        assert len(d["items"]) == 4  # IC entries touch no cash -> skipped

    def test_posted_totals_collapses_rows(self, db):
        # PM-1: the aggregate returns one row per account (6 for BU1),
        # where the old loader returned every line (18 for BU1).
        assert len(_posted_totals(db, 1)) == 6
        assert len(_old_posted_lines(db, 1)) == 18
        assert len(_posted_totals(db, 1, start=date(2026, 9, 1), end=date(2026, 9, 30))) == 5


class TestQueryCounts:
    """The PM-1/PM-3 rewrites must stay O(1) queries per report, not
    O(rows). Bounds are generous; the old per-row code exceeds them by
    3x+ on this fixture."""

    def _count(self, db, fn):
        engine = db.test_engine
        seen = []

        def _before(conn, cursor, statement, parameters, context, executemany):
            seen.append(statement)

        event.listen(engine, "before_cursor_execute", _before)
        try:
            fn()
        finally:
            event.remove(engine, "before_cursor_execute", _before)
        return len(seen)

    def test_trial_balance_is_one_query(self, db):
        assert self._count(db, lambda: trial_balance(
            business_unit_id=1, period="2026-09", user=CHAIRMAN, db=db)) <= 2

    def test_consolidated_pl_is_three_queries(self, db):
        assert self._count(db, lambda: consolidated_pl(
            period="2026-09", user=CHAIRMAN, db=db)) <= 4

    def test_aging_is_bounded(self, db):
        assert self._count(db, lambda: aging_report(user=CHAIRMAN, db=db)) <= 12

    def test_finance_summary_is_bounded(self, db):
        assert self._count(db, lambda: finance_summary(
            period="2026-09", user=CHAIRMAN, db=db)) <= 12


# ------------------------------------------------------------------ PM-2 / PM-3


class TestJournalExport:
    def test_journal_export_filters_period(self, db):
        _, _, rows = _export_rows("journal", db, CHAIRMAN, 1, "2026-09")
        assert len(rows) == 12  # 6 Sep entries x 2 lines
        _, _, rows = _export_rows("journal", db, CHAIRMAN, 1, "2026-10")
        assert len(rows) == 4  # 2 Oct entries x 2 lines
        _, _, rows = _export_rows("journal", db, CHAIRMAN, 1, None)
        assert len(rows) == 18  # all BU1 entries
        _, _, rows = _export_rows("journal", db, CHAIRMAN, 2, "2026-09")
        assert len(rows) == 6  # BU2 Sep: 3 entries x 2 lines

    def test_journal_export_cap(self, db, monkeypatch):
        import src.routers.accounting as acc
        monkeypatch.setattr(acc, "JOURNAL_EXPORT_MAX_ENTRIES", 2)
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            _export_rows("journal", db, CHAIRMAN, 1, "2026-09")
        assert exc.value.status_code == 400
        assert "narrow the period" in exc.value.detail

    def test_export_report_xlsx_streams(self, db):
        # PM-4: the accounting xlsx path is a write-only workbook streamed
        # in chunks, not a single BytesIO.
        resp = export_report(report="journal", format="xlsx", business_unit_id=1,
                             period="2026-09", user=CHAIRMAN, db=db)
        body = _drain(resp)
        assert body[:2] == b"PK"  # a real .xlsx zip
        from openpyxl import load_workbook
        from io import BytesIO
        wb = load_workbook(BytesIO(body), read_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        assert rows[0] == ("Journal",)  # title row
        assert rows[3][:3] == ("Date", "Entry No", "Code")  # header row
        assert len(rows) == 16  # title + subtitle + blank + header + 12 journal lines


class TestBatchHelpers:
    def test_invoice_amounts_batch_matches_per_invoice(self, db):
        invs = db.query(SalesInvoice).all()
        batch = invoice_amounts_batch(db, invs)
        assert set(batch) == {i.id for i in invs}
        for inv in invs:
            assert batch[inv.id] == invoice_amounts(db, inv)
        assert invoice_amounts_batch(db, []) == {}

    def test_po_net_totals_batch_matches(self, db):
        pos = db.query(PurchaseOrder).all()
        batch = po_net_totals_batch(db, pos)
        for po in pos:
            assert batch[po.id] == _old_po_net(db, po)
        assert batch[db.query(PurchaseOrder).filter(
            PurchaseOrder.po_no == "PO-001").one().id] == D("8000")
        assert po_net_totals_batch(db, []) == {}

    def test_aging_matches_old(self, db):
        assert aging_report(user=CHAIRMAN, db=db) == _old_aging(db)
        assert aging_report(business_unit_id=1, user=CHAIRMAN, db=db) == _old_aging(db, 1)

    def test_aging_spot_values(self, db):
        d = aging_report(user=CHAIRMAN, db=db)
        assert d["ar"]["total"] == 45500.0
        assert d["ap"]["total"] == 43000.0
        ar_by_no = {i["invoice_no"]: i for i in d["ar"]["items"]}
        assert ar_by_no["INV-001"]["balance"] == 5500.0
        assert ar_by_no["INV-002"]["balance"] == 30000.0
        assert ar_by_no["INV-004"]["balance"] == 10000.0
        assert "INV-003" not in ar_by_no  # draft
        ap_by_no = {i["po_no"]: i for i in d["ap"]["items"]}
        assert ap_by_no["PO-001"]["balance"] == 8000.0
        assert ap_by_no["PO-002"]["balance"] == 30000.0
        assert ap_by_no["PO-004"]["balance"] == 5000.0
        assert "PO-003" not in ap_by_no  # draft

    def test_customer_statement_matches_old(self, db):
        cust = db.query(Customer).filter(Customer.name == "Cust One").one()
        resp = customer_statement(customer_id=cust.id, format="xlsx", user=CHAIRMAN, db=db)
        body = _drain(resp)
        from openpyxl import load_workbook
        from io import BytesIO
        wb = load_workbook(BytesIO(body), read_only=True)
        rows = list(wb.active.iter_rows(values_only=True))
        old_rows, tb, tp, tr, bal = _old_statement_rows(db, cust.id)
        # Header rows (title, BU, blank, column header) then one row per invoice.
        data = [r for r in rows[4:] if r[1]]
        assert [r[1] for r in data] == [r["invoice_no"] for r in old_rows]
        assert [r[3] for r in data] == [r["total"] for r in old_rows]
        assert [r[4] for r in data] == [r["paid"] for r in old_rows]
        assert [r[5] for r in data] == [r["balance"] for r in old_rows]
        totals = [r for r in rows if r and r[2] == "TOTALS"][0]
        assert (totals[3], totals[4], totals[5]) == (float(tb), float(tp), float(bal))
        assert (tb, tp, tr, bal) == (D("51000"), D("5000"), D("10500"), D("35500"))


class TestOpsExport:
    def _seed_ops_rows(self, db):
        now = utcnow()
        db.add_all([
            ActionItem(business_unit_id=1, title="Fresh action", assigned_to_id=2,
                       status="pending", created_at=now),
            ActionItem(business_unit_id=1, title="Old action", assigned_to_id=2,
                       status="pending", created_at=now - timedelta(days=200)),
            ExpenseRequest(business_unit_id=1, submitted_by_id=2, title="Taxi",
                           amount=D("5000"), category="transport",
                           created_at=now),
        ])
        db.commit()

    def _xlsx_rows(self, resp):
        from openpyxl import load_workbook
        from io import BytesIO
        body = _drain(resp)
        wb = load_workbook(BytesIO(body), read_only=True)
        return list(wb.active.iter_rows(values_only=True))

    def test_actions_default_90_days_and_names(self, db):
        self._seed_ops_rows(db)
        rows = self._xlsx_rows(export_excel(tab="actions", user=CHAIRMAN, db=db))
        titles = [r[0] for r in rows[1:]]
        assert "Fresh action" in titles
        assert "Old action" not in titles  # PM-4: 90-day default
        assert any(r[4] == "Aye Aye" for r in rows[1:])  # PM-3: batched names

    def test_actions_wide_range_includes_old(self, db):
        self._seed_ops_rows(db)
        rows = self._xlsx_rows(export_excel(
            tab="actions", from_date=date(2026, 1, 1), user=CHAIRMAN, db=db))
        assert "Old action" in [r[0] for r in rows[1:]]

    def test_export_row_cap(self, db, monkeypatch):
        self._seed_ops_rows(db)
        rows = self._xlsx_rows(export_excel(tab="actions", limit=1, user=CHAIRMAN, db=db))
        assert len(rows) == 2  # header + 1
        import src.routers.ops as ops
        monkeypatch.setattr(ops, "OPS_EXPORT_MAX_ROWS", 1)
        rows = self._xlsx_rows(export_excel(tab="actions", limit=5000, user=CHAIRMAN, db=db))
        assert len(rows) == 2  # clamped to the (patched) max

    def test_upload_receipt_is_plain_def(self):
        # PM-6: no event-loop blocking — a plain def, like the CEO/HO submits.
        assert inspect.iscoroutinefunction(upload_expense_receipt) is False


# ------------------------------------------------------------------ Pm-1 / Pm-2 / Pm-3 / Pm-4


class TestCrontabFlock:
    def test_every_line_is_flock_guarded(self):
        repo = Path(__file__).resolve().parents[1]
        lines = [l for l in (repo / "deploy" / "crontab").read_text().splitlines()
                 if l.strip() and not l.strip().startswith("#")]
        assert len(lines) == 4  # Pm-1: no new lines added (coordinator adds reconcile)
        for line in lines:
            assert "flock -E 99 -n" in line
            assert "already running" in line
            assert "exit 0" in line  # a skip is not a failure

    def test_header_documents_the_pattern(self):
        repo = Path(__file__).resolve().parents[1]
        text = (repo / "deploy" / "crontab").read_text()
        assert "flock" in text.splitlines()[0] or "flock" in text


class TestRetentionHeartbeat:
    def _seed_file_db(self, path):
        engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(engine)
        session = sessionmaker(bind=engine)()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        session.add(AuditLog(action="LOGIN_SUCCESS", username="ceo_one",
                             ip_address="1.2.3.4", timestamp=now - timedelta(days=100)))
        session.commit()
        session.close()
        engine.dispose()
        return f"sqlite:///{path}"

    def test_real_run_writes_heartbeat(self, tmp_path, monkeypatch):
        from src.scripts.audit_retention import run
        url = self._seed_file_db(tmp_path / "audit.db")
        monkeypatch.setattr(settings, "MIGRATION_DATABASE_URL", url)
        deleted, cleared = run(90, 30, dry_run=False)
        assert (deleted, cleared) == (1, 0)
        engine = create_engine(url)
        try:
            session = sessionmaker(bind=engine)()
            row = session.query(JobHeartbeat).filter(
                JobHeartbeat.job_name == "audit_retention").one()
            assert row.last_status == "ok"
            assert row.last_run_at is not None
            # Fresh row: the weekly job is not stale (the daily jobs never
            # ran in this file DB, so they are legitimately flagged).
            assert "audit_retention" not in check_heartbeats(session)
        finally:
            engine.dispose()

    def test_dry_run_writes_no_heartbeat(self, tmp_path, monkeypatch):
        from src.scripts.audit_retention import run
        url = self._seed_file_db(tmp_path / "audit.db")
        monkeypatch.setattr(settings, "MIGRATION_DATABASE_URL", url)
        run(90, 30, dry_run=True)
        engine = create_engine(url)
        try:
            session = sessionmaker(bind=engine)()
            assert session.query(JobHeartbeat).filter(
                JobHeartbeat.job_name == "audit_retention").count() == 0
        finally:
            engine.dispose()


class TestLongReportTimeout:
    def test_noop_on_sqlite(self, db):
        # Pm-3: SET LOCAL is Postgres-only; on SQLite it must not raise
        # and must not change behaviour.
        assert _long_report_timeout(db) is None
        d = consolidated_pl(period="2026-09", user=CHAIRMAN, db=db)
        assert d["total_income"] == 180000.0

    def test_weekly_heartbeat_grace(self, db):
        # Pm-2: audit_retention is weekly — a missing row is NOT stale
        # between install and the first Sunday run; a stale row IS.
        from src.jobs.log_scan import JOB_MAX_AGE_HOURS
        now = utcnow()
        assert stale_jobs([], now=now, expected=("audit_retention",),
                          never_ran_is_stale=False) == []
        old = now - timedelta(hours=JOB_MAX_AGE_HOURS["audit_retention"] + 1)
        assert stale_jobs([("audit_retention", old)], now=now,
                          expected=("audit_retention",),
                          never_ran_is_stale=False) == ["audit_retention"]
        fresh = now - timedelta(hours=1)
        assert stale_jobs([("audit_retention", fresh)], now=now,
                          expected=("audit_retention",),
                          never_ran_is_stale=False) == []


class TestNoUtcnow:
    def test_no_datetime_utcnow_call_sites(self):
        # Pm-4: the literal is built by concatenation so this file itself
        # does not contain the call it forbids.
        needle = "datetime.utcnow" + "("
        repo = Path(__file__).resolve().parents[1]
        hits = []
        for root in (repo / "src", repo / "tests"):
            for path in root.rglob("*.py"):
                if needle in path.read_text():
                    hits.append(str(path.relative_to(repo)))
        assert hits == []


# ------------------------------------------------------------------ Cm-5 / SM-14


class TestPoolSignals:
    def test_counts_pool_waits_and_lock_timeouts(self):
        lines = [
            "2026-11-09 ERROR QueuePool limit 10 overflow 5 reached, connection timed out",
            "sqlalchemy.exc.TimeoutError: QueuePool ... pool timeout",
            "503 Database unavailable — please retry shortly",
            "psycopg2.errors.LockNotAvailable: lock timeout",
            "ERROR: deadlock detected (40P01)",
            "could not obtain lock on row (55P03)",
            "INFO request ok",
        ]
        assert count_pool_signals(lines) == {"pool_waits": 3, "lock_timeouts": 3}
        assert count_pool_signals(["INFO all quiet"]) == {"pool_waits": 0, "lock_timeouts": 0}

    def test_count_signals_shape_unchanged(self):
        # Cm-5 kept the pool counter separate: the old dict is pinned.
        assert count_signals(["ERROR journal failed", "LOGIN_LOCKOUT x"]) == {
            "total": 2, "errors": 1, "journal_failed": 1, "lockouts": 1}


class TestAuditGapCheck:
    def test_clean_when_audit_rows_match(self, db):
        now = utcnow()
        po = db.query(PurchaseOrder).filter(PurchaseOrder.po_no == "PO-001").one()
        po.received_at = now
        db.add(AuditLog(action="RECEIVE_PO", username="chairman", timestamp=now))
        db.commit()
        gaps = check_audit_gaps(db, since=now - timedelta(hours=1))
        assert not any(g.startswith("audit gap: RECEIVE_PO") for g in gaps)

    def test_missing_audit_row_is_a_gap(self, db):
        now = utcnow()
        po = db.query(PurchaseOrder).filter(PurchaseOrder.po_no == "PO-001").one()
        po.received_at = now
        db.commit()
        gaps = check_audit_gaps(db, since=now - timedelta(hours=1))
        assert "audit gap: RECEIVE_PO expected 1, wrote 0" in gaps

    def test_rejected_advance_does_not_count(self, db):
        # SM-14: approved_at is stamped by reject too ("decided at") —
        # only approved rows expect an APPROVE_ADVANCE audit row.
        now = utcnow()
        emp = Employee(business_unit_id=1, name="Staff One", base_salary=D("100000"))
        db.add(emp)
        db.flush()
        db.add(StaffAdvance(employee_id=emp.id, business_unit_id=1, amount=D("50000"),
                            remaining_amount=D("50000"), monthly_deduction=D("10000"),
                            advance_date=date(2026, 11, 9), status="rejected",
                            approved_at=now))
        db.commit()
        gaps = check_audit_gaps(db, since=now - timedelta(hours=1))
        assert not any(g.startswith("audit gap: APPROVE_ADVANCE") for g in gaps)
        adv = db.query(StaffAdvance).one()
        adv.status = "approved"
        db.commit()
        gaps = check_audit_gaps(db, since=now - timedelta(hours=1))
        assert "audit gap: APPROVE_ADVANCE expected 1, wrote 0" in gaps


# ------------------------------------------------------------------ PM-5 / PM-7 / TZ


class TestPoolAndDeploy:
    def test_pool_settings(self):
        # PM-5: modest raise once reports were cheap — 5+5 per instance,
        # 10 per instance, 20 peak across two instances of a ~22-connection
        # budget. pool_pre_ping stays (the SELECT 1 probe is pinned by
        # TestGetDbRetry and keeps the one-retry path).
        repo = Path(__file__).resolve().parents[1]
        text = (repo / "src" / "database.py").read_text()
        assert "pool_size=5" in text
        assert "max_overflow=5" in text
        assert '"pool_pre_ping": True' in text

    def test_deploy_script(self):
        repo = Path(__file__).resolve().parents[1]
        script = repo / "scripts" / "deploy.sh"
        assert script.exists()
        assert script.stat().st_mode & stat.S_IXUSR  # executable
        text = script.read_text()
        assert "stop app" in text
        assert "run --rm" in text and "migrate" in text
        assert "up -d" in text
        assert "/readyz" in text
        assert "downtime" in text.lower()

    def test_timezone_defence_in_depth(self):
        repo = Path(__file__).resolve().parents[1]
        assert "TZ=Asia/Yangon" in (repo / "Dockerfile").read_text()
        compose = (repo / "docker-compose.prod.yml").read_text()
        assert compose.count("TZ: Asia/Yangon") >= 2  # app + migrate services
        assert "scripts/deploy.sh" in compose
