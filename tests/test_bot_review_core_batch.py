"""Regression tests for the BOT review core-accounting batch (2026-11-09):
C4 is a compose file (not unit-testable here); M-A1, M-B2/B3/B5/B6/B11/B12/B13,
M-C10/C14, M-D3/D4, m1/m2/m4/m5/m8/m9/m10/m11/m12/m13 and Q3 (HO auto-post).

Run with: ENVIRONMENT=test DATABASE_URL=sqlite:// \
    .venv/bin/python -m pytest tests/test_bot_review_core_batch.py
"""
import asyncio
import time
from collections import deque
from datetime import date
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from src.auth import hash_password
from src.client_ip import _from_named_header
from src.crypto import EncryptedDate
from src.edge import EdgeMiddleware
from src.models import (
    Account, AuditLog, Base, BusinessUnit, DailyFinancial, FixedAsset,
    IntercompanyTransaction, JournalEntry, PurchaseOrder, PurchaseOrderLine,
    PurchaseReturn, PurchaseReturnLine, Supplier, User,
)
from src.routers.accounting import (
    AccountIn, AccountUpdate, BudgetIn, DepreciateIn, ExchangeRateIn, FixedAssetIn,
    IntercompanyIn, JournalIn, JournalLineIn, PeriodAction, PostingRuleIn,
    auto_post_daily_financial, cash_flow_report, close_period,
    consolidated_pl, create_account, create_entry, create_exchange_rate,
    create_fixed_asset, create_intercompany, create_journal,
    finance_summary, latest_rate, list_journal, opening_balance,
    provision_bu, run_depreciation, update_account, upsert_budget,
    upsert_posting_rule, _reverse_entry,
)
from src.routers.admin import report_logs
from src.routers.auth import login as login_endpoint
from src.routers.ceo import submit_report as ceo_submit_report
from src.routers.ho import submit_report as ho_submit_report
from src.routers.users import create_user, reset_user_password
from src.schemas import CreateUserRequest, LoginRequest

CHAIRMAN = SimpleNamespace(id=1, role="Chairman", business_unit_id=None, username="boss")
HO = SimpleNamespace(id=2, role="HO", business_unit_id=None, username="ho_staff")
SCOPED = SimpleNamespace(id=3, role="Accountant", business_unit_id=1, username="acct1")
LEGACY = SimpleNamespace(id=4, role="Accountant", business_unit_id=None, username="acct_old")
REQUEST = SimpleNamespace(headers={}, client=SimpleNamespace(host="127.0.0.1"))


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([
        BusinessUnit(id=1, code="BU1", name="Shop One", nature="retail"),
        BusinessUnit(id=2, code="BU2", name="Shop Two", nature="retail"),
        User(username="boss", hashed_password="x", role="Chairman", full_name="Boss"),
    ])
    session.commit()
    provision_bu(1, "retail", CHAIRMAN, session)
    provision_bu(2, "retail", CHAIRMAN, session)

    def acc(bu_id, code):
        return session.query(Account).filter(
            Account.business_unit_id == bu_id, Account.code == code).first()

    create_entry(session, 1, date(2026, 10, 5), "BU1 sale", "manual", 1,
                 [{"account_id": acc(1, "1110").id, "debit": Decimal("1000"), "credit": 0},
                  {"account_id": acc(1, "4100").id, "debit": 0, "credit": Decimal("1000")}],
                 post=True)
    create_entry(session, 2, date(2026, 10, 5), "BU2 sale", "manual", 1,
                 [{"account_id": acc(2, "1110").id, "debit": Decimal("5000"), "credit": 0},
                  {"account_id": acc(2, "4100").id, "debit": 0, "credit": Decimal("5000")}],
                 post=True)
    session.commit()
    session.test_acc = acc
    yield session
    session.close()


def line(db, bu_id, code, debit=0, credit=0):
    return {"account_id": db.test_acc(bu_id, code).id,
            "debit": Decimal(str(debit)), "credit": Decimal(str(credit))}


def assert_400(fn, *args, **kwargs):
    with pytest.raises(HTTPException) as e:
        fn(*args, **kwargs)
    assert e.value.status_code == 400


def assert_403(fn, *args, **kwargs):
    with pytest.raises(HTTPException) as e:
        fn(*args, **kwargs)
    assert e.value.status_code == 403


