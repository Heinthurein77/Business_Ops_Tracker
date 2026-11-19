"""PM-review 2026-11-18 workstream B (product & user flows) regressions.

One test per behaviour fix; each names the finding it proves.
"""
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from starlette.requests import Request

from src.auth import hash_password
from src.models import (
    ActionItem, AuditLog, BusinessUnit, DailyFinancial,
    LicenseDocument, Notification, User,
)
from src.report_forms import build_ceo_report_form, build_ho_report_form
from src.routers.ceo import submit_report as ceo_submit_report
from src.routers.ho import submit_report as ho_submit_report
from src.utils import today_local, utcnow

PASSWORD = "TestPass123!"


@pytest.fixture(autouse=True)
def _seed_db(db):
    db.add_all([
        BusinessUnit(id=1, code="BU1", name="Shop One"),
        BusinessUnit(id=2, code="BU2", name="Shop Two"),
        User(id=1, username="chairman", hashed_password=hash_password(PASSWORD),
             role="Chairman", is_active=True),
        User(id=2, username="ho_user", hashed_password=hash_password(PASSWORD),
             role="HO", is_active=True),
        User(id=10, username="ceo_one", hashed_password=hash_password(PASSWORD),
             role="CEO", business_unit_id=1, is_active=True),
        User(id=11, username="ceo_two", hashed_password=hash_password(PASSWORD),
             role="CEO", business_unit_id=2, is_active=True),
    ])
    db.commit()
    db.test_users = {u.username: u for u in db.query(User).all()}



def _login_as_ho(client, http_db):
    """Log in as an HO user: HO needs no TOTP, unlike the conftest
    Chairman (password-only login answers 403 TOTP_ENROLLMENT_REQUIRED)."""
    http_db.add(User(username="ho_user", hashed_password=hash_password(PASSWORD),
                     role="HO", is_active=True))
    http_db.commit()
    client.login("ho_user", PASSWORD)


def _req():
    return Request({"type": "http", "headers": [], "client": ("8.8.8.8", 5555)})


def _ceo_submit(db, user, **overrides):
    import src.routers.ceo as ceo_router
    ceo_router._rate_limiter.reset()
    payload = dict(
        report_date="", revenue="100000", cash_flow="0", receivable="0", payable="0",
        other_amount="", financial_other_notes="", meetings_attended="Standup",
        meetings_count="", site_visits="", key_decisions="", key_achievement="",
        next_day_schedule="", problems_today="", help_needed="", gym_percent="",
        swimming_percent="", cycle_percent="", activity_other_notes="",
        daily_steps="", heart_rate="", sleep_hours="", bp_systolic="", bp_diastolic="",
        stress_level="", energy_mood="", weight_kg="", is_sick="false",
        health_gym_percent=None, health_swimming_percent=None, health_cycle_percent=None,
        health_other_notes="", event_flag="false", meeting_flag="false",
        golf_flag="false", social_other_notes="", attachment=None,
    )
    payload.update(overrides)
    form = build_ceo_report_form(request=_req(), user=user, db=db,
                                 rate_limiter=ceo_router._rate_limiter, **payload)
    return ceo_submit_report(request=_req(), form=form, user=user, db=db)


def _ho_submit(db, user, **overrides):
    import src.routers.ho as ho_router
    ho_router._rate_limiter.reset()
    payload = dict(
        revenue="100000", cash_flow="20000", receivable="5000", payable="3000",
        other_amount="1000", financial_other_notes="", meetings_attended="Standup",
        site_visits="", key_decisions="", next_day_schedule="", gym_percent="50",
        swimming_percent="", cycle_percent="", activity_other_notes="",
        daily_steps="8000", heart_rate="72", sleep_hours="7.5", bp_systolic="120",
        bp_diastolic="80", health_other_notes="", event_flag="true",
        meeting_flag="false", golf_flag="false", social_other_notes="",
        attachment=None,
    )
    payload.update(overrides)
    form = build_ho_report_form(request=_req(), user=user, db=db,
                                rate_limiter=ho_router._rate_limiter, **payload)
    return ho_submit_report(request=_req(), form=form, user=user, db=db)


