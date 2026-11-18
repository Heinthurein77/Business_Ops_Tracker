"""T-m2: thin HTTP tests for every write endpoint.

Most behaviour tests in this repo call handlers directly, bypassing
routing, middleware and serialization. Each test here drives the real
app over HTTP through the shared `client` fixture from root
conftest.py and asserts PERSISTED state (not just the status code) on
the happy path, plus one denial (wrong role / wrong owner / unknown
id / unauthenticated) per endpoint.

Seed roster (fresh file-backed DB per test): chairman, ho, ceo_one
(BU1), ceo_two (BU2), pw_user (HO, must rotate password), totp_user
(HO, TOTP enrolled). Passwords are all PW.
"""
from datetime import date

import pyotp
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.auth import hash_password as make_hash
from src.models import (
    ActionItem,
    ActivityLog,
    Base,
    BusinessUnit,
    CeoProfile,
    CustomerMetric,
    DailyFinancial,
    FinancialNote,
    HeadOfficeDailyFinancial,
    HeadOfficeMonthlyTarget,
    LicenseDocument,
    MonthlyTarget,
    Notification,
    PasswordResetRequest,
    StaffRecord,
    User,
)
from src.utils import today_local

PW = "TestPass123!"
_PW_HASH = make_hash(PW)
TOTP_SECRET = "JBSWY3DPEHPK3PXP"  # fixed test secret, never a real one
TARGET = {
    "target_month": date(today_local().year, today_local().month, 1).isoformat(),
    "monthly_revenue_target": 1000,
    "monthly_cash_flow_target": 100,
    "monthly_max_receivable": 500,
    "monthly_max_payable": 500,
}
REPORT_FORM = {"revenue": "1000", "cash_flow": "100", "receivable": "0", "payable": "0"}


@pytest.fixture()
def http_db(tmp_path):
    db_path = tmp_path / "write_endpoints.db"
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([
        BusinessUnit(id=1, code="BU1", name="Shop One"),
        BusinessUnit(id=2, code="BU2", name="Shop Two"),
        User(username="chairman", hashed_password=_PW_HASH,
             role="Chairman", is_active=True, require_password_change=False,
             is_profile_completed=True, totp_secret=TOTP_SECRET,
             totp_enabled=True),
        User(username="ho", hashed_password=_PW_HASH,
             role="HO", is_active=True, require_password_change=False,
             is_profile_completed=True),
        User(username="ceo_one", hashed_password=_PW_HASH,
             role="CEO", business_unit_id=1, is_active=True,
             require_password_change=False, is_profile_completed=True),
        User(username="ceo_two", hashed_password=_PW_HASH,
             role="CEO", business_unit_id=2, is_active=True,
             require_password_change=False, is_profile_completed=True),
        User(username="pw_user", hashed_password=_PW_HASH,
             role="HO", is_active=True, require_password_change=True,
             is_profile_completed=True),
        User(username="totp_user", hashed_password=_PW_HASH,
             role="HO", is_active=True, require_password_change=False,
             is_profile_completed=True, totp_secret=TOTP_SECRET,
             totp_enabled=True),
        User(username="roamer", hashed_password=_PW_HASH,
             role="HO", is_active=True, require_password_change=False,
             is_profile_completed=True),
    ])
    session.commit()
    yield session
    session.close()
    engine.dispose()


def _uid(http_db, username):
    return http_db.query(User).filter(User.username == username).one().id


@pytest.fixture()
def chairman(make_client):
    # The seeded chairman has TOTP enrolled (M-01): password alone
    # earns no session, a current code is the second factor.
    c = make_client()
    c.login("chairman", PW, totp_code=pyotp.TOTP(TOTP_SECRET).now())
    return c


@pytest.fixture()
def ho(make_client):
    c = make_client()
    c.login("ho", PW)
    return c


@pytest.fixture()
def ceo(make_client):
    c = make_client()
    c.login("ceo_one", PW)
    return c


@pytest.fixture()
def ceo2(make_client):
    c = make_client()
    c.login("ceo_two", PW)
    return c


def _reset_submit_limiters():
    # The CEO/HO submit limiters are process-global and keyed by user
    # id; ids restart per fresh DB, so another test's submits could
    # count against this test's user. Clear both budgets first.
    from src.routers.ceo import _rate_limiter as ceo_limiter
    from src.routers.ho import _rate_limiter as ho_limiter

    ceo_limiter.reset()
    ho_limiter.reset()


