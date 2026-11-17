"""Tests for workstream B of the 2026-11-17 BOT code-quality + security
review (branch fix/review-b-jobs): M-03, M-04, M-05, M-06, m-05, m-08,
m-11, m-31, m-34, m-26, M-13, m-18, m-19.

Run with: ENVIRONMENT=test DATABASE_URL=sqlite:// \
    .venv/bin/python -m pytest tests/test_bot_review_b_batch.py -q
"""
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models import AuditLog, Base, BusinessUnit, Notification, User
from src.routers.ceo import set_monthly_target as ceo_set_monthly_target
from src.routers.ceo import submit_report as ceo_submit_report
from src.routers.ho import set_monthly_target as ho_set_monthly_target
from src.routers.ho import submit_report as ho_submit_report
from src.schemas import MonthlyTargetRequest
from src.utils import today_local

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
    # High ids: submit_report's rate limiter is process-global and keyed
    # by user id — small ids would share a bucket with other test files.
    chairman = User(id=801, username="chairman_b", hashed_password="x", role="Chairman",
                    full_name="Chair Man", business_unit_id=None, is_active=True)
    ho = User(id=802, username="ho_b", hashed_password="x", role="HO",
              full_name="Aitch O", business_unit_id=None, is_active=True)
    ho2 = User(id=803, username="ho2_b", hashed_password="x", role="HO",
               full_name="Aitch Two", business_unit_id=None, is_active=True)
    ceo = User(id=804, username="ceo_b", hashed_password="x", role="CEO",
               full_name="CEO Bee", business_unit_id=1, is_active=True)
    ceo2 = User(id=805, username="ceo2_b", hashed_password="x", role="CEO",
                full_name="CEO Two Bee", business_unit_id=2, is_active=True)
    session.add_all([chairman, ho, ho2, ceo, ceo2])
    session.commit()
    session.test_engine = engine
    session.test_users = {"chairman": chairman, "ho": ho, "ho2": ho2, "ceo": ceo, "ceo2": ceo2}
    yield session
    session.close()
    engine.dispose()


def _fresh_session(db):
    return sessionmaker(bind=db.test_engine)()


