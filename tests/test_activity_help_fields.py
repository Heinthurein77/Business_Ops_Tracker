"""Tests for the ActivityLog problems_today / help_needed fields (2026-10-22).

Covers: the CEO submit-report round-trip (create + resubmit edit), the
CEO dashboard payload and ?date= form prefill, the calendar day payload,
and the admin activity-dashboard payload (problems/help fields plus the
needs_help escalation flag — true only for non-blank help text).

Run with: ENVIRONMENT=test DATABASE_URL=sqlite:// \
    .venv/bin/python -m pytest tests/test_activity_help_fields.py
"""
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models import ActivityLog, Base, BusinessUnit, User
from src.routers.admin import activity_dashboard
from src.routers.ceo import ceo_dashboard, submit_report
from src.utils import build_ceo_activity_calendar, today_local

CHAIRMAN = SimpleNamespace(id=90, role="Chairman", business_unit_id=None)
REQUEST = SimpleNamespace(headers={}, client=SimpleNamespace(host="127.0.0.1"))

PROBLEMS = "Delivery truck broke down; two routes delayed"
HELP = "Need Chairman approval to rent a backup truck this week"


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([
        BusinessUnit(id=1, code="BU1", name="Shop One"),
        BusinessUnit(id=2, code="BU2", name="Shop Two"),
    ])
    # Explicit high ids: submit_report's rate limiter is process-global and
    # keyed by user id — small ids would share a bucket with other test
    # files' users and trip the limit when the whole suite runs together.
    ceo = User(id=501, username="ceo_one", hashed_password="x", role="CEO", full_name="CEO One",
               business_unit_id=1, is_active=True)
    ceo2 = User(id=502, username="ceo_two", hashed_password="x", role="CEO", full_name="CEO Two",
                business_unit_id=2, is_active=True)
    session.add_all([ceo, ceo2])
    session.commit()
    # BU1's latest report (today) asks for help; BU2's latest report has a
    # problems note but a whitespace-only help field — not an escalation.
    session.add(ActivityLog(business_unit_id=1, report_date=today_local(),
                            meetings_attended="Ops review",
                            problems_today=PROBLEMS, help_needed=HELP,
                            submitted_by=ceo.id))
    session.add(ActivityLog(business_unit_id=2, report_date=date(2026, 10, 10),
                            meetings_attended="Supplier call",
                            problems_today="Rain delayed the market run",
                            help_needed="   ",
                            submitted_by=ceo2.id))
    session.commit()
    session.test_users = {"ceo": ceo, "ceo2": ceo2}
    yield session
    session.close()
    engine.dispose()


_SUBMIT_DEFAULTS = dict(
    report_date="", revenue="", cash_flow="", receivable="", payable="",
    other_amount="", financial_other_notes="", meetings_attended="",
    site_visits="", key_decisions="", next_day_schedule="", problems_today="",
    help_needed="", gym_percent="", swimming_percent="", cycle_percent="",
    activity_other_notes="", daily_steps="", heart_rate="", sleep_hours="",
    bp_systolic="", bp_diastolic="", health_other_notes="", event_flag="false",
    meeting_flag="false", golf_flag="false", social_other_notes="",
    attachment=None,
)

_MONEY = dict(revenue="5000", cash_flow="4200", receivable="300", payable="150")


def _submit(db, user, **overrides):
    kwargs = {**_SUBMIT_DEFAULTS, **_MONEY, **overrides}
    return submit_report(request=REQUEST, user=user, db=db, **kwargs)


def _activity(db, bu_id, on):
    return (
        db.query(ActivityLog)
        .filter(ActivityLog.business_unit_id == bu_id, ActivityLog.report_date == on)
        .first()
    )


# ------------------------------------------------------------ submit path

def test_submit_stores_problems_and_help(db):
    ceo = db.test_users["ceo"]
    result = _submit(db, ceo, report_date="2026-10-06",
                     meetings_attended="Route planning",
                     problems_today=PROBLEMS, help_needed=HELP)
    assert result == {"ok": True}
    row = _activity(db, 1, date(2026, 10, 6))
    assert row is not None
    assert row.problems_today == PROBLEMS
    assert row.help_needed == HELP