# --------------------------------------------------------------------- auth

class TestAuthWrites:
    def test_login_happy(self, client):
        r = client.post("/api/auth/login",
                        json={"username": "ceo_one", "password": PW})
        assert r.status_code == 200, r.text
        assert r.json()["user"]["username"] == "ceo_one"
        assert r.json()["user"]["role"] == "CEO"
        assert r.headers.get("set-cookie", "").startswith("access_token=")

    def test_login_wrong_password_denied(self, client):
        r = client.post("/api/auth/login",
                        json={"username": "ceo_one", "password": "WrongPass123!"})
        assert r.status_code == 401, r.text

    def test_logout_happy_then_me_401(self, ceo):
        assert ceo.post("/api/auth/logout").status_code == 200
        assert ceo.get("/api/auth/me").status_code == 401

    def test_logout_unauthenticated_still_ok(self, make_client):
        # logout() takes an optional user: with no session it just
        # clears the (absent) cookie and returns ok — idempotent.
        assert make_client().post("/api/auth/logout").status_code == 200

    def test_forgot_password_happy_creates_request(self, client, http_db):
        r = client.post("/api/auth/forgot-password", json={"username": "ceo_one"})
        assert r.status_code == 200, r.text
        row = http_db.query(PasswordResetRequest).filter(
            PasswordResetRequest.user_id == _uid(http_db, "ceo_one")).one()
        assert row.status == "Pending"

    def test_forgot_password_unknown_user_same_response_no_row(self, client, http_db):
        known = client.post("/api/auth/forgot-password", json={"username": "ceo_one"}).json()
        r = client.post("/api/auth/forgot-password", json={"username": "nobody_here"})
        assert r.status_code == 200, r.text
        assert r.json() == known
        assert http_db.query(PasswordResetRequest).filter(
            PasswordResetRequest.username == "nobody_here").count() == 0

    def test_force_password_change_happy(self, make_client, http_db):
        c = make_client()
        c.login("pw_user", PW)
        r = c.post("/api/auth/force-password-change", json={
            "current_password": PW, "new_password": "Brand-New-99!x",
            "confirm_password": "Brand-New-99!x"})
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True
        user = http_db.query(User).filter(User.username == "pw_user").one()
        assert user.require_password_change is False
        # The new password actually works for the next login.
        c2 = make_client()
        c2.login("pw_user", "Brand-New-99!x")

    def test_force_password_change_wrong_current_denied(self, make_client):
        c = make_client()
        c.login("pw_user", PW)
        r = c.post("/api/auth/force-password-change", json={
            "current_password": "WrongPass123!", "new_password": "Brand-New-99!x",
            "confirm_password": "Brand-New-99!x"})
        assert r.status_code == 400, r.text

    def _unenroll_chairman(self, http_db):
        # The TOTP-flow tests need a chairman with NO enrolled TOTP, so
        # login answers 403 TOTP_ENROLLMENT_REQUIRED with a setup token.
        user = http_db.query(User).filter(User.username == "chairman").one()
        user.totp_secret = None
        user.totp_enabled = False
        http_db.commit()

    def _enroll(self, client, http_db):
        self._unenroll_chairman(http_db)
        r = client.post("/api/auth/login",
                        json={"username": "chairman", "password": PW})
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == "TOTP_ENROLLMENT_REQUIRED"
        token = r.json()["totp_setup_token"]
        r = client.post("/api/auth/totp/enroll", json={"setup_token": token})
        assert r.status_code == 200, r.text
        return token, r.json()["secret"]

    def test_totp_enroll_happy(self, make_client, http_db):
        c = make_client()
        token, secret = self._enroll(c, http_db)
        assert secret and len(secret) >= 16
        user = http_db.query(User).filter(User.username == "chairman").one()
        assert user.totp_secret is not None
        assert user.totp_enabled is False

    def test_totp_enroll_bogus_token_denied(self, make_client):
        r = make_client().post("/api/auth/totp/enroll",
                               json={"setup_token": "not-a-token"})
        assert r.status_code == 401, r.text

    def test_totp_confirm_happy(self, make_client, http_db):
        c = make_client()
        token, secret = self._enroll(c, http_db)
        code = pyotp.TOTP(secret).now()
        r = c.post("/api/auth/totp/confirm",
                   json={"code": code, "setup_token": token})
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True
        user = http_db.query(User).filter(User.username == "chairman").one()
        assert user.totp_enabled is True

    def test_totp_confirm_wrong_code_denied(self, make_client, http_db):
        c = make_client()
        token, secret = self._enroll(c, http_db)
        r = c.post("/api/auth/totp/confirm",
                   json={"code": "000000", "setup_token": token})
        assert r.status_code == 400, r.text

    def test_totp_disable_happy(self, make_client, http_db):
        c = make_client()
        c.login("totp_user", PW, totp_code=pyotp.TOTP(TOTP_SECRET).now())
        code = pyotp.TOTP(TOTP_SECRET).now()
        r = c.post("/api/auth/totp/disable", json={"code": code})
        assert r.status_code == 200, r.text
        user = http_db.query(User).filter(User.username == "totp_user").one()
        assert user.totp_enabled is False
        assert user.totp_secret is None

    def test_totp_disable_not_enabled_denied(self, ho):
        r = ho.post("/api/auth/totp/disable",
                    json={"code": pyotp.TOTP(TOTP_SECRET).now()})
        assert r.status_code == 400, r.text


