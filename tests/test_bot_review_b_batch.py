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

from src.models import Base, BusinessUnit, Notification, User
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
