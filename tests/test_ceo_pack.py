"""Tests for the 2026-10-22 CEO feature pack.

Part 1 — help-request resolution loop: POST /api/admin/activity-logs/
{id}/resolve-help permissions + 400/404/422 paths, the CEO notification,
needs_help flipping false after resolution, and the submit path's
re-open rule (editing a resolved day's help text clears the resolution;
an unchanged resubmit keeps it).
Part 2 — key_achievement + meetings_count round-trip (submit, ?date=
prefill, calendar payload).
Part 3 — health completion: stress/energy chips, weight, sick toggle
and exercise % stored on the health row (422 on bad values), the legacy
activity-percent path for clients that don't send health_*_percent,
and the calendar's health-first exercise fallback.
Part 4 — GET /api/admin/ceo-monthly-summary PDF endpoint.

Run with: ENVIRONMENT=test DATABASE_URL=sqlite:// \
    .venv/bin/python -m pytest tests/test_ceo_pack.py
"""
import asyncio
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.auth import InsufficientRoleException
from src.models import ActivityLog, Base, BusinessUnit, DailyFinancial, HealthStatus, Notification, User
from src.routers.admin import activity_dashboard, ceo_monthly_summary, require_admin, resolve_help_request
from src.routers.ceo import ceo_dashboard, submit_report
from src.schemas import ResolveHelpRequest
from src.utils import build_ceo_activity_calendar

REQUEST = SimpleNamespace(headers={}, client=SimpleNamespace(host="127.0.0.1"))
HELP = "Need Chairman approval to rent a backup truck this week"
RESPONSE = "Approved — rent the truck, invoice to HO by Friday."


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([
        BusinessUnit(id=1, code="BU1", name="Shop One"),
        BusinessUnit(id=2, code="BU2", name="Shop Two"),
    ])
    # Explicit high ids: submit_report's rate limiter is process-global
    # and keyed by user id — small ids would share a bucket with other
    # test files' users and trip the limit when the suite runs together.
    chairman = User(id=790, username="chairman_t", hashed_password="x", role="Chairman",
                    full_name="Chair Man", business_unit_id=None, is_active=True)
    ho = User(id=791, username="ho_t", hashed_password="x", role="HO",
              full_name="Aitch O", business_unit_id=None, is_active=True)
    ceo = User(id=701, username="ceo_one", hashed_password="x", role="CEO", full_name="CEO One",
               business_unit_id=1, is_active=True)
    ceo2 = User(id=702, username="ceo_two", hashed_password="x", role="CEO", full_name="CEO Two",
                business_unit_id=2, is_active=True)
    session.add_all([chairman, ho, ceo, ceo2])
    session.commit()
    # An open help request (BU1), a report with no ask, and an already
    # resolved request — the three states the resolution loop switches on.
    session.add(ActivityLog(business_unit_id=1, report_date=date(2026, 10, 6),
                            meetings_attended="Route planning",
                            problems_today="Truck broke down", help_needed=HELP,
                            submitted_by=ceo.id))
    session.add(ActivityLog(business_unit_id=1, report_date=date(2026, 10, 7),
                            meetings_attended="Quiet day", submitted_by=ceo.id))
    session.add(ActivityLog(business_unit_id=1, report_date=date(2026, 10, 8),
                            meetings_attended="Supplier review", help_needed="Old ask",
                            help_resolved_at=datetime(2026, 10, 9, 10, 30),
                            help_resolved_by=chairman.id, help_response="Sorted by phone.",
                            submitted_by=ceo.id))
    session.commit()
    session.test_users = {"chairman": chairman, "ho": ho, "ceo": ceo, "ceo2": ceo2}
    yield session
    session.close()
    engine.dispose()