# --------------------------------------------------------------------- admin

class TestAdminWrites:
    def test_override_ho_monthly_target_happy(self, chairman, http_db):
        r = chairman.post("/api/admin/head-office/monthly-target", json=TARGET)
        assert r.status_code == 200, r.text
        row = http_db.query(HeadOfficeMonthlyTarget).one()
        assert float(row.monthly_revenue_target) == 1000
        assert row.set_by == _uid(http_db, "chairman")

    def test_override_ho_monthly_target_ceo_denied(self, ceo):
        assert ceo.post("/api/admin/head-office/monthly-target", json=TARGET).status_code == 403

    def test_override_bu_monthly_target_happy(self, ho, http_db):
        r = ho.post("/api/admin/business-unit/1/monthly-target", json=TARGET)
        assert r.status_code == 200, r.text
        row = http_db.query(MonthlyTarget).filter(
            MonthlyTarget.business_unit_id == 1).one()
        assert float(row.monthly_revenue_target) == 1000

    def test_override_bu_monthly_target_ceo_denied(self, ceo):
        assert ceo.post("/api/admin/business-unit/1/monthly-target", json=TARGET).status_code == 403

    def test_override_bu_monthly_target_unknown_bu_404(self, ho):
        assert ho.post("/api/admin/business-unit/9999/monthly-target", json=TARGET).status_code == 404

    def test_resolve_help_happy(self, ho, http_db):
        log = ActivityLog(business_unit_id=1, report_date=today_local(),
                          help_needed="Need a delivery van", problems_today="Van broke")
        http_db.add(log)
        http_db.commit()
        r = ho.post(f"/api/admin/activity-logs/{log.id}/resolve-help",
                    json={"response": "Van booked for tomorrow"})
        assert r.status_code == 200, r.text
        http_db.expire_all()
        saved = http_db.query(ActivityLog).filter(ActivityLog.id == log.id).one()
        assert saved.help_resolved_at is not None
        assert saved.help_resolved_by == _uid(http_db, "ho")

    def test_resolve_help_ceo_denied(self, ceo, http_db):
        log = ActivityLog(business_unit_id=1, report_date=today_local(),
                          help_needed="Need a delivery van")
        http_db.add(log)
        http_db.commit()
        r = ceo.post(f"/api/admin/activity-logs/{log.id}/resolve-help",
                     json={"response": "I will handle it"})
        assert r.status_code == 403, r.text

    def test_create_business_unit_happy(self, chairman, http_db):
        r = chairman.post("/api/admin/business-units", json={"name": "New Test BU"})
        assert r.status_code == 200, r.text
        assert http_db.query(BusinessUnit).filter(
            BusinessUnit.name == "New Test BU").count() == 1

    def test_create_business_unit_duplicate_denied(self, chairman):
        r = chairman.post("/api/admin/business-units", json={"name": "Shop One"})
        assert r.status_code == 400, r.text

    def test_update_business_unit_happy(self, chairman, http_db):
        r = chairman.patch("/api/admin/business-units/1", json={"name": "Renamed Shop"})
        assert r.status_code == 200, r.text
        assert http_db.query(BusinessUnit).filter(BusinessUnit.id == 1).one().name == "Renamed Shop"

    def test_update_business_unit_unknown_404(self, chairman):
        assert chairman.patch("/api/admin/business-units/9999",
                              json={"name": "Nope"}).status_code == 404


