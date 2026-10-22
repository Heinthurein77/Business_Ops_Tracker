"""Tests for BU-scoped accountants in the accounting API (in-memory SQLite).

Model: an accountant assigned to a BU (business_unit_id set) is pinned to
that BU across the whole accounting router and locked out of group-level
views; a legacy accountant with business_unit_id NULL, and Chairman/HO,
keep group-wide access.

Run with: ENVIRONMENT=test DATABASE_URL=sqlite:// \
    .venv/bin/python -m pytest tests/test_accountant_scoping.py
"""
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models import Account, Base, BusinessUnit
from src.routers.accounting import (
    BudgetIn, ExchangeRateIn, IntercompanyIn, JournalIn, JournalLineIn,
    PeriodAction, PostingRuleIn, bu_comparison, close_period,
    consolidated_pl, create_entry, create_exchange_rate, create_intercompany,
    create_journal, export_report, finance_summary, get_coa,
    list_business_units, list_intercompany, list_journal, list_periods,
    list_posting_rules, opening_balance_template, post_journal, profit_loss,
    provision_bu, upsert_budget, upsert_posting_rule,
)

CHAIRMAN = SimpleNamespace(id=1, role="Chairman", business_unit_id=None)
SCOPED = SimpleNamespace(id=3, role="Accountant", business_unit_id=1)
LEGACY = SimpleNamespace(id=4, role="Accountant", business_unit_id=None)


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([
        BusinessUnit(id=1, code="BU1", name="Shop One", nature="retail"),
        BusinessUnit(id=2, code="BU2", name="Shop Two", nature="retail"),
    ])
    session.commit()
    provision_bu(1, "retail", CHAIRMAN, session)
    provision_bu(2, "retail", CHAIRMAN, session)

    def acc(bu_id, code):
        return session.query(Account).filter(
            Account.business_unit_id == bu_id, Account.code == code).first()

    # one posted sale per BU so the P&Ls have distinct numbers
    create_entry(session, 1, date(2026, 10, 5), "BU1 sale", "manual", 1,
                 [{"account_id": acc(1, "1110").id, "debit": 1000, "credit": 0},
                  {"account_id": acc(1, "4100").id, "debit": 0, "credit": 1000}],
                 post=True)
    create_entry(session, 2, date(2026, 10, 5), "BU2 sale", "manual", 1,
                 [{"account_id": acc(2, "1110").id, "debit": 5000, "credit": 0},
                  {"account_id": acc(2, "4100").id, "debit": 0, "credit": 5000}],
                 post=True)
    session.commit()
    session.test_acc = acc
    yield session
    session.close()


def assert_403(fn, *args, **kwargs):
    with pytest.raises(HTTPException) as e:
        fn(*args, **kwargs)
    assert e.value.status_code == 403


# ------------------------------------------------------------- BU picker

def test_business_units_scoped_accountant_sees_only_own(db):
    out = list_business_units(user=SCOPED, db=db)
    assert out["scope"] == "bu"
    assert [i["bu_id"] for i in out["items"]] == [1]
    item = out["items"][0]
    assert item["bu_name"] == "Shop One" and item["has_books"] is True
    assert item["account_count"] > 0


def test_business_units_group_callers_see_all(db):
    for user in (CHAIRMAN, LEGACY):
        out = list_business_units(user=user, db=db)
        assert out["scope"] == "group"
        assert [i["bu_id"] for i in out["items"]] == [1, 2]


# ------------------------------------------------------------ BU reports

def test_scoped_accountant_reports_own_bu_only(db):
    pl = profit_loss(business_unit_id=1, period="2026-10", user=SCOPED, db=db)
    assert pl["total_income"] == 1000
    assert_403(profit_loss, business_unit_id=2, period="2026-10", user=SCOPED, db=db)
    # legacy + chairman keep cross-BU access
    assert profit_loss(business_unit_id=2, period="2026-10", user=LEGACY, db=db)["total_income"] == 5000
    assert profit_loss(business_unit_id=2, period="2026-10", user=CHAIRMAN, db=db)["total_income"] == 5000


def test_scoped_accountant_coa_and_periods(db):
    assert get_coa(business_unit_id=1, user=SCOPED, db=db)["items"]
    assert_403(get_coa, business_unit_id=2, user=SCOPED, db=db)
    assert list_periods(business_unit_id=1, user=SCOPED, db=db)["items"] is not None
    assert_403(list_periods, business_unit_id=2, user=SCOPED, db=db)
    assert_403(close_period, PeriodAction(business_unit_id=2, period="2026-10"),
               user=SCOPED, db=db)
    out = close_period(PeriodAction(business_unit_id=1, period="2026-10"),
                       user=SCOPED, db=db)
    assert out["status"] == "closed"