def _seed_ceo_history(db, revenue, days=14):
    today = today_local()
    for i in range(1, days + 1):
        db.add(DailyFinancial(business_unit_id=1, report_date=today - timedelta(days=i),
                              revenue=Decimal(str(revenue)), expenses=Decimal("0"),
                              net_profit=Decimal("0"), cash_in_hand=Decimal("0")))
    db.commit()


# ------------------------------------------------------------------ PM-2

def test_pm2_missing_report_hour_is_16_and_gate():
    from src.services.ops_alerts import MISSING_REPORT_HOUR, missing_report_due
    from src.utils import APP_TZ
    assert MISSING_REPORT_HOUR == 16
    assert missing_report_due(datetime(2026, 11, 18, 15, 59, tzinfo=APP_TZ)) is False
    assert missing_report_due(datetime(2026, 11, 18, 16, 0, tzinfo=APP_TZ)) is True


def test_pm2_missing_count_counts_only_unsubmitted_ceos(db):
    from src.services.ops_alerts import count_missing_report_recipients
    db.add(DailyFinancial(business_unit_id=1, report_date=today_local(),
                          revenue=Decimal("1"), expenses=Decimal("0"),
                          net_profit=Decimal("0"), cash_in_hand=Decimal("0")))
    db.commit()
    assert count_missing_report_recipients(db) == 1  # only BU2's CEO is missing


def test_pm2_scheduled_job_skips_missing_step_before_hour(monkeypatch):
    import src.jobs.scheduled_alerts as job
    calls = []
    monkeypatch.setattr(job, "run_remind_missing_reports", lambda db: calls.append(1) or 99)
    monkeypatch.setattr(job, "missing_report_due", lambda: False)
    monkeypatch.setattr(job, "run_check_document_expiry", lambda db, days=30: {"checked": 0, "notified": 0})
    monkeypatch.setattr(job, "run_remind_action_items", lambda db: {"checked": 0, "notified": 0})
    monkeypatch.setattr(job, "_record_heartbeat", lambda db, name, status: None)
    # SessionLocal is only used to build the db session; the patched
    # functions never touch it, so a real session is fine.
    assert job.main() == 0
    assert calls == []


# ------------------------------------------------------------------ PM-3

def test_pm3_document_expiry_link_is_role_aware(db):
    from src.services.ops_alerts import run_check_document_expiry
    db.add(LicenseDocument(business_unit_id=1, title="Trade licence",
                           document_type="license",
                           expiry_date=today_local() + timedelta(days=5)))
    db.commit()
    out = run_check_document_expiry(db, days=30)
    assert out["notified"] >= 2
    ceo_note = db.query(Notification).filter(Notification.user_id == 10).one()
    assert ceo_note.link == "/ceo"
    chair_note = db.query(Notification).filter(Notification.user_id == 1).one()
    assert chair_note.link == "/admin/daily-ops"


# ------------------------------------------------------------------ PM-4

def test_pm4_window_error_states_manual_ho_correction():
    from src.utils import report_date_window_error
    msg = report_date_window_error(today_local() - timedelta(days=8), today_local(), 7)
    assert "contact HO" in msg.lower() or "Contact HO" in msg
    assert "manual" in msg


# ------------------------------------------------------------------ PM-6

def test_pm6_anomaly_is_sticky_until_reviewed_then_clears(db):
    from src.routers.admin import review_revenue_anomaly
    ceo = db.test_users["ceo_one"]
    chairman = db.test_users["chairman"]
    _seed_ceo_history(db, 100000)
    assert _ceo_submit(db, ceo, revenue="200000") == {"ok": True}
    fin = db.query(DailyFinancial).filter(
        DailyFinancial.business_unit_id == 1,
        DailyFinancial.report_date == today_local()).one()
    assert fin.revenue_anomaly is True
    # Chairman is notified too (was HO-only).
    assert db.query(Notification).filter(
        Notification.user_id == chairman.id, Notification.title.like("Revenue anomaly%")).count() == 1
    # Resubmit inside the band: the unreviewed flag must NOT clear.
    assert _ceo_submit(db, ceo, revenue="110000") == {"ok": True}
    db.refresh(fin)
    assert fin.revenue_anomaly is True
    assert fin.revenue_anomaly_reviewed_at is None
    # Explicit review, then a normal resubmit clears it.
    out = review_revenue_anomaly(1, today_local().isoformat(), request=_req(),
                                 user=chairman, db=db)
    assert out["reviewed"] is True
    db.refresh(fin)
    assert fin.revenue_anomaly_reviewed_at is not None
    assert _ceo_submit(db, ceo, revenue="110000") == {"ok": True}
    db.refresh(fin)
    assert fin.revenue_anomaly is False