def assert_404(fn, *args, **kwargs):
    with pytest.raises(HTTPException) as e:
        fn(*args, **kwargs)
    assert e.value.status_code == 404


# ------------------------------------------------------------------ M-A1

def test_close_period_chairman_ho_only_and_audited(db):
    assert_403(close_period, PeriodAction(business_unit_id=1, period="2026-10"),
               user=SCOPED, db=db)
    out = close_period(PeriodAction(business_unit_id=1, period="2026-10"),
                       user=CHAIRMAN, db=db)
    assert out["status"] == "closed"
    assert db.query(AuditLog).filter(AuditLog.action == "FISCAL_PERIOD_CLOSED").count() == 1


def test_close_period_rejects_future(db):
    assert_400(close_period, PeriodAction(business_unit_id=1, period="2999-01"),
               user=CHAIRMAN, db=db)


def test_close_period_refuses_with_unposted_drafts(db):
    create_entry(db, 1, date(2026, 10, 6), "draft", "manual", 1,
                 [line(db, 1, "1110", debit=10), line(db, 1, "4100", credit=10)])
    db.commit()
    with pytest.raises(HTTPException) as e:
        close_period(PeriodAction(business_unit_id=1, period="2026-10"),
                     user=CHAIRMAN, db=db)
    assert e.value.status_code == 400 and "draft" in e.value.detail.lower()


# ------------------------------------------------------------------ M-B2

def test_intercompany_missing_account_leaves_no_transaction(db):
    payload = IntercompanyIn(from_business_unit_id=1, to_business_unit_id=2,
                             amount=100000, txn_date=date(2026, 10, 10),
                             buyer_account_code="9999")
    assert_400(create_intercompany, payload, user=CHAIRMAN, db=db)
    assert db.query(IntercompanyTransaction).count() == 0
    assert db.query(JournalEntry).filter(JournalEntry.source == "intercompany").count() == 0


def test_intercompany_success_posts_both_journals(db):
    t = create_intercompany(IntercompanyIn(
        from_business_unit_id=1, to_business_unit_id=2, amount=100000,
        description="Shared logistics", txn_date=date(2026, 10, 10),
        buyer_account_code="6400"), user=CHAIRMAN, db=db)
    assert t["status"] == "open"
    assert db.query(JournalEntry).filter(JournalEntry.source == "intercompany").count() == 2


# ------------------------------------------------------------------ M-B3

def _make_asset(db, bu_id=1):
    create_fixed_asset(FixedAssetIn(business_unit_id=bu_id, name="Truck",
                                    purchase_date=date(2026, 1, 15), cost=1200000,
                                    useful_life_months=12), user=LEGACY, db=db)
    return db.query(FixedAsset).filter(FixedAsset.business_unit_id == bu_id).first()


def test_depreciation_closed_period_marks_nothing(db):
    asset = _make_asset(db)
    close_period(PeriodAction(business_unit_id=1, period="2026-10"),
                 user=CHAIRMAN, db=db)
    assert_400(run_depreciation, DepreciateIn(business_unit_id=1, period="2026-10"),
               user=LEGACY, db=db)
    db.refresh(asset)
    assert asset.accumulated_depreciation == 0
    assert asset.last_depreciated_period is None
    assert db.query(JournalEntry).filter(JournalEntry.source == "depreciation").count() == 0


def test_depreciation_missing_accounts_errors_without_marking(db):
    db.add(BusinessUnit(id=3, code="BU3", name="Shop Three"))
    db.commit()
    asset = _make_asset(db, bu_id=3)
    assert_400(run_depreciation, DepreciateIn(business_unit_id=3, period="2026-10"),
               user=LEGACY, db=db)
    db.refresh(asset)
    assert asset.accumulated_depreciation == 0
    assert asset.last_depreciated_period is None


# ------------------------------------------------------------------ M-B5

def test_reversal_uses_original_entry_date(db):
    e = create_entry(db, 1, date(2026, 10, 1), "sale", "manual", 1,
                     [line(db, 1, "1110", debit=1000), line(db, 1, "4100", credit=1000)],
                     post=True)
    db.commit()
    rev = _reverse_entry(db, e, None)
    db.commit()
    assert rev.entry_date == date(2026, 10, 1)