def test_scoped_accountant_journal(db):
    acc = db.test_acc
    payload = JournalIn(
        business_unit_id=2, entry_date=date(2026, 10, 6), description="nope",
        lines=[JournalLineIn(account_id=acc(2, "1110").id, debit=10),
               JournalLineIn(account_id=acc(2, "4100").id, credit=10)])
    assert_403(create_journal, payload, user=SCOPED, db=db)
    assert_403(list_journal, business_unit_id=2, user=SCOPED, db=db)
    assert list_journal(business_unit_id=1, user=SCOPED, db=db)["items"]
    # posting another BU's draft is blocked too
    draft = create_entry(db, 2, date(2026, 10, 6), "BU2 draft", "manual", 1,
                         [{"account_id": acc(2, "1110").id, "debit": 10, "credit": 0},
                          {"account_id": acc(2, "4100").id, "debit": 0, "credit": 10}])
    db.commit()
    assert_403(post_journal, draft.id, user=SCOPED, db=db)


def test_scoped_accountant_export(db):
    assert_403(export_report, report="pl", business_unit_id=2, user=SCOPED, db=db)
    # no BU named -> forced to their own BU and succeeds
    resp = export_report(report="pl", business_unit_id=None, user=SCOPED, db=db)
    assert resp.status_code == 200
    assert_403(export_report, report="consolidated-pl", user=SCOPED, db=db)
    assert export_report(report="consolidated-pl", user=LEGACY, db=db).status_code == 200


def test_scoped_accountant_opening_template_and_budgets(db):
    assert opening_balance_template(business_unit_id=1, user=SCOPED, db=db).status_code == 200
    assert_403(opening_balance_template, business_unit_id=2, user=SCOPED, db=db)
    assert_403(upsert_budget,
               BudgetIn(business_unit_id=2, period="2026-10", account_code="6100", amount=100),
               user=SCOPED, db=db)
    out = upsert_budget(BudgetIn(business_unit_id=1, period="2026-10",
                                 account_code="6100", amount=100),
                        user=SCOPED, db=db)
    assert out["ok"] is True


# ------------------------------------------------------------ group views

def test_scoped_accountant_group_views_403(db):
    assert_403(consolidated_pl, period="2026-10", user=SCOPED, db=db)
    assert_403(bu_comparison, period="2026-10", user=SCOPED, db=db)
    assert_403(finance_summary, period="2026-10", user=SCOPED, db=db)
    assert_403(list_intercompany, user=SCOPED, db=db)
    payload = IntercompanyIn(from_business_unit_id=1, to_business_unit_id=2,
                             amount=100, txn_date=date(2026, 10, 1))
    assert_403(create_intercompany, payload, user=SCOPED, db=db)
    # group callers unaffected
    assert consolidated_pl(period="2026-10", user=LEGACY, db=db)["total_income"] == 6000
    assert finance_summary(period="2026-10", user=CHAIRMAN, db=db)["income"] == 6000


# ------------------------------------------------------- rules & rates

def test_scoped_accountant_posting_rules(db):
    own = PostingRuleIn(business_unit_id=1, source_field="daily_financial.revenue",
                        debit_account_code="1120", credit_account_code="4100")
    assert upsert_posting_rule(own, user=SCOPED, db=db)["ok"] is True
    other = PostingRuleIn(business_unit_id=2, source_field="daily_financial.revenue",
                          debit_account_code="1120", credit_account_code="4100")
    assert_403(upsert_posting_rule, other, user=SCOPED, db=db)
    group = PostingRuleIn(business_unit_id=None, source_field="daily_financial.revenue",
                          debit_account_code="1120", credit_account_code="4100")
    assert_403(upsert_posting_rule, group, user=SCOPED, db=db)
    # listing with no BU named: own BU rules + group rules only
    upsert_posting_rule(other, user=CHAIRMAN, db=db)
    scopes = {r["business_unit_id"] for r in list_posting_rules(user=SCOPED, db=db)["items"]}
    assert scopes <= {1, None}
    assert_403(list_posting_rules, business_unit_id=2, user=SCOPED, db=db)


def test_scoped_accountant_exchange_rate_write_403(db):
    payload = ExchangeRateIn(currency="USD", rate_date=date(2026, 10, 1), rate=2100)
    assert_403(create_exchange_rate, payload, user=SCOPED, db=db)
    assert create_exchange_rate(payload, user=LEGACY, db=db)["ok"] is True