def test_pm6_review_endpoint_404s_without_anomaly(db):
    from fastapi import HTTPException
    from src.routers.admin import review_revenue_anomaly
    chairman = db.test_users["chairman"]
    with pytest.raises(HTTPException) as exc:
        review_revenue_anomaly(1, today_local().isoformat(), request=_req(),
                               user=chairman, db=db)
    assert exc.value.status_code == 404


def test_pm6_comparison_and_detail_surface_anomaly(db):
    from src.routers.admin import business_unit_detail, gather_admin_overview
    _seed_ceo_history(db, 100000)
    assert _ceo_submit(db, db.test_users["ceo_one"], revenue="200000") == {"ok": True}
    ov = gather_admin_overview(db, parts=("comparison",))
    row = next(r for r in ov["comparison_data"] if r["bu_id"] == 1)
    assert row["revenue_anomaly"] is True
    assert row["revenue_anomaly_reviewed"] is False
    detail = business_unit_detail(1, user=db.test_users["chairman"], db=db)
    assert len(detail["revenue_anomalies"]) == 1
    assert detail["revenue_anomalies"][0]["reviewed"] is False


# ------------------------------------------------------------------ PM-7 / PM-20 (HTTP)

def test_pm7_forgot_password_notifies_chairman(client, http_db):
    http_db.add(User(username="ceo_one", hashed_password=hash_password(PASSWORD),
                     role="CEO", business_unit_id=1, is_active=True))
    http_db.commit()
    r = client.post("/api/auth/forgot-password", json={"username": "ceo_one"})
    assert r.status_code == 200
    assert "Ask the Chairman" in r.json()["message"]
    notes = http_db.query(Notification).filter(
        Notification.type == "alert",
        Notification.title.like("Password reset requested%")).all()
    assert len(notes) == 1
    assert notes[0].link == "/admin/users"


def test_pm20_expired_temp_password_gets_distinct_login_message(client, http_db):
    http_db.add(User(username="newceo", hashed_password=hash_password(PASSWORD),
                     role="CEO", business_unit_id=1, is_active=True,
                     temp_password_expires_at=utcnow() - timedelta(hours=1)))
    http_db.commit()
    r = client.post("/api/auth/login", json={"username": "newceo", "password": PASSWORD})
    assert r.status_code == 401
    assert "temporary password has expired" in r.json()["detail"]
    assert "ask the Chairman" in r.json()["detail"]