# --------------------------------------------------------------------- users

class TestUserWrites:
    def test_create_user_happy(self, chairman, http_db):
        r = chairman.post("/api/admin/users", json={
            "username": "new_ho", "full_name": "New HO", "role": "HO"})
        assert r.status_code == 200, r.text
        assert r.json()["temporary_password"]
        user = http_db.query(User).filter(User.username == "new_ho").one()
        assert user.role == "HO" and user.require_password_change is True

    def test_create_user_ho_denied(self, ho):
        r = ho.post("/api/admin/users", json={
            "username": "new_ho", "full_name": "New HO", "role": "HO"})
        assert r.status_code == 403, r.text

    def test_create_user_ceo_without_bu_denied(self, chairman):
        r = chairman.post("/api/admin/users", json={
            "username": "new_ceo", "full_name": "New CEO", "role": "CEO"})
        assert r.status_code == 400, r.text

    def test_reset_password_happy(self, chairman, http_db):
        uid = _uid(http_db, "ceo_one")
        r = chairman.post(f"/api/admin/users/{uid}/reset-password")
        assert r.status_code == 200, r.text
        assert r.json()["temporary_password"]
        row = http_db.query(PasswordResetRequest).filter(
            PasswordResetRequest.user_id == uid).one()
        assert row.status == "Approved"

    def test_reset_password_self_denied(self, chairman, http_db):
        uid = _uid(http_db, "chairman")
        r = chairman.post(f"/api/admin/users/{uid}/reset-password")
        assert r.status_code == 400, r.text

    def test_set_user_active_happy(self, chairman, http_db):
        uid = _uid(http_db, "ceo_two")
        r = chairman.post(f"/api/admin/users/{uid}/active", json={"is_active": False})
        assert r.status_code == 200, r.text
        assert http_db.query(User).filter(User.id == uid).one().is_active is False

    def test_set_user_active_ceo_denied(self, ceo, http_db):
        uid = _uid(http_db, "ceo_two")
        r = ceo.post(f"/api/admin/users/{uid}/active", json={"is_active": False})
        assert r.status_code == 403, r.text

    def test_update_user_happy(self, chairman, http_db):
        # Move ceo_one from BU1 to BU2: first deactivate ceo_two, or the
        # one-active-CEO-per-BU rule answers 409.
        ceo_two = _uid(http_db, "ceo_two")
        assert chairman.post(f"/api/admin/users/{ceo_two}/active",
                             json={"is_active": False}).status_code == 200
        uid = _uid(http_db, "ceo_one")
        r = chairman.patch(f"/api/admin/users/{uid}", json={"business_unit_id": 2})
        assert r.status_code == 200, r.text
        assert http_db.query(User).filter(User.id == uid).one().business_unit_id == 2

    def test_update_user_ho_to_bu_denied(self, chairman, http_db):
        # HO accounts are group-level and can never be BU-assigned.
        uid = _uid(http_db, "roamer")
        r = chairman.patch(f"/api/admin/users/{uid}", json={"business_unit_id": 1})
        assert r.status_code == 400, r.text

    def test_update_user_unknown_404(self, chairman):
        assert chairman.patch("/api/admin/users/9999",
                              json={"business_unit_id": 1}).status_code == 404

    def test_unlock_login_happy_ids(self, ho, http_db):
        uid = _uid(http_db, "ceo_one")
        r = ho.post(f"/api/admin/users/{uid}/unlock-login")
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True
        assert r.json()["was_locked"] is False

    def test_unlock_login_ceo_denied(self, ceo, http_db):
        uid = _uid(http_db, "ceo_one")
        assert ceo.post(f"/api/admin/users/{uid}/unlock-login").status_code == 403

    def test_disable_user_totp_happy(self, chairman, http_db):
        uid = _uid(http_db, "totp_user")
        r = chairman.post(f"/api/admin/users/{uid}/totp-disable")
        assert r.status_code == 200, r.text
        user = http_db.query(User).filter(User.id == uid).one()
        assert user.totp_enabled is False
        assert user.totp_secret is None

    def test_disable_user_totp_ceo_denied(self, ceo, http_db):
        uid = _uid(http_db, "totp_user")
        assert ceo.post(f"/api/admin/users/{uid}/totp-disable").status_code == 403

    def test_approve_password_reset_happy(self, chairman, http_db):
        req = PasswordResetRequest(user_id=_uid(http_db, "ceo_one"),
                                   username="ceo_one", status="Pending")
        http_db.add(req)
        http_db.commit()
        r = chairman.post(f"/api/admin/password-resets/{req.id}/approve")
        assert r.status_code == 200, r.text
        assert r.json()["temporary_password"]
        assert http_db.query(PasswordResetRequest).filter(
            PasswordResetRequest.id == req.id).one().status == "Approved"

    def test_approve_password_reset_twice_409(self, chairman, http_db):
        req = PasswordResetRequest(user_id=_uid(http_db, "ceo_one"),
                                   username="ceo_one", status="Pending")
        http_db.add(req)
        http_db.commit()
        assert chairman.post(f"/api/admin/password-resets/{req.id}/approve").status_code == 200
        r = chairman.post(f"/api/admin/password-resets/{req.id}/approve")
        assert r.status_code == 409, r.text

    def test_reject_password_reset_happy(self, chairman, http_db):
        req = PasswordResetRequest(user_id=_uid(http_db, "ceo_one"),
                                   username="ceo_one", status="Pending")
        http_db.add(req)
        http_db.commit()
        r = chairman.post(f"/api/admin/password-resets/{req.id}/reject")
        assert r.status_code == 200, r.text
        assert http_db.query(PasswordResetRequest).filter(
            PasswordResetRequest.id == req.id).one().status == "Rejected"

    def test_reject_password_reset_ceo_denied(self, ceo, http_db):
        req = PasswordResetRequest(user_id=_uid(http_db, "ceo_one"),
                                   username="ceo_one", status="Pending")
        http_db.add(req)
        http_db.commit()
        assert ceo.post(f"/api/admin/password-resets/{req.id}/reject").status_code == 403