def test_auto_post_resubmit_into_closed_period_raises(db):
    df = DailyFinancial(business_unit_id=1, report_date=date(2026, 10, 20),
                        revenue=Decimal("500000"), cash_flow=Decimal("0"),
                        receivable=Decimal("0"), payable=Decimal("0"),
                        expenses=Decimal("0"), submitted_by=None)
    db.add(df)
    db.commit()
    auto_post_daily_financial(db, df, None)
    close_period(PeriodAction(business_unit_id=1, period="2026-10"),
                 user=CHAIRMAN, db=db)
    df.revenue = Decimal("600000")
    db.commit()
    with pytest.raises(HTTPException) as e:
        auto_post_daily_financial(db, df, None)
    assert "closed" in e.value.detail.lower()


def test_ceo_submit_surfaces_auto_post_failure(db):
    close_period(PeriodAction(business_unit_id=1, period="2026-11"),
                 user=CHAIRMAN, db=db)
    ceo = SimpleNamespace(id=9101, role="CEO", business_unit_id=1, username="ceo1")
    # Direct calls must supply every Form param (an omitted one stays a
    # Form(...) object) — same full-defaults pattern as test_ceo_calendar.
    out = ceo_submit_report(
        request=REQUEST, user=ceo, db=db, report_date="",
        revenue="100000", cash_flow="0", receivable="0", payable="0",
        other_amount="", financial_other_notes="", meetings_attended="",
        meetings_count="", site_visits="", key_decisions="", key_achievement="",
        next_day_schedule="", problems_today="", help_needed="", gym_percent="",
        swimming_percent="", cycle_percent="", activity_other_notes="",
        daily_steps="", heart_rate="", sleep_hours="", bp_systolic="",
        bp_diastolic="", stress_level="", energy_mood="", weight_kg="",
        is_sick="false", health_gym_percent=None, health_swimming_percent=None,
        health_cycle_percent=None, health_other_notes="", event_flag="false",
        meeting_flag="false", golf_flag="false", social_other_notes="",
        attachment=None)
    assert out["ok"] is True
    assert out.get("auto_post_failed") is True
    assert "closed" in out["auto_post_error"].lower()
    assert db.query(AuditLog).filter(
        AuditLog.action == "DAILY_REPORT_AUTO_POST_FAILED").count() == 1


# ------------------------------------------------------------------ M-B6

def test_consolidated_pl_eliminates_intercompany(db):
    create_intercompany(IntercompanyIn(
        from_business_unit_id=1, to_business_unit_id=2, amount=300000,
        description="Shared logistics", txn_date=date(2026, 10, 10),
        buyer_account_code="6400"), user=CHAIRMAN, db=db)
    out = consolidated_pl(period="2026-10", user=LEGACY, db=db)
    assert out["intercompany_eliminated"] == 300000
    assert out["total_income"] == 6000  # BU1+BU2 sales; IC sale eliminated
    assert out["total_expenses"] == 0  # buyer 6400 charge eliminated
    assert out["net_profit"] == 6000


# ----------------------------------------------------------------- M-B11

def test_draft_journal_is_validated(db):
    payload = JournalIn(
        business_unit_id=1, entry_date=date(2026, 10, 6), description="bad",
        lines=[JournalLineIn(account_id=db.test_acc(1, "1110").id, debit=10),
               JournalLineIn(account_id=db.test_acc(1, "4100").id, credit=9)])
    assert_400(create_journal, payload, user=SCOPED, db=db)


def test_journal_requires_two_lines_and_short_strings(db):
    with pytest.raises(ValidationError):
        JournalIn(business_unit_id=1, entry_date=date(2026, 10, 6), lines=[
            JournalLineIn(account_id=db.test_acc(1, "1110").id, debit=10)])
    with pytest.raises(ValidationError):
        JournalIn(business_unit_id=1, entry_date=date(2026, 10, 6),
                  description="x" * 501, lines=[
                      JournalLineIn(account_id=db.test_acc(1, "1110").id, debit=10),
                      JournalLineIn(account_id=db.test_acc(1, "4100").id, credit=10)])
    with pytest.raises(ValidationError):
        JournalLineIn(account_id=db.test_acc(1, "1110").id, debit=-5,
                      credit=0)


def test_create_entry_draft_is_validated(db):
    with pytest.raises(HTTPException):
        create_entry(db, 1, date(2026, 10, 6), "one-liner", "manual", 1,
                     [line(db, 1, "1110", debit=10)])


