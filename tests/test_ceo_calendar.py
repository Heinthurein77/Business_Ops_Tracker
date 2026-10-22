"""Tests for the CEO activity month calendar (2026-10-22).

Covers: the Chairman/HO endpoint GET /api/admin/ceo-activity-calendar, the
generalized build_ceo_activity_calendar (user_id=None returns the BU's
rows regardless of submitter; a CEO id keeps the personal scoping), the
role guard, and the CEO submit path's explicit report_date (fill/edit
any day of the month from the History calendar).

Run with: ENVIRONMENT=test DATABASE_URL=sqlite:// \
    .venv/bin/python -m pytest tests/test_ceo_calendar.py
"""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.auth import InsufficientRoleException
from src.models import ActivityLog, Base, BusinessUnit, DailyFinancial, User
from src.routers.admin import ceo_activity_calendar, require_admin
from src.routers.ceo import ceo_dashboard, submit_report
from src.utils import build_ceo_activity_calendar, today_local

CHAIRMAN = SimpleNamespace(id=90, role="Chairman", business_unit_id=None)
HO = SimpleNamespace(id=91, role="HO", business_unit_id=None)
REQUEST = SimpleNamespace(headers={}, client=SimpleNamespace(host="127.0.0.1"))


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([
        BusinessUnit(id=1, code="BU1", name="Shop One"),
        BusinessUnit(id=2, code="BU2", name="Shop Two"),
    ])
    ceo = User(username="ceo_one", hashed_password="x", role="CEO", full_name="CEO One",
               business_unit_id=1, is_active=True)
    predecessor = User(username="ceo_old", hashed_password="x", role="CEO", full_name="Old CEO",
                       business_unit_id=1, is_active=False)
    ceo2 = User(username="ceo_two", hashed_password="x", role="CEO", full_name="CEO Two",
                business_unit_id=2, is_active=True)
    session.add_all([ceo, predecessor, ceo2])
    session.commit()
    # the current CEO's own day (+ that day's financial row)
    session.add(ActivityLog(business_unit_id=1, report_date=date(2026, 10, 14),
                            meetings_attended="Board meeting with HO",
                            key_decisions="Approved Q4 budget",
                            submitted_by=ceo.id))
    session.add(DailyFinancial(business_unit_id=1, report_date=date(2026, 10, 14),
                               revenue=Decimal("250000"), expenses=Decimal("0"),
                               net_profit=Decimal("0"), cash_in_hand=Decimal("0"),
                               cash_flow=Decimal("180000"), receivable=Decimal("40000"),
                               payable=Decimal("30000"), submitted_by=ceo.id))
    # a day left behind by the previous CEO of the same unit
    session.add(ActivityLog(business_unit_id=1, report_date=date(2026, 10, 9),
                            meetings_attended="Predecessor supplier review",
                            submitted_by=predecessor.id))
    # another unit's day — must never leak into BU1's calendar
    session.add(ActivityLog(business_unit_id=2, report_date=date(2026, 10, 14),
                            meetings_attended="Other unit meeting", submitted_by=ceo2.id))
    session.commit()
    session.test_users = {"ceo": ceo, "predecessor": predecessor, "ceo2": ceo2}
    yield session
    session.close()
    engine.dispose()


def _cell(weeks, iso):
    for week in weeks:
        for day in week:
            if day["date_iso"] == iso:
                return day
    raise AssertionError(f"no calendar cell for {iso}")


# ------------------------------------------------------- admin endpoint

def test_admin_calendar_chairman_gets_bu_month(db):
    out = ceo_activity_calendar(business_unit_id=1, month="2026-10", user=CHAIRMAN, db=db)
    assert out["business_unit"] == {"id": 1, "code": "BU1", "name": "Shop One"}
    assert out["ceo"]["username"] == "ceo_one"
    assert out["ceo"]["full_name"] == "CEO One"
    assert out["calendar_month_label"] == "October 2026"
    assert out["prev_month"] == "2026-09"
    assert out["next_month"] == "2026-11"

    cell = _cell(out["calendar_weeks"], "2026-10-14")
    assert cell["has_report"] is True
    assert cell["meetings_attended"] == "Board meeting with HO"
    assert cell["key_decisions"] == "Approved Q4 budget"
    assert cell["activity_submitted_by_username"] == "ceo_one"
    assert cell["revenue"] == 250000.0

    empty = _cell(out["calendar_weeks"], "2026-10-20")
    assert empty["has_report"] is False
    assert empty["meetings_attended"] is None

    all_meetings = {
        d["meetings_attended"] for w in out["calendar_weeks"] for d in w if d["meetings_attended"]
    }
    assert "Other unit meeting" not in all_meetings


def test_admin_calendar_ho_gets_same_month(db):
    out = ceo_activity_calendar(business_unit_id=1, month="2026-10", user=HO, db=db)
    assert out["calendar_month_label"] == "October 2026"
    assert _cell(out["calendar_weeks"], "2026-10-14")["has_report"] is True


def test_admin_calendar_shows_predecessor_days(db):
    # Admin view is unscoped: a day submitted by the unit's previous CEO
    # still shows, attributed to its submitter.
    out = ceo_activity_calendar(business_unit_id=1, month="2026-10", user=CHAIRMAN, db=db)
    cell = _cell(out["calendar_weeks"], "2026-10-09")
    assert cell["has_report"] is True
    assert cell["meetings_attended"] == "Predecessor supplier review"
    assert cell["activity_submitted_by_username"] == "ceo_old"


def test_admin_calendar_unknown_bu_404(db):
    with pytest.raises(HTTPException) as exc:
        ceo_activity_calendar(business_unit_id=999, month="2026-10", user=CHAIRMAN, db=db)
    assert exc.value.status_code == 404