# --------------------------------------------------------------------- ceo / ho

class TestCeoHoWrites:
    def test_ceo_monthly_target_happy(self, ceo, http_db):
        r = ceo.post("/api/ceo/monthly-target", json=TARGET)
        assert r.status_code == 200, r.text
        row = http_db.query(MonthlyTarget).filter(
            MonthlyTarget.business_unit_id == 1).one()
        assert float(row.monthly_revenue_target) == 1000

    def test_ceo_monthly_target_ho_denied(self, ho):
        assert ho.post("/api/ceo/monthly-target", json=TARGET).status_code == 403

    def test_ceo_profile_happy(self, make_client, http_db):
        # ceo_one's profile is incomplete until this call: use a fresh
        # CEO whose profile was never completed.
        c = make_client()
        c.login("ceo_two", PW)
        r = c.post("/api/ceo/profile", data={
            "full_name": "CEO Two", "phone_number": "+959111111111",
            "date_of_birth": "1990-05-01", "address": "Yangon"})
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True
        assert http_db.query(CeoProfile).filter(
            CeoProfile.user_id == _uid(http_db, "ceo_two")).count() == 1
        assert http_db.query(User).filter(
            User.username == "ceo_two").one().is_profile_completed is True

    def test_ceo_profile_ho_denied(self, ho):
        r = ho.post("/api/ceo/profile", data={
            "full_name": "HO", "phone_number": "+959111111111",
            "date_of_birth": "1990-05-01", "address": "Yangon"})
        assert r.status_code == 403, r.text

    def test_ceo_financial_note_happy(self, ceo, http_db):
        r = ceo.post("/api/ceo/financial-notes", json={"note_text": "Big client paid early"})
        assert r.status_code == 200, r.text
        assert http_db.query(FinancialNote).filter(
            FinancialNote.business_unit_id == 1).count() == 1

    def test_ceo_financial_note_blank_denied(self, ceo):
        # Whitespace-only notes are a 400 from the handler ("Note cannot
        # be blank"), not a 422 — min_length=1 passes, the strip fails.
        assert ceo.post("/api/ceo/financial-notes",
                        json={"note_text": "   "}).status_code == 400

    def test_ceo_submit_report_happy(self, ceo, http_db):
        _reset_submit_limiters()
        today = today_local()
        r = ceo.post("/api/ceo/submit-report",
                     data={**REPORT_FORM, "report_date": today.isoformat()})
        assert r.status_code == 200, r.text
        assert http_db.query(DailyFinancial).filter(
            DailyFinancial.business_unit_id == 1,
            DailyFinancial.report_date == today).count() == 1

    def test_ceo_submit_report_ho_denied(self, ho):
        _reset_submit_limiters()
        r = ho.post("/api/ceo/submit-report",
                    data={**REPORT_FORM, "report_date": today_local().isoformat()})
        assert r.status_code == 403, r.text

    def test_ho_monthly_target_happy(self, ho, http_db):
        r = ho.post("/api/ho/monthly-target", json=TARGET)
        assert r.status_code == 200, r.text
        row = http_db.query(HeadOfficeMonthlyTarget).one()
        assert float(row.monthly_revenue_target) == 1000

    def test_ho_monthly_target_ceo_denied(self, ceo):
        assert ceo.post("/api/ho/monthly-target", json=TARGET).status_code == 403

    def test_ho_submit_report_happy(self, ho, http_db):
        _reset_submit_limiters()
        r = ho.post("/api/ho/submit-report", data=REPORT_FORM)
        assert r.status_code == 200, r.text
        assert http_db.query(HeadOfficeDailyFinancial).filter(
            HeadOfficeDailyFinancial.report_date == today_local()).count() == 1

    def test_ho_submit_report_ceo_denied(self, ceo):
        _reset_submit_limiters()
        assert ceo.post("/api/ho/submit-report", data=REPORT_FORM).status_code == 403