# ----------------------------------------------------------------- M-B12

def test_journal_list_filters_period_in_sql_and_paginates(db):
    for i in range(305):
        create_entry(db, 1, date(2026, 10, 5), f"bulk {i}", "manual", 1,
                     [line(db, 1, "1110", debit=10), line(db, 1, "4100", credit=10)],
                     post=True)
    for d in (date(2026, 9, 30), date(2026, 9, 29)):
        create_entry(db, 1, d, "september", "manual", 1,
                     [line(db, 1, "1110", debit=10), line(db, 1, "4100", credit=10)],
                     post=True)
    db.commit()
    old = list_journal(business_unit_id=1, period="2026-09", user=SCOPED, db=db)
    assert len(old["items"]) == 2 and old["total"] == 2
    page = list_journal(business_unit_id=1, limit=2, offset=0, user=SCOPED, db=db)
    assert len(page["items"]) == 2 and page["total"] == 308


# ----------------------------------------------------------------- M-B13

def test_cash_flow_classifies_by_dominant_counter_account(db):
    # Cash out 60,000: counters are investing 30,000 (1210), operating
    # 70,000 (6600) and financing 40,000 (2140) — dominant is operating.
    # The old first-line-break rule classified it investing (1210 first).
    create_entry(db, 1, date(2026, 10, 10), "mixed purchase", "manual", 1,
                 [{"account_id": db.test_acc(1, "1210").id, "debit": Decimal("30000"), "credit": 0},
                  {"account_id": db.test_acc(1, "6600").id, "debit": Decimal("70000"), "credit": 0},
                  {"account_id": db.test_acc(1, "1110").id, "debit": 0, "credit": Decimal("60000")},
                  {"account_id": db.test_acc(1, "2140").id, "debit": 0, "credit": Decimal("40000")}],
                 post=True)
    db.commit()
    out = cash_flow_report(1, "2026-10", user=CHAIRMAN, db=db)
    # -60,000 from the mixed entry plus +1,000 operating from the
    # fixture's BU1 sale (Dr 1110 / Cr 4100).
    assert out["operating"] == -59000
    assert out["investing"] == 0
    assert out["financing"] == 0


# ----------------------------------------------------------------- M-C10

def _received_po_with_return(db, return_qty, n=1):
    s = Supplier(business_unit_id=1, name="Vendor")
    db.add(s)
    db.commit()
    # Distinct numbers per call: the M-D5 per-BU unique constraints
    # (workstream D) reject a second PO/return carrying the same number.
    po = PurchaseOrder(business_unit_id=1, supplier_id=s.id, po_no=f"PO-1-{n:04d}",
                       status="received", order_date=date(2026, 10, 1),
                       exchange_rate=Decimal("1"))
    db.add(po)
    db.commit()
    pl = PurchaseOrderLine(po_id=po.id, item_name="Widget", quantity=Decimal("10"),
                           unit_cost=Decimal("1000"))
    db.add(pl)
    db.commit()
    if return_qty:
        r = PurchaseReturn(business_unit_id=1, po_id=po.id, return_no=f"PR-1-{n:04d}",
                           status="posted", return_date=date(2026, 10, 2))
        db.add(r)
        db.commit()
        db.add(PurchaseReturnLine(return_id=r.id, po_line_id=pl.id,
                                  quantity=Decimal(str(return_qty)),
                                  unit_cost=Decimal("1000")))
        db.commit()
    return po


def test_finance_summary_ap_nets_purchase_returns(db):
    _received_po_with_return(db, return_qty=0)
    assert finance_summary(period="2026-10", user=CHAIRMAN, db=db)["ap_outstanding"] == 10000
    _received_po_with_return(db, return_qty=4, n=2)  # second PO + 4-unit return
    out = finance_summary(period="2026-10", user=CHAIRMAN, db=db)
    assert out["ap_outstanding"] == 10000 + 10000 - 4000


# ----------------------------------------------------------------- M-C14

def test_latest_rate_refuses_silent_one_to_one(db):
    assert latest_rate(db, "MMK") == Decimal("1")
    with pytest.raises(HTTPException) as e:
        latest_rate(db, "USD")
    assert "No exchange rate" in e.value.detail
    create_exchange_rate(ExchangeRateIn(
        currency="USD", rate_date=date(2026, 10, 1), rate=2100),
        user=LEGACY, db=db)
    assert latest_rate(db, "USD") == Decimal("2100")