def test_admin_calendar_malformed_month_falls_back_to_current(db):
    out = ceo_activity_calendar(business_unit_id=1, month="not-a-month", user=CHAIRMAN, db=db)
    today = today_local()
    assert out["calendar_month_label"] == today.strftime("%B %Y")
    assert out["current_month"] == f"{today.year:04d}-{today.month:02d}"


def test_admin_calendar_role_guard():
    for role in ("CEO", "Accountant"):
        with pytest.raises(InsufficientRoleException):
            require_admin(user=SimpleNamespace(id=1, role=role, business_unit_id=1))
    for role in ("Chairman", "HO"):
        assert require_admin(user=SimpleNamespace(id=1, role=role, business_unit_id=None)).role == role


# ------------------------------------------------------ calendar helper

def test_helper_none_user_returns_bu_rows_ceo_scope_unchanged(db):
    users = db.test_users
    weeks_all = build_ceo_activity_calendar(db, 1, 2026, 10, date(2026, 10, 22), None)
    assert _cell(weeks_all, "2026-10-09")["has_report"] is True
    assert _cell(weeks_all, "2026-10-14")["has_report"] is True

    # The CEO's own view stays personal: the predecessor's day is hidden.
    weeks_ceo = build_ceo_activity_calendar(db, 1, 2026, 10, date(2026, 10, 22), users["ceo"].id)
    assert _cell(weeks_ceo, "2026-10-14")["has_report"] is True
    predecessor_cell = _cell(weeks_ceo, "2026-10-09")
    assert predecessor_cell["has_report"] is False
    assert predecessor_cell["meetings_attended"] is None


# ------------------------------------------------- CEO dashboard form date

def test_ceo_dashboard_form_date_prefills_that_day(db):
    ceo = db.test_users["ceo"]
    out = ceo_dashboard(month="", for_date="2026-10-14", user=ceo, db=db)
    assert out["form_date"] == "2026-10-14"
    assert out["form_is_today"] is False
    assert out["form_already_submitted"] is True
    assert out["form_activity"]["meetings_attended"] == "Board meeting with HO"
    assert out["form_financial"]["revenue"] == 250000.0

    # No date given: the form keeps editing today, exactly as before.
    out_today = ceo_dashboard(month="", for_date="", user=ceo, db=db)
    assert out_today["form_is_today"] is True
    assert out_today["form_date"] == today_local().isoformat()
    assert out_today["form_activity"] == out_today["activity"]


# ------------------------------------------------------- CEO submit path

_SUBMIT_DEFAULTS = dict(
    report_date="", revenue="", cash_flow="", receivable="", payable="",
    other_amount="", financial_other_notes="", meetings_attended="",
    site_visits="", key_decisions="", next_day_schedule="", problems_today="",
    help_needed="", gym_percent="",
    swimming_percent="", cycle_percent="", activity_other_notes="",
    daily_steps="", heart_rate="", sleep_hours="", bp_systolic="",
    bp_diastolic="", health_other_notes="", event_flag="false",
    meeting_flag="false", golf_flag="false", social_other_notes="",
    attachment=None,
)


def _submit(db, user, **overrides):
    kwargs = {**_SUBMIT_DEFAULTS, **overrides}
    return submit_report(request=REQUEST, user=user, db=db, **kwargs)


def test_ceo_submit_report_for_past_date(db):
    ceo = db.test_users["ceo"]
    result = _submit(
        db, ceo,
        report_date="2026-10-05",
        revenue="1000", cash_flow="800", receivable="100", payable="50",
        meetings_attended="Backfilled supplier meeting",
        next_day_schedule="Warehouse visit",
    )
    assert result == {"ok": True}
    row = (
        db.query(ActivityLog)
        .filter(ActivityLog.business_unit_id == 1, ActivityLog.report_date == date(2026, 10, 5))
        .first()
    )
    assert row is not None
    assert row.meetings_attended == "Backfilled supplier meeting"
    assert row.next_day_schedule == "Warehouse visit"
    assert row.submitted_by == ceo.id
    fin = (
        db.query(DailyFinancial)
        .filter(DailyFinancial.business_unit_id == 1, DailyFinancial.report_date == date(2026, 10, 5))
        .first()
    )
    assert fin is not None and float(fin.revenue) == 1000.0


def test_ceo_submit_report_cannot_write_another_bu(db):
    ceo = db.test_users["ceo"]
    _submit(
        db, ceo,
        report_date="2026-10-14",
        revenue="1000", cash_flow="800", receivable="100", payable="50",
        meetings_attended="CEO One updated meeting",
    )
    # BU2's own row for the same date is untouched; the write landed on BU1.
    bu2_row = (
        db.query(ActivityLog)
        .filter(ActivityLog.business_unit_id == 2, ActivityLog.report_date == date(2026, 10, 14))
        .first()
    )
    assert bu2_row.meetings_attended == "Other unit meeting"
    assert bu2_row.submitted_by == db.test_users["ceo2"].id
    bu1_row = (
        db.query(ActivityLog)
        .filter(ActivityLog.business_unit_id == 1, ActivityLog.report_date == date(2026, 10, 14))
        .first()
    )
    assert bu1_row.meetings_attended == "CEO One updated meeting"


def test_ceo_submit_report_rejects_bad_date(db):
    ceo = db.test_users["ceo"]
    with pytest.raises(HTTPException) as exc:
        _submit(
            db, ceo,
            report_date="14/10/2026",
            revenue="1000", cash_flow="800", receivable="100", payable="50",
        )
    assert exc.value.status_code == 400