def test_pm20_wrong_password_still_generic(client, http_db):
    r = client.post("/api/auth/login", json={"username": "chairman", "password": "wrong-password"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid username or password"


# ------------------------------------------------------------------ PM-1 / PM-m3 (HTTP)

def test_pm1_action_item_assign_round_trip(client, http_db):
    _login_as_ho(client, http_db)
    http_db.add_all([
        BusinessUnit(id=1, code="BU1", name="Shop One"),
        BusinessUnit(id=2, code="BU2", name="Shop Two"),
        User(id=10, username="ceo_one", hashed_password=hash_password(PASSWORD),
             role="CEO", business_unit_id=1, is_active=True),
        User(id=11, username="ceo_two", hashed_password=hash_password(PASSWORD),
             role="CEO", business_unit_id=2, is_active=True),
    ])
    http_db.commit()
    r = client.post("/api/ops/action-items", json={
        "business_unit_id": 1, "title": "Follow up audit", "assigned_to_id": 10})
    assert r.status_code == 200, r.text
    item = r.json()
    assert item["assigned_to_id"] == 10
    assert item["assigned_to_name"] == "ceo_one"
    assert http_db.query(Notification).filter(
        Notification.user_id == 10, Notification.type == "action_assigned").count() == 1
    # Reassign: the new assignee is notified.
    r = client.patch(f"/api/ops/action-items/{item['id']}", json={"assigned_to_id": 11})
    assert r.status_code == 200, r.text
    assert r.json()["assigned_to_id"] == 11
    assert http_db.query(Notification).filter(
        Notification.user_id == 11, Notification.type == "action_assigned").count() == 1
    # Delete: the (old) assignee is notified.
    r = client.delete(f"/api/ops/action-items/{item['id']}")
    assert r.status_code == 200
    assert http_db.query(Notification).filter(
        Notification.user_id == 11, Notification.title == "Action item deleted").count() == 1


def test_pm_m3_done_to_in_progress_rejected_done_to_pending_reopens_with_audit(db):
    from fastapi import HTTPException
    from src.routers.ops import create_action_item, update_action_item
    from src.schemas import ActionItemIn, ActionItemUpdate
    chairman = db.test_users["chairman"]
    out = create_action_item(ActionItemIn(business_unit_id=1, title="T"), user=chairman, db=db)
    item_id = out["id"]
    update_action_item(item_id, ActionItemUpdate(status="done"), user=chairman, db=db)
    with pytest.raises(HTTPException) as exc:
        update_action_item(item_id, ActionItemUpdate(status="in_progress"), user=chairman, db=db)
    assert exc.value.status_code == 400
    assert "Invalid status transition" in exc.value.detail
    update_action_item(item_id, ActionItemUpdate(status="pending"), user=chairman, db=db)
    assert db.query(AuditLog).filter(AuditLog.action == "REOPEN_ACTION_ITEM").count() == 1
    item = db.query(ActionItem).filter(ActionItem.id == item_id).one()
    assert item.status == "pending" and item.completed_at is None


# ------------------------------------------------------------------ PM-m1 / PM-m2 / PM-m4 / PM-m5 / PM-21 / PM-m22

def test_pm_m1_ho_anomaly_and_backdated_resubmit_notify(client, http_db):
    from src.models import HeadOfficeDailyFinancial as _HODF
    today = today_local()
    for i in range(1, 15):
        http_db.add(_HODF(report_date=today - timedelta(days=i),
                           revenue=Decimal("100000")))
    http_db.add(User(id=2, username="ho_user", hashed_password=hash_password(PASSWORD),
                     role="HO", is_active=True))
    http_db.commit()
    client.login("ho_user", PASSWORD)
    base = dict(
        revenue="200000", cash_flow="20000", receivable="5000", payable="3000",
        other_amount="1000", financial_other_notes="", meetings_attended="Standup",
        site_visits="", key_decisions="", next_day_schedule="", gym_percent="50",
        swimming_percent="", cycle_percent="", activity_other_notes="",
        daily_steps="8000", heart_rate="72", sleep_hours="7.5", bp_systolic="120",
        bp_diastolic="80", health_other_notes="", event_flag="true",
        meeting_flag="false", golf_flag="false", social_other_notes="",
    )
    r = client.post("/api/ho/submit-report", data=base)
    assert r.status_code == 200, r.text
    assert http_db.query(Notification).filter(
        Notification.title == "Revenue anomaly: Head Office").count() == 2  # Chairman + HO
    day = today - timedelta(days=2)
    # PM-m22: the backfill date rides as ?date= (the HO form parser has
    # no report_date field).
    r = client.post(f"/api/ho/submit-report?date={day.isoformat()}",
                    data={**base, "revenue": "100000"})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/ho/submit-report?date={day.isoformat()}",
                    data={**base, "revenue": "110000"})
    assert r.status_code == 200, r.text
    assert http_db.query(Notification).filter(
        Notification.title == "Backdated report edit: Head Office").count() == 4
    # Both backdated submits notify (2 recipients each): the seeded
    # history row makes even the first backfill a resubmit.


def test_pm_m2_health_dashboard_includes_ho(db):
    from src.models import HeadOfficeHealthStatus
    from src.routers.admin import health_dashboard
    db.add(HeadOfficeHealthStatus(report_date=today_local(), daily_steps=9000,
                                  heart_rate=70, sleep_hours=Decimal("7.5")))
    db.commit()
    out = health_dashboard(user=db.test_users["chairman"], db=db)
    assert out["ho_health"] is not None
    assert out["ho_health"]["bu_name"] == "Head Office"
    assert out["ho_health"]["submitted_today"] is True
    assert out["ho_health"]["daily_steps"] == 9000


def test_pm_m4_login_lockout_is_an_enum_value():
    from src.constants import NotificationType
    assert NotificationType.LOGIN_LOCKOUT == "login_lockout"
    repo = Path(__file__).resolve().parents[1]
    for rel in ("src/throttle.py", "src/jobs/log_scan.py", "src/jobs/scheduled_alerts.py"):
        text = (repo / rel).read_text()
        assert '"login_lockout"' not in text
        assert '"alert"' not in text


def test_pm_m5_report_builders_moved_out_of_ops_router(db):
    from src.reports.ops_reports import build_monthly_report_pdf, build_ops_excel
    from src.routers import ops as ops_router
    assert not hasattr(ops_router, "build_monthly_report_pdf")
    data, filename = build_monthly_report_pdf(db, None)
    assert data[:4] == b"%PDF"
    assert filename.endswith(".pdf")
    data, filename = build_ops_excel(db, None, tab="actions")
    assert data[:2] == b"PK"  # zip container of an .xlsx workbook
    assert filename == "ops_actions.xlsx"
    with pytest.raises(Exception):
        build_ops_excel(db, None, tab="nope")


def test_pm_m5_endpoints_still_work(client, http_db):
    _login_as_ho(client, http_db)
    r = client.get("/api/ops/monthly-report")
    assert r.status_code == 200, r.text
    assert r.content[:4] == b"%PDF"
    r = client.get("/api/ops/export-excel?tab=actions")
    assert r.status_code == 200, r.text
    assert r.content[:2] == b"PK"


def test_pm21_notifications_paginate(db):
    from src.routers.ops import list_notifications
    for i in range(3):
        db.add(Notification(user_id=10, type="alert", title=f"n{i}", message="m"))
    db.commit()
    page1 = list_notifications(user=db.test_users["ceo_one"], db=db, limit=2, offset=0)
    assert page1["total"] == 3 and len(page1["items"]) == 2
    page2 = list_notifications(user=db.test_users["ceo_one"], db=db, limit=2, offset=2)
    assert len(page2["items"]) == 1
    assert page1["items"][0]["title"] == "n2"  # newest first


def test_pm_m22_ho_dashboard_backfill_date(db):
    from fastapi import HTTPException
    from src.routers.ho import ho_dashboard
    ho = db.test_users["ho_user"]
    day = today_local() - timedelta(days=3)
    out = ho_dashboard(date=day.isoformat(), user=ho, db=db)
    assert out["form_date"] == day.isoformat()
    assert out["form_is_today"] is False
    with pytest.raises(HTTPException) as exc:
        ho_dashboard(date=(today_local() - timedelta(days=8)).isoformat(), user=ho, db=db)
    assert exc.value.status_code == 400


def test_pm2_missing_count_endpoint(client, http_db):
    _login_as_ho(client, http_db)
    http_db.add_all([
        BusinessUnit(id=1, code="BU1", name="Shop One"),
        User(id=10, username="ceo_one", hashed_password=hash_password(PASSWORD),
             role="CEO", business_unit_id=1, is_active=True),
    ])
    http_db.commit()
    r = client.get("/api/ops/notifications/missing-count")
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 1
    assert r.json()["missing_report_hour"] == 16


def test_pm16_forecast_returns_bu_name(db):
    from src.routers.ops import forecast
    out = forecast(user=db.test_users["chairman"], db=db)
    assert out["business_unit_name"] == "All Business Units (group total)"
    out = forecast(business_unit_id=1, user=db.test_users["chairman"], db=db)
    assert out["business_unit_name"] == "Shop One"