_SUBMIT_DEFAULTS = dict(
    report_date="", revenue="", cash_flow="", receivable="", payable="",
    other_amount="", financial_other_notes="", meetings_attended="",
    meetings_count="", site_visits="", key_decisions="", key_achievement="",
    next_day_schedule="", problems_today="", help_needed="", gym_percent="",
    swimming_percent="", cycle_percent="", activity_other_notes="",
    daily_steps="", heart_rate="", sleep_hours="", bp_systolic="",
    bp_diastolic="", stress_level="", energy_mood="", weight_kg="",
    is_sick="false", health_gym_percent=None, health_swimming_percent=None,
    health_cycle_percent=None, health_other_notes="", event_flag="false",
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


def _health(db, bu_id, on):
    return (
        db.query(HealthStatus)
        .filter(HealthStatus.business_unit_id == bu_id, HealthStatus.report_date == on)
        .first()
    )


def _cell(weeks, iso):
    for week in weeks:
        for day in week:
            if day["date_iso"] == iso:
                return day
    raise AssertionError(f"no calendar cell for {iso}")


# ------------------------------------------------------ Part 1: resolve

def test_resolve_help_role_guard():
    for role in ("CEO", "Accountant"):
        with pytest.raises(InsufficientRoleException):
            require_admin(user=SimpleNamespace(id=1, role=role, business_unit_id=1))
    for role in ("Chairman", "HO"):
        assert require_admin(user=SimpleNamespace(id=1, role=role, business_unit_id=None)).role == role


def test_resolve_help_happy_path_notifies_ceo(db):
    ho = db.test_users["ho"]
    log = _activity(db, 1, date(2026, 10, 6))

    out = resolve_help_request(log_id=log.id, payload=ResolveHelpRequest(response=RESPONSE),
                               request=REQUEST, user=ho, db=db)
    assert out["ok"] is True
    assert out["help_resolved_by_username"] == "ho_t"
    assert out["help_response"] == RESPONSE

    db.refresh(log)
    assert log.help_resolved_at is not None
    assert log.help_resolved_by == ho.id
    assert log.help_response == RESPONSE

    notes = db.query(Notification).filter(Notification.user_id == db.test_users["ceo"].id).all()
    assert len(notes) == 1
    note = notes[0]
    assert note.type == "help_resolved"
    assert note.message == RESPONSE
    assert note.link == "/ceo?date=2026-10-06"
    assert note.is_read is False


def test_resolve_help_flips_needs_help_everywhere(db):
    chairman = db.test_users["chairman"]
    ceo = db.test_users["ceo"]
    # The directory reflects each unit's LATEST report, so the open
    # request under test must be BU1's latest day.
    db.add(ActivityLog(business_unit_id=1, report_date=date(2026, 10, 20),
                       meetings_attended="Depot visit", help_needed=HELP,
                       submitted_by=ceo.id))
    db.commit()

    before = activity_dashboard(user=chairman, db=db)
    directory = {d["bu_id"]: d for d in before["directory"]}
    assert directory[1]["needs_help"] is True
    assert directory[1]["help_needed"] == HELP

    log = _activity(db, 1, date(2026, 10, 20))
    resolve_help_request(log_id=log.id, payload=ResolveHelpRequest(response=RESPONSE),
                         request=REQUEST, user=chairman, db=db)

    after = activity_dashboard(user=chairman, db=db)
    directory = {d["bu_id"]: d for d in after["directory"]}
    assert directory[1]["needs_help"] is False
    assert directory[1]["help_resolved_at"] is not None
    assert directory[1]["help_resolved_by_username"] == "chairman_t"
    assert directory[1]["help_response"] == RESPONSE

    # The CEO sees the answer on their own dashboard payload too.
    out = ceo_dashboard(month="", for_date="2026-10-20", user=ceo, db=db)
    assert out["form_activity"]["needs_help"] is False
    assert out["form_activity"]["help_resolved_at"] is not None
    assert out["form_activity"]["help_resolved_by_username"] == "chairman_t"
    assert out["form_activity"]["help_response"] == RESPONSE

    # And on the calendar day payload (both scopes).
    weeks = build_ceo_activity_calendar(db, 1, 2026, 10, date(2026, 10, 22), None)
    cell = _cell(weeks, "2026-10-20")
    assert cell["needs_help"] is False
    assert cell["help_resolved_by_username"] == "chairman_t"
    assert cell["help_response"] == RESPONSE


def test_resolve_help_error_paths(db):
    chairman = db.test_users["chairman"]
    open_log = _activity(db, 1, date(2026, 10, 6))
    no_help_log = _activity(db, 1, date(2026, 10, 7))
    resolved_log = _activity(db, 1, date(2026, 10, 8))

    with pytest.raises(HTTPException) as exc:
        resolve_help_request(log_id=99999, payload=ResolveHelpRequest(response="x"),
                             request=REQUEST, user=chairman, db=db)
    assert exc.value.status_code == 404

    with pytest.raises(HTTPException) as exc:
        resolve_help_request(log_id=no_help_log.id, payload=ResolveHelpRequest(response="x"),
                             request=REQUEST, user=chairman, db=db)
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        resolve_help_request(log_id=resolved_log.id, payload=ResolveHelpRequest(response="x"),
                             request=REQUEST, user=chairman, db=db)
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        resolve_help_request(log_id=open_log.id, payload=ResolveHelpRequest(response="   "),
                             request=REQUEST, user=chairman, db=db)
    assert exc.value.status_code == 422


def test_resolved_log_payload_shows_resolution(db):
    # The fixture's pre-resolved day reads as resolved (not an open
    # escalation) in the admin payload and the calendar.
    out = activity_dashboard(user=db.test_users["chairman"], db=db)
    history = {e["report_date"]: e for e in out["activity_history"]}
    entry = history["08 Oct 2026"]
    assert entry["needs_help"] is False
    assert entry["help_resolved_by_username"] == "chairman_t"
    assert entry["help_response"] == "Sorted by phone."

    weeks = build_ceo_activity_calendar(db, 1, 2026, 10, date(2026, 10, 22), None)
    cell = _cell(weeks, "2026-10-08")
    assert cell["needs_help"] is False
    assert cell["help_resolved_at"] is not None

    open_cell = _cell(weeks, "2026-10-06")
    assert open_cell["needs_help"] is True
    assert open_cell["help_resolved_at"] is None


def test_editing_resolved_help_reopens_only_when_text_changes(db):
    ceo = db.test_users["ceo"]
    chairman = db.test_users["chairman"]
    log = _activity(db, 1, date(2026, 10, 6))
    resolve_help_request(log_id=log.id, payload=ResolveHelpRequest(response=RESPONSE),
                         request=REQUEST, user=chairman, db=db)

    # Resubmitting the day with the SAME ask (e.g. fixing other fields)
    # keeps the resolution.
    _submit(db, ceo, report_date="2026-10-06", help_needed=HELP, meetings_attended="Route planning v2")
    db.refresh(log)
    assert log.help_resolved_at is not None
    assert log.help_response == RESPONSE

    # Changing the ask re-opens the request: the old answer no longer
    # matches what is being asked.
    _submit(db, ceo, report_date="2026-10-06", help_needed="Actually we need TWO trucks now")
    db.refresh(log)
    assert log.help_needed == "Actually we need TWO trucks now"
    assert log.help_resolved_at is None
    assert log.help_resolved_by is None
    assert log.help_response is None


# ------------------------------------------- Part 2: achievement + count

def test_achievement_and_meetings_count_round_trip(db):
    ceo = db.test_users["ceo"]
    result = _submit(db, ceo, report_date="2026-10-05",
                     meetings_attended="Supplier marathon", meetings_count="3",
                     key_achievement="Signed the Yangon distribution deal")
    assert result == {"ok": True}
    row = _activity(db, 1, date(2026, 10, 5))
    assert row.key_achievement == "Signed the Yangon distribution deal"
    assert row.meetings_count == 3

    out = ceo_dashboard(month="", for_date="2026-10-05", user=ceo, db=db)
    assert out["form_activity"]["key_achievement"] == "Signed the Yangon distribution deal"
    assert out["form_activity"]["meetings_count"] == 3

    weeks = build_ceo_activity_calendar(db, 1, 2026, 10, date(2026, 10, 22), ceo.id)
    cell = _cell(weeks, "2026-10-05")
    assert cell["key_achievement"] == "Signed the Yangon distribution deal"
    assert cell["meetings_count"] == 3

    dash = activity_dashboard(user=db.test_users["chairman"], db=db)
    history = {e["report_date"]: e for e in dash["activity_history"]}
    assert history["05 Oct 2026"]["key_achievement"] == "Signed the Yangon distribution deal"
    assert history["05 Oct 2026"]["meetings_count"] == 3


def test_meetings_count_out_of_range_rejected(db):
    # users.business_unit_id is unique (one CEO per unit) — validation
    # users each get their own throwaway unit.
    db.add(BusinessUnit(id=30, code="BU30", name="Validation Thirty"))
    ceo = User(id=716, username="ceo_mc", hashed_password="x", role="CEO", full_name="MC",
               business_unit_id=30, is_active=True)
    db.add(ceo)
    db.commit()
    with pytest.raises(HTTPException) as exc:
        _submit(db, ceo, report_date="2026-11-02", meetings_count="100")
    assert exc.value.status_code == 400


# ------------------------------------------------------ Part 3: health

def test_health_completion_stored_on_health_row(db):
    ceo = db.test_users["ceo"]
    result = _submit(
        db, ceo, report_date="2026-10-12",
        stress_level="High", energy_mood="Normal", weight_kg="70.5", is_sick="true",
        health_gym_percent="60", health_swimming_percent="", health_cycle_percent="20",
    )
    assert result == {"ok": True}

    health = _health(db, 1, date(2026, 10, 12))
    assert health.stress_level == "High"
    assert health.energy_mood == "Normal"
    assert health.weight_kg == Decimal("70.5")
    assert health.is_sick is True
    assert health.gym_percent == 60
    assert health.swimming_percent is None
    assert health.cycle_percent == 20

    # The exercise move leaves the activity row's legacy percents alone.
    activity = _activity(db, 1, date(2026, 10, 12))
    assert activity.gym_percent is None

    out = ceo_dashboard(month="", for_date="2026-10-12", user=ceo, db=db)
    form_health = out["form_health"]
    assert form_health["stress_level"] == "High"
    assert form_health["energy_mood"] == "Normal"
    assert form_health["weight_kg"] == 70.5
    assert form_health["is_sick"] is True
    assert form_health["gym_percent"] == 60

    weeks = build_ceo_activity_calendar(db, 1, 2026, 10, date(2026, 10, 22), ceo.id)
    cell = _cell(weeks, "2026-10-12")
    assert cell["stress_level"] == "High"
    assert cell["energy_mood"] == "Normal"
    assert cell["weight_kg"] == 70.5
    assert cell["is_sick"] is True
    assert cell["exercise_gym_percent"] == 60
    assert cell["exercise_cycle_percent"] == 20


def test_health_legacy_activity_percents_still_work_and_fall_back(db):
    ceo2 = db.test_users["ceo2"]
    # A pre-move client: no health_*_percent fields at all, exercise %
    # sent as the old activity fields.
    result = _submit(db, ceo2, report_date="2026-10-12", gym_percent="40", swimming_percent="10")
    assert result == {"ok": True}

    activity = _activity(db, 2, date(2026, 10, 12))
    assert activity.gym_percent == 40
    assert activity.swimming_percent == 10
    health = _health(db, 2, date(2026, 10, 12))
    assert health.gym_percent is None

    # The calendar's effective exercise values fall back to the activity
    # row's legacy percents.
    weeks = build_ceo_activity_calendar(db, 2, 2026, 10, date(2026, 10, 22), ceo2.id)
    cell = _cell(weeks, "2026-10-12")
    assert cell["exercise_gym_percent"] == 40
    assert cell["exercise_swimming_percent"] == 10
    assert cell["exercise_cycle_percent"] is None


def test_health_validation_422s(db):
    cases = [
        (711, dict(stress_level="Extreme")),
        (712, dict(energy_mood="Sleepy")),
        (713, dict(weight_kg="500")),
        (714, dict(weight_kg="not-a-number")),
        (715, dict(health_gym_percent="150")),
        (717, dict(health_cycle_percent="abc")),
    ]
    for user_id, overrides in cases:
        bu_id = 40 + (user_id % 100)
        db.add(BusinessUnit(id=bu_id, code=f"BV{user_id}", name=f"Validation {user_id}"))
        user = User(id=user_id, username=f"ceo_v{user_id}", hashed_password="x", role="CEO",
                    full_name="Validation", business_unit_id=bu_id, is_active=True)
        db.add(user)
        db.commit()
        with pytest.raises(HTTPException) as exc:
            _submit(db, user, report_date="2026-11-03", **overrides)
        assert exc.value.status_code == 422, f"{overrides} should 422, got {exc.value.status_code}"


# --------------------------------------------------- Part 4: summary PDF

async def _collect_pdf(resp) -> bytes:
    chunks = []
    async for chunk in resp.body_iterator:
        chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode())
    return b"".join(chunks)