# ------------------------------------------------------------------ M-D3

def test_create_account_validates_code_and_parent(db):
    acc = db.test_acc
    assert_400(create_account, AccountIn(business_unit_id=1, code="ABC", name="Bad",
                                         account_type="asset"), user=SCOPED, db=db)
    assert_400(create_account, AccountIn(business_unit_id=1, code="9999", name="Bad",
                                         account_type="asset"), user=SCOPED, db=db)
    # whitespace duplicate of an existing code
    assert_400(create_account, AccountIn(business_unit_id=1, code=" 1110", name="Dup",
                                         account_type="asset"), user=SCOPED, db=db)
    assert_404(create_account, AccountIn(business_unit_id=1, code="1170", name="Orphan",
                                         account_type="asset", parent_id=99999),
               user=SCOPED, db=db)
    assert_400(create_account, AccountIn(business_unit_id=1, code="1170", name="Bad parent",
                                         account_type="asset",
                                         parent_id=acc(1, "1110").id),
               user=SCOPED, db=db)
    assert_400(create_account, AccountIn(business_unit_id=1, code="1170", name="Wrong BU",
                                         account_type="asset",
                                         parent_id=acc(2, "1100").id),
               user=SCOPED, db=db)
    out = create_account(AccountIn(business_unit_id=1, code="1170", name="New Asset",
                                   account_type="asset", parent_id=acc(1, "1100").id),
                         user=SCOPED, db=db)
    assert out["code"] == "1170"


# ------------------------------------------------------------------ M-D4

def test_budget_requires_real_postable_account(db):
    assert_400(upsert_budget, BudgetIn(business_unit_id=1, period="2026-10",
                                       account_code="9999", amount=100),
               user=SCOPED, db=db)
    assert_400(upsert_budget, BudgetIn(business_unit_id=1, period="2026-10",
                                       account_code="1100", amount=100),
               user=SCOPED, db=db)
    assert upsert_budget(BudgetIn(business_unit_id=1, period="2026-10",
                                  account_code="6100", amount=100),
                         user=SCOPED, db=db)["ok"] is True


def test_posting_rule_validates_codes(db):
    assert_400(upsert_posting_rule, PostingRuleIn(
        business_unit_id=1, source_field="t", debit_account_code="6100",
        credit_account_code="6100"), user=SCOPED, db=db)
    assert_400(upsert_posting_rule, PostingRuleIn(
        business_unit_id=1, source_field="t", debit_account_code="9999",
        credit_account_code="4100"), user=SCOPED, db=db)
    assert_400(upsert_posting_rule, PostingRuleIn(
        business_unit_id=1, source_field="t", debit_account_code="1120",
        credit_account_code="6000"), user=SCOPED, db=db)
    assert upsert_posting_rule(PostingRuleIn(
        business_unit_id=1, source_field="t", debit_account_code="1120",
        credit_account_code="4100"), user=SCOPED, db=db)["ok"] is True
    assert_400(upsert_posting_rule, PostingRuleIn(
        business_unit_id=None, source_field="t", debit_account_code="9999",
        credit_account_code="4100"), user=CHAIRMAN, db=db)
    assert upsert_posting_rule(PostingRuleIn(
        business_unit_id=None, source_field="t", debit_account_code="1120",
        credit_account_code="4100"), user=CHAIRMAN, db=db)["ok"] is True


# ------------------------------------------------------------------ m1/m2

def test_provision_sets_and_backfills_nature(db):
    db.add(BusinessUnit(id=3, code="BU3", name="Shop Three"))
    db.commit()
    provision_bu(3, "retail", CHAIRMAN, db)
    bu = db.query(BusinessUnit).filter(BusinessUnit.id == 3).first()
    assert bu.nature == "retail"
    bu.nature = None
    db.commit()
    provision_bu(3, "retail", CHAIRMAN, db)
    db.refresh(bu)
    assert bu.nature == "retail"


def _upload_opening_balance(db, rows, user=None):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = BytesIO()
    wb.save(buf)

    class FakeUpload:
        filename = "ob.xlsx"

        async def read(self):
            return buf.getvalue()

    u = user or SimpleNamespace(id=None, role="Accountant", business_unit_id=None)
    return asyncio.get_event_loop().run_until_complete(
        opening_balance(business_unit_id=1, as_of=date(2026, 10, 1),
                        file=FakeUpload(), user=u, db=db))