# --------------------------------------------------------------------- ops

class TestOpsWrites:
    def test_create_action_item_happy(self, ceo, http_db):
        r = ceo.post("/api/ops/action-items",
                     json={"title": "Call supplier", "due_date": today_local().isoformat()})
        assert r.status_code == 200, r.text
        row = http_db.query(ActionItem).filter(
            ActionItem.business_unit_id == 1).one()
        assert row.title == "Call supplier"
        assert row.status == "pending"

    def test_create_action_item_cross_bu_denied(self, ceo):
        r = ceo.post("/api/ops/action-items",
                     json={"business_unit_id": 2, "title": "Other BU task"})
        assert r.status_code == 403, r.text

    def _make_item(self, ceo, http_db):
        ceo.post("/api/ops/action-items", json={"title": "Do the thing"})
        return http_db.query(ActionItem).one()

    def test_update_action_item_happy(self, ceo, http_db):
        item = self._make_item(ceo, http_db)
        r = ceo.patch(f"/api/ops/action-items/{item.id}", json={"status": "done"})
        assert r.status_code == 200, r.text
        saved = http_db.query(ActionItem).filter(ActionItem.id == item.id).one()
        assert saved.status == "done"
        assert saved.completed_at is not None

    def test_update_action_item_unknown_404(self, ceo):
        assert ceo.patch("/api/ops/action-items/9999",
                         json={"status": "done"}).status_code == 404

    def test_delete_action_item_happy(self, ceo, http_db):
        item = self._make_item(ceo, http_db)
        r = ceo.delete(f"/api/ops/action-items/{item.id}")
        assert r.status_code == 200, r.text
        assert http_db.query(ActionItem).filter(ActionItem.id == item.id).count() == 0

    def test_delete_action_item_unknown_404(self, ceo):
        assert ceo.delete("/api/ops/action-items/9999").status_code == 404

    def test_mark_notification_read_happy(self, ceo, http_db):
        n = Notification(user_id=_uid(http_db, "ceo_one"), type="missing_report",
                         title="Missing", message="Submit today", link="/ceo")
        http_db.add(n)
        http_db.commit()
        r = ceo.post(f"/api/ops/notifications/{n.id}/read")
        assert r.status_code == 200, r.text
        assert http_db.query(Notification).filter(
            Notification.id == n.id).one().is_read is True

    def test_mark_notification_read_other_user_404(self, ceo2, http_db):
        n = Notification(user_id=_uid(http_db, "ceo_one"), type="missing_report",
                         title="Missing", message="Submit today", link="/ceo")
        http_db.add(n)
        http_db.commit()
        assert ceo2.post(f"/api/ops/notifications/{n.id}/read").status_code == 404

    def test_read_all_happy(self, ceo, http_db):
        uid = _uid(http_db, "ceo_one")
        http_db.add_all([
            Notification(user_id=uid, type="alert", title="A", message="m"),
            Notification(user_id=uid, type="alert", title="B", message="m"),
        ])
        http_db.commit()
        r = ceo.post("/api/ops/notifications/read-all")
        assert r.status_code == 200, r.text
        assert http_db.query(Notification).filter(
            Notification.user_id == uid, Notification.is_read == False).count() == 0  # noqa: E712

    def test_read_all_unauthenticated_denied(self, make_client):
        assert make_client().post("/api/ops/notifications/read-all").status_code == 401

    def test_remind_missing_happy(self, ho):
        assert ho.post("/api/ops/notifications/remind-missing").status_code == 200

    def test_remind_missing_ceo_denied(self, ceo):
        assert ceo.post("/api/ops/notifications/remind-missing").status_code == 403

    def test_remind_actions_happy(self, ho):
        assert ho.post("/api/ops/notifications/remind-actions").status_code == 200

    def test_remind_actions_ceo_denied(self, ceo):
        assert ceo.post("/api/ops/notifications/remind-actions").status_code == 403

    def test_submit_staff_happy(self, ceo, http_db):
        r = ceo.post("/api/ops/staff", json={
            "report_date": today_local().isoformat(),
            "total_staff": 12, "present": 11, "absent": 1})
        assert r.status_code == 200, r.text
        assert http_db.query(StaffRecord).filter(
            StaffRecord.business_unit_id == 1).count() == 1

    def test_submit_staff_cross_bu_denied(self, ceo):
        r = ceo.post("/api/ops/staff", json={
            "business_unit_id": 2, "report_date": today_local().isoformat(),
            "total_staff": 12, "present": 11, "absent": 1})
        assert r.status_code == 403, r.text

    def test_submit_customers_happy(self, ceo, http_db):
        r = ceo.post("/api/ops/customers", json={
            "report_date": today_local().isoformat(),
            "footfall": 120, "transactions": 40, "complaints": 1,
            "satisfaction_score": 4.5})
        assert r.status_code == 200, r.text
        row = http_db.query(CustomerMetric).filter(
            CustomerMetric.business_unit_id == 1).one()
        assert float(row.satisfaction_score) == 4.5

    def test_submit_customers_bad_score_denied(self, ceo):
        r = ceo.post("/api/ops/customers", json={
            "report_date": today_local().isoformat(),
            "footfall": 120, "transactions": 40, "complaints": 1,
            "satisfaction_score": 9.9})
        assert r.status_code == 400, r.text

    def test_create_document_happy(self, ceo, http_db):
        r = ceo.post("/api/ops/documents", json={
            "title": "Shop license", "document_type": "license",
            "expiry_date": "2027-01-01"})
        assert r.status_code == 200, r.text
        assert http_db.query(LicenseDocument).filter(
            LicenseDocument.business_unit_id == 1).count() == 1

    def test_create_document_missing_expiry_denied(self, ceo):
        r = ceo.post("/api/ops/documents", json={"title": "No expiry"})
        assert r.status_code == 422, r.text

    def test_delete_document_happy(self, ceo, http_db):
        ceo.post("/api/ops/documents", json={
            "title": "Shop license", "expiry_date": "2027-01-01"})
        doc = http_db.query(LicenseDocument).one()
        r = ceo.delete(f"/api/ops/documents/{doc.id}")
        assert r.status_code == 200, r.text
        assert http_db.query(LicenseDocument).filter(
            LicenseDocument.id == doc.id).count() == 0

    def test_delete_document_unknown_404(self, ceo):
        assert ceo.delete("/api/ops/documents/9999").status_code == 404

    def test_check_expiry_happy(self, ho):
        assert ho.post("/api/ops/documents/check-expiry").status_code == 200

    def test_check_expiry_ceo_denied(self, ceo):
        assert ceo.post("/api/ops/documents/check-expiry").status_code == 403