def test_monthly_summary_pdf(db):
    chairman = db.test_users["chairman"]
    ceo = db.test_users["ceo"]
    # A fuller month for BU1: achievement + meetings + revenue + health.
    db.add(ActivityLog(business_unit_id=1, report_date=date(2026, 10, 5),
                       meetings_attended="Supplier marathon", meetings_count=3,
                       key_achievement="Signed the Yangon distribution deal",
                       problems_today="Rain delayed deliveries", submitted_by=ceo.id))
    db.add(DailyFinancial(business_unit_id=1, report_date=date(2026, 10, 5),
                          revenue=Decimal("500000"), expenses=Decimal("0"),
                          net_profit=Decimal("0"), cash_in_hand=Decimal("0"),
                          cash_flow=Decimal("400000"), receivable=Decimal("0"),
                          payable=Decimal("0"), submitted_by=ceo.id))
    db.add(HealthStatus(business_unit_id=1, report_date=date(2026, 10, 5),
                        daily_steps=8000, sleep_hours=Decimal("7.5"),
                        weight_kg=Decimal("70.0"), gym_percent=50, submitted_by=ceo.id))
    db.commit()

    resp = ceo_monthly_summary(business_unit_id=1, month="2026-10", user=chairman, db=db)
    assert resp.media_type == "application/pdf"
    assert "ceo_monthly_summary_BU1_2026-10.pdf" in resp.headers["content-disposition"]
    body = asyncio.run(_collect_pdf(resp))
    assert body.startswith(b"%PDF")
    assert len(body) > 1000


def test_monthly_summary_empty_month_still_pdf(db):
    resp = ceo_monthly_summary(business_unit_id=2, month="2026-09",
                               user=db.test_users["chairman"], db=db)
    assert resp.media_type == "application/pdf"
    body = asyncio.run(_collect_pdf(resp))
    assert body.startswith(b"%PDF")


def test_monthly_summary_unknown_bu_and_malformed_month(db):
    chairman = db.test_users["chairman"]
    with pytest.raises(HTTPException) as exc:
        ceo_monthly_summary(business_unit_id=999, month="2026-10", user=chairman, db=db)
    assert exc.value.status_code == 404

    today = date.today()
    resp = ceo_monthly_summary(business_unit_id=1, month="garbage", user=chairman, db=db)
    assert resp.media_type == "application/pdf"
    assert f"{today.year:04d}-{today.month:02d}" in resp.headers["content-disposition"]