def test_opening_balance_headerless_keeps_first_row(db):
    out = _upload_opening_balance(db, [
        ["1110", 2000000, None],
        ["3100", None, 2000000],
    ])
    assert out["status"] == "posted"
    assert out["total_debit"] == 2000000


def test_opening_balance_rejects_negative_and_too_many_rows(db):
    with pytest.raises(HTTPException) as e:
        _upload_opening_balance(db, [
            ["account_code", "debit", "credit"],
            ["1110", -100, None],
            ["3100", None, 100],
        ])
    assert "negative" in e.value.detail.lower()
    with pytest.raises(HTTPException) as e:
        _upload_opening_balance(db, [
            ["account_code", "debit", "credit"],
            *([["1110", 1, None]] * 501),
        ])
    assert "Too many rows" in e.value.detail


# ------------------------------------------------------------------ m4/m5

def test_report_logs_paginated(db):
    for i in range(3):
        db.add(AuditLog(action="DAILY_REPORT_SUBMITTED", username=f"ceo{i}",
                        details=f"date=2026-10-0{i + 1}"))
    db.add(AuditLog(action="LOGIN_SUCCESS", username="boss", details=""))
    db.commit()
    page1 = report_logs(limit=2, offset=0, user=CHAIRMAN, db=db)
    assert len(page1["logs"]) == 2 and page1["total"] == 3
    page2 = report_logs(limit=2, offset=2, user=CHAIRMAN, db=db)
    assert len(page2["logs"]) == 1
    capped = report_logs(limit=9999, offset=0, user=CHAIRMAN, db=db)
    assert capped["limit"] == 500


def test_export_budget_evicts_empty_buckets(db):
    from src.config import settings

    mw = EdgeMiddleware(lambda scope, receive, send: None)
    stale = time.monotonic() - settings.EDGE_EXPORT_RATE_WINDOW - 10
    mw._export_hits["ip:1.2.3.4"] = deque([stale])
    scope = {"type": "http", "http_version": "1.1", "method": "GET", "scheme": "http",
             "path": "/api/accounting/reports/pl", "query_string": b"", "headers": [],
             "client": ("1.2.3.4", 1234), "server": ("testserver", 80)}
    assert mw._export_budget_retry(scope, {}) is None
    hits = mw._export_hits["ip:1.2.3.4"]
    assert len(hits) == 1 and hits[0] != stale


# ------------------------------------------------------------------ m8

def test_update_account_name_capped_and_deactivation_blocked(db):
    with pytest.raises(ValidationError):
        AccountUpdate(name="x" * 151)
    acc = db.test_acc(1, "1110")  # has a posted sale line
    assert_400(update_account, acc.id, AccountUpdate(is_active=False),
               user=SCOPED, db=db)
    idle = db.test_acc(1, "1150")  # no postings
    out = update_account(idle.id, AccountUpdate(is_active=False), user=SCOPED, db=db)
    assert out["is_active"] is False


# ------------------------------------------------------------------ m9/m10

def _boss(db):
    return db.query(User).filter(User.username == "boss").first()


def test_create_user_rejects_bu_for_ho(db):
    with pytest.raises(HTTPException) as e:
        create_user(CreateUserRequest(username="ho_x", full_name="HO X", role="HO",
                                     business_unit_id=1),
                    None, Response(), _boss(db), db)
    assert e.value.status_code == 400


def test_reset_password_blocked_for_deactivated(db):
    out = create_user(CreateUserRequest(username="acct_z", full_name="Acct Z",
                                       role="Accountant", business_unit_id=1),
                      None, Response(), _boss(db), db)
    target = db.query(User).filter(User.id == out["user_id"]).first()
    target.is_active = False
    db.commit()
    with pytest.raises(HTTPException) as e:
        reset_user_password(target.id, None, Response(), _boss(db), db)
    assert e.value.status_code == 400


# ----------------------------------------------------------------- m11/m12/m13

def test_encrypted_date_rejects_non_iso_legacy(db):
    with pytest.raises(RuntimeError):
        EncryptedDate().process_result_value("not-a-date", None)
    assert EncryptedDate().process_result_value("2001-11-23", None) == date(2001, 11, 23)