_SUBMIT_DEFAULTS = dict(
    report_date="", revenue="5000", cash_flow="4200", receivable="300", payable="150",
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


def _ceo_submit(db, user, **overrides):
    import src.routers.ceo as ceo_router
    ceo_router._report_submit_timestamps.clear()
    kwargs = {**_SUBMIT_DEFAULTS, **overrides}
    return ceo_submit_report(request=REQUEST, user=user, db=db, **kwargs)


def _ho_submit(db, user, **overrides):
    import src.routers.ho as ho_router
    ho_router._report_submit_timestamps.clear()
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
    return ho_submit_report(REQUEST, user=user, db=db, **payload)


def _target(month_iso):
    return MonthlyTargetRequest(target_month=month_iso, monthly_revenue_target=1000000,
                                monthly_cash_flow_target=0, monthly_max_receivable=0,
                                monthly_max_payable=0)


# ------------------------------------------------------------------ M-03

class TestReportEditWindow:
    def test_report_date_older_than_window_rejected(self, db):
        ceo = db.test_users["ceo"]
        day = today_local() - timedelta(days=8)
        with pytest.raises(HTTPException) as exc:
            _ceo_submit(db, ceo, report_date=day.isoformat())
        assert exc.value.status_code == 400
        assert "more than 7 days old" in exc.value.detail

    def test_report_date_at_window_boundary_accepted(self, db):
        ceo = db.test_users["ceo"]
        day = today_local() - timedelta(days=7)
        assert _ceo_submit(db, ceo, report_date=day.isoformat()) == {"ok": True}

    def test_report_date_future_rejected(self, db):
        ceo = db.test_users["ceo"]
        day = today_local() + timedelta(days=1)
        with pytest.raises(HTTPException) as exc:
            _ceo_submit(db, ceo, report_date=day.isoformat())
        assert exc.value.status_code == 400
        assert "future" in exc.value.detail

    def test_backdated_resubmit_notifies_chairman_and_ho(self, db):
        ceo = db.test_users["ceo"]
        day = today_local() - timedelta(days=2)
        assert _ceo_submit(db, ceo, report_date=day.isoformat(), revenue="5000") == {"ok": True}
        # A first submission inside the window is not a restatement: no alert.
        assert db.query(Notification).filter(
            Notification.type == "alert",
            Notification.title.like("Backdated report edit%")).count() == 0
        assert _ceo_submit(db, ceo, report_date=day.isoformat(), revenue="9000") == {"ok": True}
        # The alert must be committed, not just pending in the caller's
        # session — a fresh session (as the Chairman's next request would
        # use) has to see it.
        fresh = _fresh_session(db)
        try:
            notes = fresh.query(Notification).filter(
                Notification.title.like("Backdated report edit%")).all()
            assert {n.user_id for n in notes} == {
                db.test_users["chairman"].id, db.test_users["ho"].id,
                db.test_users["ho2"].id}
            assert all(day.isoformat() in n.message for n in notes)
        finally:
            fresh.close()

    def test_monthly_target_for_old_month_rejected(self, db, monkeypatch):
        ceo = db.test_users["ceo"]
        today = today_local()
        # Two months ago: that month ended well over 7 days ago.
        if today.month > 2:
            old_month = date(today.year, today.month - 2, 1)
        else:
            old_month = date(today.year - 1, today.month + 10, 1)
        with pytest.raises(HTTPException) as exc:
            ceo_set_monthly_target(_target(old_month.isoformat()), REQUEST, user=ceo, db=db)
        assert exc.value.status_code == 400
        assert "past months" in exc.value.detail

    def test_monthly_target_current_month_allowed(self, db):
        ceo = db.test_users["ceo"]
        today = today_local()
        out = ceo_set_monthly_target(
            _target(date(today.year, today.month, 1).isoformat()), REQUEST, user=ceo, db=db)
        assert out["ok"] is True

    def test_monthly_target_window_is_configurable(self, db, monkeypatch):
        from src.config import settings
        ceo = db.test_users["ceo"]
        today = today_local()
        old_month = date(today.year, today.month - 2, 1) if today.month > 2 else date(today.year - 1, today.month + 10, 1)
        monkeypatch.setattr(settings, "REPORT_EDIT_WINDOW_DAYS", 90)
        out = ceo_set_monthly_target(_target(old_month.isoformat()), REQUEST, user=ceo, db=db)
        assert out["ok"] is True

    def test_ho_monthly_target_for_old_month_rejected(self, db):
        ho = db.test_users["ho"]
        today = today_local()
        old_month = date(today.year, today.month - 2, 1) if today.month > 2 else date(today.year - 1, today.month + 10, 1)
        with pytest.raises(HTTPException) as exc:
            ho_set_monthly_target(_target(old_month.isoformat()), REQUEST, user=ho, db=db)
        assert exc.value.status_code == 400


# ------------------------------------------------------------------ m-05

class TestMidRequestRevocationRecheck:
    def _revoke(self, db, user):
        # Simulate a logout/password change landing mid-request: the
        # session's token version is now stale in the database.
        user._token_tv = user.token_version
        db.query(User).filter(User.id == user.id).update(
            {"token_version": user.token_version + 1})
        db.commit()

    def test_ceo_submit_revoked_mid_request_401(self, db):
        ceo = db.test_users["ceo"]
        self._revoke(db, ceo)
        with pytest.raises(HTTPException) as exc:
            _ceo_submit(db, ceo)
        assert exc.value.status_code == 401

    def test_ho_submit_revoked_mid_request_401(self, db):
        ho = db.test_users["ho"]
        self._revoke(db, ho)
        with pytest.raises(HTTPException) as exc:
            _ho_submit(db, ho)
        assert exc.value.status_code == 401

    def test_ceo_submit_current_token_still_ok(self, db):
        ceo = db.test_users["ceo"]
        ceo._token_tv = ceo.token_version  # token as issued, never revoked
        assert _ceo_submit(db, ceo) == {"ok": True}


# ------------------------------------------------------------------ M-04

class TestAuditGapsClosed:
    def _audit_actions(self, db):
        fresh = _fresh_session(db)
        try:
            return [row.action for row in fresh.query(AuditLog).all()]
        finally:
            fresh.close()

    def test_create_business_unit_audited(self, db):
        from src.routers.admin import BUCreateIn, create_business_unit
        chairman = db.test_users["chairman"]
        out = create_business_unit(BUCreateIn(name="New Unit B"), user=chairman, db=db)
        actions = self._audit_actions(db)
        assert "BUSINESS_UNIT_CREATED" in actions
        fresh = _fresh_session(db)
        try:
            row = fresh.query(AuditLog).filter(
                AuditLog.action == "BUSINESS_UNIT_CREATED").one()
            assert f"bu_id={out['id']}" in (row.details or "")
        finally:
            fresh.close()

    def test_update_business_unit_audited(self, db):
        from src.routers.admin import BUUpdateIn, update_business_unit
        chairman = db.test_users["chairman"]
        update_business_unit(1, BUUpdateIn(name="Shop One Renamed"), user=chairman, db=db)
        assert "BUSINESS_UNIT_UPDATED" in self._audit_actions(db)

    def test_ceo_monthly_summary_audited(self, db):
        from src.routers.admin import ceo_monthly_summary
        chairman = db.test_users["chairman"]
        ceo_monthly_summary(business_unit_id=1, month="", user=chairman, db=db)
        assert "CEO_MONTHLY_SUMMARY_EXPORT" in self._audit_actions(db)

    def test_monthly_report_audited(self, db):
        from src.routers.ops import monthly_report
        chairman = db.test_users["chairman"]
        monthly_report(month=None, user=chairman, db=db)
        assert "MONTHLY_REPORT_EXPORT" in self._audit_actions(db)

    def test_export_excel_audited(self, db):
        from src.routers.ops import export_excel
        # Chairman (no BU filter): the CEO path of this endpoint has a
        # separate pre-existing filter-after-limit 500, flagged in the
        # workstream report rather than fixed here.
        chairman = db.test_users["chairman"]
        export_excel(tab="actions", user=chairman, db=db)
        assert "OPS_EXCEL_EXPORT" in self._audit_actions(db)

    def test_view_upload_audited(self, db):
        from src.models import ActivityLog
        from src.routers.ops import view_upload
        ceo = db.test_users["ceo"]
        db.add(ActivityLog(
            business_unit_id=1, report_date=today_local(),
            document_url="https://test-bucket.r2.dev/activity-attachments/abc123.png?X-Amz-Expires=900",
            submitted_by=ceo.id))
        db.commit()
        resp = view_upload(key="activity-attachments/abc123.png", user=ceo, db=db)
        assert resp.status_code == 302
        assert "VIEW_UPLOAD" in self._audit_actions(db)


# ------------------------------------------------------------------ M-05 / M-06

def _scan_engine():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(User(id=1, username="chairman", hashed_password="x",
                     role="Chairman", is_active=True))
    session.commit()
    session.close()
    return engine


def _seed_heartbeat(engine, job, age_hours, status="ok"):
    from datetime import timezone as _tz
    from src.models import JobHeartbeat
    session = sessionmaker(bind=engine)()
    session.merge(JobHeartbeat(
        job_name=job,
        last_run_at=datetime.now(_tz.utc).replace(tzinfo=None) - timedelta(hours=age_hours),
        last_status=status))
    session.commit()
    session.close()


def _notifications(engine):
    session = sessionmaker(bind=engine)()
    try:
        return session.query(Notification).all()
    finally:
        session.close()


def _stamp(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S,000")


class TestJobFailureBlindness:
    def test_failed_heartbeat_is_stale_even_when_fresh(self):
        import src.jobs.log_scan as log_scan
        now = datetime(2026, 11, 9, 12, 0, 0)
        assert log_scan.stale_jobs(
            [("scheduled_alerts", now - timedelta(hours=1), "failed"),
             ("log_scan", now - timedelta(hours=1), "ok")], now=now) == ["scheduled_alerts"]

    def test_ok_heartbeat_is_not_stale(self):
        import src.jobs.log_scan as log_scan
        now = datetime(2026, 11, 9, 12, 0, 0)
        assert log_scan.stale_jobs(
            [("scheduled_alerts", now - timedelta(hours=1), "ok"),
             ("log_scan", now - timedelta(hours=1), "ok")], now=now) == []

    def test_two_tuple_heartbeats_still_work(self):
        import src.jobs.log_scan as log_scan
        now = datetime(2026, 11, 9, 12, 0, 0)
        assert log_scan.stale_jobs(
            [("scheduled_alerts", now - timedelta(hours=1)),
             ("log_scan", now - timedelta(hours=1))], now=now) == []

    def test_check_heartbeats_reads_last_status(self):
        import src.jobs.log_scan as log_scan
        engine = _scan_engine()
        _seed_heartbeat(engine, "scheduled_alerts", 1, status="failed")
        _seed_heartbeat(engine, "log_scan", 1, status="ok")
        session = sessionmaker(bind=engine)()
        try:
            assert log_scan.check_heartbeats(session) == ["scheduled_alerts"]
        finally:
            session.close()
            engine.dispose()

    def test_failed_heartbeat_notifies_chairman_via_main(self, tmp_path, monkeypatch):
        import src.jobs.log_scan as log_scan
        engine = _scan_engine()
        _seed_heartbeat(engine, "scheduled_alerts", 1, status="failed")
        _seed_heartbeat(engine, "log_scan", 1, status="ok")
        log_file = tmp_path / "app.log"
        log_file.write_text(f"{_stamp(datetime.now() - timedelta(hours=1))} INFO ok\n")
        monkeypatch.setattr(log_scan, "SessionLocal", sessionmaker(bind=engine))
        assert log_scan.main(["--log-file", str(log_file), "--hours", "24"]) == 0
        notes = _notifications(engine)
        assert len(notes) == 1
        assert "scheduled_alerts" in notes[0].message
        engine.dispose()

    def test_scheduled_alerts_failure_notifies_chairman_and_ho(self, monkeypatch, caplog):
        import src.jobs.scheduled_alerts as scheduled_alerts
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        session = sessionmaker(bind=engine)()
        session.add_all([
            User(id=1, username="chairman", hashed_password="x", role="Chairman", is_active=True),
            User(id=2, username="ho", hashed_password="x", role="HO", is_active=True),
        ])
        session.commit()
        session.close()
        monkeypatch.setattr(scheduled_alerts, "SessionLocal", sessionmaker(bind=engine))
        monkeypatch.setattr(scheduled_alerts, "run_remind_missing_reports",
                            lambda db: (_ for _ in ()).throw(RuntimeError("boom")))
        with caplog.at_level("ERROR", logger="src.jobs.scheduled_alerts"):
            assert scheduled_alerts.main() == 1
        assert "scheduled_alerts failed" in caplog.text
        notes = _notifications(engine)
        assert {n.user_id for n in notes} == {1, 2}
        assert all(n.title == "Scheduled alerts failed" for n in notes)
        engine.dispose()


class TestLogScanBlindness:
    def test_empty_stdin_returns_one_and_notifies(self, monkeypatch):
        import io
        import src.jobs.log_scan as log_scan
        engine = _scan_engine()
        _seed_heartbeat(engine, "scheduled_alerts", 1)
        _seed_heartbeat(engine, "log_scan", 1)
        monkeypatch.setattr("sys.stdin", io.StringIO(""))
        monkeypatch.setattr(log_scan, "SessionLocal", sessionmaker(bind=engine))
        assert log_scan.main(["--log-file", "-"]) == 1
        notes = _notifications(engine)
        assert len(notes) == 1
        assert "no log lines read" in notes[0].message
        engine.dispose()

    def test_empty_log_file_returns_one(self, tmp_path, monkeypatch):
        import src.jobs.log_scan as log_scan
        engine = _scan_engine()
        _seed_heartbeat(engine, "scheduled_alerts", 1)
        _seed_heartbeat(engine, "log_scan", 1)
        log_file = tmp_path / "empty.log"
        log_file.write_text("")
        monkeypatch.setattr(log_scan, "SessionLocal", sessionmaker(bind=engine))
        assert log_scan.main(["--log-file", str(log_file)]) == 1
        engine.dispose()

    def test_clean_log_but_db_down_returns_one(self, tmp_path, monkeypatch, capsys):
        import src.jobs.log_scan as log_scan
        log_file = tmp_path / "app.log"
        log_file.write_text(f"{_stamp(datetime.now() - timedelta(hours=1))} INFO ok\n")

        def _raise():
            raise RuntimeError("db down")

        monkeypatch.setattr(log_scan, "SessionLocal", _raise)
        assert log_scan.main(["--log-file", str(log_file)]) == 1
        err = capsys.readouterr().err
        assert "heartbeat check unavailable" in err
        assert "ALERT" in err


# ------------------------------------------------------------------ m-08 / m-11

class TestMissingReportDedupeAndLock:
    def test_double_run_sends_one_notification(self, db):
        from decimal import Decimal
        from src.models import DailyFinancial
        from src.routers.ops import run_remind_missing_reports
        # BU1 submitted today; BU2 did not.
        db.add(DailyFinancial(business_unit_id=1, report_date=today_local(),
                              revenue=Decimal("1000"), expenses=Decimal("0"),
                              net_profit=Decimal("0"), cash_in_hand=Decimal("0")))
        db.commit()
        assert run_remind_missing_reports(db) == 1
        assert run_remind_missing_reports(db) == 0
        notes = db.query(Notification).filter(
            Notification.type == "missing_report").all()
        assert len(notes) == 1
        assert notes[0].user_id == db.test_users["ceo2"].id

    def test_manual_endpoints_409_when_a_run_is_in_progress(self, db):
        import src.routers.ops as ops
        chairman = db.test_users["chairman"]
        assert ops._REMINDER_LOCK.acquire(blocking=False)
        try:
            for call in (lambda: ops.remind_missing(user=chairman, db=db),
                         lambda: ops.remind_actions(user=chairman, db=db),
                         lambda: ops.check_document_expiry(user=chairman, db=db)):
                with pytest.raises(HTTPException) as exc:
                    call()
                assert exc.value.status_code == 409
                assert "already in progress" in exc.value.detail
        finally:
            ops._REMINDER_LOCK.release()

    def test_manual_endpoint_works_when_lock_free(self, db):
        import src.routers.ops as ops
        chairman = db.test_users["chairman"]
        assert ops.remind_missing(user=chairman, db=db)["notified"] == 2


class TestExpiringSoonSqlFilter:
    def _seed_docs(self, db):
        from src.models import LicenseDocument
        today = today_local()
        docs = [
            ("Soon 5d", today + timedelta(days=5)),
            ("Soon 10d", today + timedelta(days=10)),
            ("Expired", today - timedelta(days=1)),
            ("Later 40d", today + timedelta(days=40)),
            ("Later 100d", today + timedelta(days=100)),
        ]
        for title, expiry in docs:
            db.add(LicenseDocument(business_unit_id=1, title=title,
                                   document_type="license", expiry_date=expiry,
                                   created_by_id=db.test_users["ceo"].id))
        db.commit()

    def test_expiring_soon_counts_all_matches_not_just_the_page(self, db):
        from src.routers.ops import list_documents
        self._seed_docs(db)
        ceo = db.test_users["ceo"]
        # 3 matching rows (5d, 10d, expired); limit 2 must still report 3.
        out = list_documents(expiring_soon=True, limit=2, offset=0, user=ceo, db=db)
        assert out["count"] == 3
        assert len(out["items"]) == 2
        assert {i["title"] for i in out["items"]} <= {"Soon 5d", "Soon 10d", "Expired"}

    def test_expiring_soon_second_page_reaches_remaining_match(self, db):
        from src.routers.ops import list_documents
        self._seed_docs(db)
        ceo = db.test_users["ceo"]
        out = list_documents(expiring_soon=True, limit=2, offset=2, user=ceo, db=db)
        assert out["count"] == 3
        assert len(out["items"]) == 1
        assert out["items"][0]["title"] in {"Soon 5d", "Soon 10d", "Expired"}

    def test_expiring_soon_never_returns_far_future_docs(self, db):
        from src.routers.ops import list_documents
        self._seed_docs(db)
        ceo = db.test_users["ceo"]
        out = list_documents(expiring_soon=True, limit=100, offset=0, user=ceo, db=db)
        assert {i["title"] for i in out["items"]} == {"Soon 5d", "Soon 10d", "Expired"}

    def test_unfiltered_list_still_returns_everything(self, db):
        from src.routers.ops import list_documents
        self._seed_docs(db)
        ceo = db.test_users["ceo"]
        out = list_documents(expiring_soon=False, limit=100, offset=0, user=ceo, db=db)
        assert out["count"] == 5
        assert len(out["items"]) == 5