def test_submit_resubmit_edits_and_clears_fields(db):
    ceo = db.test_users["ceo"]
    _submit(db, ceo, report_date="2026-10-07",
            problems_today="Old problem", help_needed="Old ask")
    _submit(db, ceo, report_date="2026-10-07",
            problems_today="Updated problem", help_needed="")
    row = _activity(db, 1, date(2026, 10, 7))
    assert row.problems_today == "Updated problem"
    assert row.help_needed is None  # blank clears back to NULL, not ""


def test_submit_blank_help_stays_null(db):
    ceo = db.test_users["ceo"]
    _submit(db, ceo, report_date="2026-10-08", problems_today="Minor delay")
    row = _activity(db, 1, date(2026, 10, 8))
    assert row.problems_today == "Minor delay"
    assert row.help_needed is None


# --------------------------------------------------------- dashboard payload

def test_ceo_dashboard_payload_includes_fields(db):
    ceo = db.test_users["ceo"]
    out = ceo_dashboard(month="", for_date="", user=ceo, db=db)
    # Seeded BU1 row is today's, so both the "today" activity payload and
    # the form prefill carry the new fields.
    assert out["activity"]["problems_today"] == PROBLEMS
    assert out["activity"]["help_needed"] == HELP
    assert out["form_activity"]["problems_today"] == PROBLEMS
    assert out["form_activity"]["help_needed"] == HELP


def test_ceo_dashboard_date_prefill_includes_fields(db):
    ceo = db.test_users["ceo"]
    _submit(db, ceo, report_date="2026-10-06",
            problems_today=PROBLEMS, help_needed=HELP)
    out = ceo_dashboard(month="", for_date="2026-10-06", user=ceo, db=db)
    assert out["form_date"] == "2026-10-06"
    assert out["form_activity"]["problems_today"] == PROBLEMS
    assert out["form_activity"]["help_needed"] == HELP


# ---------------------------------------------------------- calendar payload

def _cell(weeks, iso):
    for week in weeks:
        for day in week:
            if day["date_iso"] == iso:
                return day
    raise AssertionError(f"no calendar cell for {iso}")


def test_calendar_day_payload_includes_fields(db):
    ceo = db.test_users["ceo"]
    today = today_local()
    weeks_ceo = build_ceo_activity_calendar(db, 1, today.year, today.month, today, ceo.id)
    cell = _cell(weeks_ceo, today.isoformat())
    assert cell["problems_today"] == PROBLEMS
    assert cell["help_needed"] == HELP

    weeks_admin = build_ceo_activity_calendar(db, 1, today.year, today.month, today, None)
    cell_admin = _cell(weeks_admin, today.isoformat())
    assert cell_admin["problems_today"] == PROBLEMS
    assert cell_admin["help_needed"] == HELP

    empty = _cell(weeks_admin, date(today.year, today.month, 1).isoformat())
    if empty["date_iso"] != today.isoformat():
        assert empty["problems_today"] is None
        assert empty["help_needed"] is None


# ------------------------------------------------------ admin activity page

def test_admin_activity_dashboard_needs_help_flags(db):
    out = activity_dashboard(user=CHAIRMAN, db=db)
    directory = {d["bu_id"]: d for d in out["directory"]}

    bu1 = directory[1]
    assert bu1["problems_today"] == PROBLEMS
    assert bu1["help_needed"] == HELP
    assert bu1["needs_help"] is True

    bu2 = directory[2]
    assert bu2["problems_today"] == "Rain delayed the market run"
    assert bu2["needs_help"] is False  # whitespace-only help is no escalation

    # Today's briefing entries (BU1 reported today) carry the same flag.
    briefing = {e["bu_code"]: e for e in out["today_briefing"]}
    assert briefing["BU1"]["needs_help"] is True
    assert briefing["BU1"]["help_needed"] == HELP

    # History entries too.
    history = {e["bu_code"]: e for e in out["activity_history"]}
    assert history["BU1"]["needs_help"] is True
    assert history["BU2"]["needs_help"] is False