def test_login_rejects_over_72_byte_password(db):
    db.add(User(username="victim", hashed_password=hash_password("CorrectHorse1!"),
                role="HO", full_name="Victim", is_active=True))
    db.commit()
    with pytest.raises(HTTPException) as e:
        login_endpoint(LoginRequest(username="victim", password="x" * 73),
                       REQUEST, Response(), db=db)
    assert e.value.status_code == 401


def _starlette_request(headers):
    scope = {"type": "http", "http_version": "1.1", "method": "GET", "scheme": "http",
             "path": "/", "query_string": b"", "headers": headers,
             "client": ("9.9.9.9", 1), "server": ("testserver", 80)}
    return Request(scope)


def test_named_header_rejects_comma_values(db):
    multi = _starlette_request([(b"x-forwarded-for", b"1.2.3.4, 5.6.7.8")])
    assert _from_named_header(multi, "x-forwarded-for") is None
    single = _starlette_request([(b"x-forwarded-for", b"1.2.3.4")])
    assert _from_named_header(single, "x-forwarded-for") == "1.2.3.4"


# ------------------------------------------------------------------ Q3

def _ho_user(user_id):
    return SimpleNamespace(id=user_id, role="HO", business_unit_id=None,
                           username=f"ho_{user_id}")


def _submit_ho(db, user_id, revenue):
    # Direct calls must supply every Form param (an omitted one stays a
    # Form(...) object) — same full-defaults pattern as test_ceo_calendar.
    return ho_submit_report(
        request=REQUEST, revenue=str(revenue), cash_flow="0",
        receivable="0", payable="0", other_amount="",
        financial_other_notes="", meetings_attended="", site_visits="",
        key_decisions="", next_day_schedule="", gym_percent="",
        swimming_percent="", cycle_percent="", activity_other_notes="",
        daily_steps="", heart_rate="", sleep_hours="", bp_systolic="",
        bp_diastolic="", health_other_notes="", event_flag="false",
        meeting_flag="false", golf_flag="false", social_other_notes="",
        attachment=None, user=_ho_user(user_id), db=db)


def test_ho_submit_without_ho_bu_posts_nothing(db):
    out = _submit_ho(db, 9101, 500000)
    assert out == {"ok": True}
    assert db.query(JournalEntry).count() == 2  # fixture sales only


def test_ho_submit_posts_to_ho_bu_and_resubmits(db):
    db.add(BusinessUnit(id=3, code="HO", name="Head Office"))
    db.commit()
    provision_bu(3, "retail", CHAIRMAN, db)
    out = _submit_ho(db, 9102, 500000)
    assert out == {"ok": True}
    entries = db.query(JournalEntry).filter(
        JournalEntry.source == "ho_daily_financial").all()
    assert len(entries) == 1 and entries[0].status == "posted"
    assert entries[0].business_unit_id == 3
    cash = sum(float(l.debit) - float(l.credit) for l in entries[0].lines
               if l.account_id == db.test_acc(3, "1110").id)
    assert cash == 500000
    # resubmit with a corrected figure: old reversed, new posted
    out = _submit_ho(db, 9102, 600000)
    assert out == {"ok": True}
    entries = db.query(JournalEntry).filter(
        JournalEntry.source == "ho_daily_financial").all()
    assert len(entries) == 2
    # A reversed entry keeps status "posted" — reversed_by_id is what
    # marks it superseded (same as the CEO daily flow).
    assert sum(1 for e in entries if e.reversed_by_id is None) == 1
    assert sum(1 for e in entries if e.reversed_by_id is not None) == 1
    posted = next(e for e in entries if e.reversed_by_id is None)
    assert sum(float(l.debit) for l in posted.lines) == 600000


def test_ho_submit_surfaces_posting_failure(db):
    db.add(BusinessUnit(id=3, code="HO", name="Head Office"))
    db.commit()
    provision_bu(3, "retail", CHAIRMAN, db)
    from src.utils import today_local

    close_period(PeriodAction(business_unit_id=3, period=today_local().strftime("%Y-%m")),
                 user=CHAIRMAN, db=db)
    out = _submit_ho(db, 9103, 500000)
    assert out["ok"] is True
    assert out.get("auto_post_failed") is True
    assert "closed" in out["auto_post_error"].lower()
    assert db.query(AuditLog).filter(
        AuditLog.action == "HO_DAILY_REPORT_AUTO_POST_FAILED").count() == 1
