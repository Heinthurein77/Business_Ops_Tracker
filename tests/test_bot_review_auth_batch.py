"""Tests for the BOT code-quality + security review fixes, workstream A
(auth/secrets/compose): C-1, C-2, M-01, M-02, m-17, m-27, m-28, m-29,
m-30, m-32, m-35.

M-02 and m-35 are covered by the updated tests in
test_security_fixes.py (seed guards) and test_config_guards.py (prod
config guards); the tests here cover the rest, plus the end-to-end
behaviour of the shared pieces.
"""
import asyncio
import stat
from datetime import timedelta
from pathlib import Path

import httpx
import pyotp
import pytest
from fastapi import HTTPException, Response, UploadFile
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from src.auth import hash_password, verify_password
from src.database import get_db
from src.main import app
from src.models import AuditLog, Base, BusinessUnit, PasswordHistory, User
from src.schemas import ForcePasswordChangeRequest, TotpEnrollRequest
from src.utils import utcnow

ROOT = Path(__file__).resolve().parent.parent
TEST_TOTP_SECRET = "JBSWY3DPEHPK3PXP"  # fixed test secret, never a real one
PUBLIC_A = "8.8.8.8"
PUBLIC_B = "1.1.1.1"


def make_request(peer="8.8.8.8", headers=None):
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "headers": raw, "client": (peer, 5555) if peer else None})


def _totp_code(secret=TEST_TOTP_SECRET):
    return pyotp.TOTP(secret).now()


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


def _add_user(db, username, role, password="S3cure-Horse-99!", business_unit_id=None,
              is_active=True, totp_secret=None, totp_enabled=False):
    user = User(username=username, hashed_password=hash_password(password),
                role=role, business_unit_id=business_unit_id, is_active=is_active,
                require_password_change=False, is_profile_completed=True,
                totp_secret=totp_secret, totp_enabled=totp_enabled)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture()
def http_db(tmp_path):
    """File-backed SQLite DB shared with the app under test (see
    test_http_smoke.py: sync endpoints run in a worker thread)."""
    db_path = tmp_path / "http.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(BusinessUnit(id=1, code="BU1", name="Business Unit 1"))
    session.add(User(username="chairman", hashed_password=hash_password("S3cure-Horse-99!"),
                     role="Chairman", is_active=True, require_password_change=False,
                     is_profile_completed=True,
                     totp_secret=TEST_TOTP_SECRET, totp_enabled=True))
    session.add(User(username="ho_staff", hashed_password=hash_password("S3cure-Horse-99!"),
                     role="HO", is_active=True, require_password_change=False,
                     is_profile_completed=True))
    session.add(User(username="ceo_bu1", hashed_password=hash_password("S3cure-Horse-99!"),
                     role="CEO", business_unit_id=1, is_active=True,
                     require_password_change=False, is_profile_completed=True))
    session.commit()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def client(http_db):
    def _override():
        try:
            yield http_db
        finally:
            pass

    app.dependency_overrides[get_db] = _override
    try:
        class SyncClient:
            def request(self, method, url, **kw):
                async def _go():
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                                 base_url="http://testserver") as ac:
                        return await ac.request(method, url, **kw)
                return asyncio.run(_go())

            def get(self, url, **kw):
                return self.request("GET", url, **kw)

            def post(self, url, **kw):
                return self.request("POST", url, **kw)

        yield SyncClient()
    finally:
        app.dependency_overrides.pop(get_db, None)


def _login(client, username, password="S3cure-Horse-99!", totp_code=None):
    body = {"username": username, "password": password}
    if totp_code is not None:
        body["totp_code"] = totp_code
    r = client.post("/api/auth/login", json=body)
    assert r.status_code == 200, r.text
    return r.headers.get("set-cookie", "").split(";")[0]


# --------------------------------------------------------------------- C-1

class TestComposeMigrateDatabaseUrl:
    def test_migrate_service_database_url_uses_env_interpolation(self):
        lines = (ROOT / "docker-compose.prod.yml").read_text().splitlines()
        # The migrate service's DATABASE_URL and the app service's
        # (known-good) one must be identical, and the value must be an
        # interpolation with a hard failure when unset — never a literal.
        # Located structurally: hardcoded line numbers go stale whenever
        # a service block gains lines (m-15's logging block moved the
        # migrate line from 73 to 80).
        db_lines = [l for l in lines if l.strip().startswith("DATABASE_URL:")]
        assert len(db_lines) == 2, f"expected migrate + app DATABASE_URL lines, got {db_lines!r}"
        migrate_line, app_line = db_lines
        assert migrate_line == app_line
        assert "${DATABASE_URL:?" in migrate_line
        assert "<redacted>" not in migrate_line

    def test_no_database_url_line_holds_a_literal_value(self):
        # Every DATABASE_URL / MIGRATION_DATABASE_URL assignment in the
        # compose file must be an env interpolation (a literal here is
        # either a dead deploy, as C-1 was, or a committed secret).
        for line in (ROOT / "docker-compose.prod.yml").read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith(("DATABASE_URL:", "MIGRATION_DATABASE_URL:")):
                assert "${" in stripped, f"literal database URL line: {stripped!r}"
                assert "://" not in stripped, f"inline connection string: {stripped!r}"


# --------------------------------------------------------------------- C-2

class TestCustomersSatisfactionScore:
    def test_submit_customers_with_satisfaction_score(self, client, http_db):
        cookie = _login(client, "ceo_bu1")
        r = client.post("/api/ops/customers",
                        json={"business_unit_id": 1, "report_date": "2026-11-17",
                              "footfall": 120, "transactions": 40, "complaints": 1,
                              "satisfaction_score": 4.5, "notes": "good day"},
                        headers={"cookie": cookie})
        assert r.status_code == 200, r.text
        from src.models import CustomerMetric
        row = http_db.query(CustomerMetric).one()
        assert float(row.satisfaction_score) == 4.5

    def test_submit_customers_without_score_still_works(self, client, http_db):
        cookie = _login(client, "ceo_bu1")
        r = client.post("/api/ops/customers",
                        json={"business_unit_id": 1, "report_date": "2026-11-17",
                              "footfall": 10, "transactions": 2, "complaints": 0,
                              "satisfaction_score": None},
                        headers={"cookie": cookie})
        assert r.status_code == 200, r.text


# --------------------------------------------------------------------- M-01

class TestTotpEndToEnd:
    def test_chairman_enrollment_flow_then_login(self, client, http_db):
        # Start unenrolled: clear the fixture chairman's TOTP.
        chairman = http_db.query(User).filter(User.username == "chairman").one()
        chairman.totp_secret = None
        chairman.totp_enabled = False
        http_db.commit()

        # Password alone earns no session — only a setup token.
        r = client.post("/api/auth/login",
                        json={"username": "chairman", "password": "S3cure-Horse-99!"})
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == "TOTP_ENROLLMENT_REQUIRED"
        setup_token = r.json()["totp_setup_token"]
        assert "access_token=" not in r.headers.get("set-cookie", "")

        # A bogus token enrols nobody.
        r = client.post("/api/auth/totp/enroll", json={"setup_token": "not-a-token"})
        assert r.status_code == 401

        # Enroll with the setup token.
        r = client.post("/api/auth/totp/enroll", json={"setup_token": setup_token})
        assert r.status_code == 200, r.text
        secret = r.json()["secret"]
        assert r.json()["otpauth_url"].startswith("otpauth://totp/")

        # A wrong code does not enable TOTP.
        r = client.post("/api/auth/totp/confirm",
                        json={"setup_token": setup_token, "code": "000000"})
        assert r.status_code == 400
        http_db.expire_all()
        assert http_db.query(User).filter(User.username == "chairman").one().totp_enabled is False

        # The right code enables it.
        r = client.post("/api/auth/totp/confirm",
                        json={"setup_token": setup_token, "code": pyotp.TOTP(secret).now()})
        assert r.status_code == 200, r.text

        # Password alone is now refused; a wrong code is refused; the
        # right code signs in.
        r = client.post("/api/auth/login",
                        json={"username": "chairman", "password": "S3cure-Horse-99!"})
        assert r.status_code == 401
        assert r.json()["detail"] == "TOTP_REQUIRED"
        r = client.post("/api/auth/login",
                        json={"username": "chairman", "password": "S3cure-Horse-99!",
                              "totp_code": "000000"})
        assert r.status_code == 401
        cookie = _login(client, "chairman", totp_code=pyotp.TOTP(secret).now())
        r = client.get("/api/auth/me", headers={"cookie": cookie})
        assert r.status_code == 200
        assert r.json()["user"]["totp_enabled"] is True

    def test_non_chairman_login_without_totp_still_works(self, client):
        cookie = _login(client, "ceo_bu1")
        r = client.get("/api/auth/me", headers={"cookie": cookie})
        assert r.status_code == 200
        assert r.json()["user"]["totp_enabled"] is False

    def test_totp_secret_is_encrypted_at_rest(self, db, monkeypatch):
        from cryptography.fernet import Fernet, MultiFernet

        import src.crypto as crypto
        from src.routers.auth import totp_enroll

        key = Fernet.generate_key()
        monkeypatch.setattr(crypto, "_FERNET", MultiFernet([Fernet(key)]))
        monkeypatch.setattr(crypto, "_PRIMARY", Fernet(key))
        chairman = _add_user(db, "chairman", "Chairman")
        out = totp_enroll(TotpEnrollRequest(setup_token=None), make_request(),
                          user=chairman, db=db)
        assert out["secret"]
        db.expire_all()
        raw = db.execute(
            text("SELECT totp_secret FROM users WHERE username='chairman'")
        ).scalar_one()
        assert raw.startswith("enc:v1:")
        assert out["secret"] not in raw
        # ...and the stored value still verifies through the model layer.
        db.expire_all()
        stored = db.query(User).filter(User.username == "chairman").one()
        assert stored.totp_secret == out["secret"]

    def test_admin_disable_and_cli_reset_force_reenrollment(self, client, http_db):
        chairman_cookie = _login(client, "chairman", totp_code=_totp_code())
        ceo = http_db.query(User).filter(User.username == "ceo_bu1").one()

        # Enroll the CEO first (optional enrollment over a live session).
        r = client.post("/api/auth/totp/enroll", json={},
                        headers={"cookie": _login(client, "ceo_bu1")})
        assert r.status_code == 200, r.text
        ceo_secret = r.json()["secret"]
        r = client.post("/api/auth/totp/confirm",
                        json={"code": pyotp.TOTP(ceo_secret).now()},
                        headers={"cookie": _login(client, "ceo_bu1")})
        assert r.status_code == 200, r.text

        # The Chairman can clear another account's enrollment.
        r = client.post(f"/api/admin/users/{ceo.id}/totp-disable", json={},
                        headers={"cookie": chairman_cookie})
        assert r.status_code == 200, r.text
        assert r.json()["was_enabled"] is True
        http_db.expire_all()
        assert http_db.query(User).filter(User.username == "ceo_bu1").one().totp_enabled is False

        # The CLI path clears the Chairman's own enrollment; his next
        # sign-in then forces enrollment again.
        from src.scripts.reset_totp import reset_user_totp
        assert reset_user_totp(http_db, "chairman") == "chairman"
        r = client.post("/api/auth/login",
                        json={"username": "chairman", "password": "S3cure-Horse-99!"})
        assert r.status_code == 403
        assert r.json()["detail"] == "TOTP_ENROLLMENT_REQUIRED"
        with pytest.raises(RuntimeError):
            reset_user_totp(http_db, "no-such-user")

    def test_totp_disable_self_requires_a_valid_code(self, client, http_db):
        cookie = _login(client, "chairman", totp_code=_totp_code())
        r = client.post("/api/auth/totp/disable", json={"code": "000000"},
                        headers={"cookie": cookie})
        assert r.status_code == 400
        r = client.post("/api/auth/totp/disable", json={"code": _totp_code()},
                        headers={"cookie": cookie})
        assert r.status_code == 200, r.text
        http_db.expire_all()
        assert http_db.query(User).filter(User.username == "chairman").one().totp_enabled is False


# --------------------------------------------------------------------- m-27

class TestPasswordPolicy:
    def test_rejects_weak_passwords(self):
        from src.schemas import password_policy_error as policy

        # Denylist: digits-only, and common passwords hidden in a longer
        # string (the old prefix-only check let "xPassword123" through).
        assert policy("123456789012") is not None
        assert policy("xPassword123") is not None
        assert policy("MyDragon!2026") is not None
        assert policy("letmein-2026!!") is not None
        # Single character class, however long.
        assert policy("abcdefghijklm") is not None
        assert policy("12345678901234") is not None

    def test_accepts_strong_passwords_and_passphrases(self):
        from src.schemas import password_policy_error as policy

        assert policy("Horse-Stable-99") is None
        assert policy("S3cure-Horse-99!") is None
        assert policy("correct horse battery", username="ceo_bu5") is None

    def test_password_reuse_is_blocked_by_history(self, db):
        from src.routers.auth import force_password_change

        user = _add_user(db, "alice", "CEO", password="S3cure-Horse-99!")

        def _change(current, new):
            return force_password_change(
                ForcePasswordChangeRequest(current_password=current, new_password=new,
                                           confirm_password=new),
                make_request(), Response(), user=user, db=db)

        assert _change("S3cure-Horse-99!", "New-Horse-88!x")["ok"] is True
        # Rotating straight back to the previous password is a reuse.
        with pytest.raises(HTTPException) as exc:
            _change("New-Horse-88!x", "S3cure-Horse-99!")
        assert exc.value.status_code == 400
        assert "used recently" in exc.value.detail

        # History stays bounded at the last 5 hashes.
        passwords = ["New-Horse-88!x", "Third-Horse-77!x", "Fourth-Horse-66!x",
                     "Fifth-Horse-55!x", "Sixth-Horse-44!x"]
        current = "New-Horse-88!x"
        for new in passwords[1:]:
            assert _change(current, new)["ok"] is True
            current = new
        assert db.query(PasswordHistory).filter(
            PasswordHistory.user_id == user.id).count() == 5


# --------------------------------------------------------------------- m-28

class TestLoginLockoutChairmanBypass:
    def _add_rows(self, db, action, n, username, ip, age_seconds=0):
        now = utcnow()
        for _ in range(n):
            db.add(AuditLog(action=action, username=username, ip_address=ip,
                            timestamp=now - timedelta(seconds=age_seconds)))
        db.commit()

    def test_chairman_gets_no_trusted_ip_bypass(self, db):
        from src.throttle import check_login_throttle

        _add_user(db, "chairman", "Chairman")
        self._add_rows(db, "LOGIN_FAILED", 5, "chairman", PUBLIC_B)
        self._add_rows(db, "LOGIN_SUCCESS", 1, "chairman", PUBLIC_A, age_seconds=3600)
        # A recent success from this IP would exempt any other account —
        # the Chairman must still be locked out.
        assert check_login_throttle(db, "chairman", PUBLIC_A) is not None

    def test_other_accounts_keep_the_trusted_ip_bypass(self, db):
        from src.throttle import check_login_throttle

        _add_user(db, "ceo_bu5", "CEO", business_unit_id=1)
        self._add_rows(db, "LOGIN_FAILED", 5, "ceo_bu5", PUBLIC_B)
        self._add_rows(db, "LOGIN_SUCCESS", 1, "ceo_bu5", PUBLIC_A, age_seconds=3600)
        assert check_login_throttle(db, "ceo_bu5", PUBLIC_A) is None

    def test_client_ip_self_check_reports_and_enforces(self, monkeypatch):
        from src.config import settings
        from src.throttle import check_client_ip_configuration, enforce_client_ip_configuration

        assert check_client_ip_configuration() is True
        monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 2)
        assert check_client_ip_configuration() is False
        # Outside production the failure is advisory only.
        enforce_client_ip_configuration()
        # In production it fails startup.
        monkeypatch.setattr(settings, "IS_PRODUCTION", True)
        with pytest.raises(RuntimeError, match="Client-IP self-check failed"):
            enforce_client_ip_configuration()
        monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 1)
        enforce_client_ip_configuration()

    def test_edge_middleware_refuses_to_start_in_production_when_misconfigured(self, monkeypatch):
        from src.config import settings
        from src.edge import EdgeMiddleware

        monkeypatch.setattr(settings, "IS_PRODUCTION", True)
        monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 2)
        with pytest.raises(RuntimeError, match="Client-IP self-check failed"):
            EdgeMiddleware(lambda scope, receive, send: None)


# --------------------------------------------------------------------- m-29

class TestResetChairmanScript:
    def test_deactivated_chairman_stays_deactivated_by_default(self, db):
        from src.scripts.reset_chairman import reset_chairman_password

        chairman = _add_user(db, "chairman", "Chairman", is_active=False)
        _username, temp = reset_chairman_password(db)
        db.expire_all()
        chairman = db.query(User).filter(User.username == "chairman").one()
        assert chairman.is_active is False
        assert verify_password(temp, chairman.hashed_password)

    def test_reactivate_flag_restores_the_account(self, db):
        from src.scripts.reset_chairman import reset_chairman_password

        _add_user(db, "chairman", "Chairman", is_active=False)
        reset_chairman_password(db, reactivate=True)
        db.expire_all()
        assert db.query(User).filter(User.username == "chairman").one().is_active is True

    def test_main_writes_0600_file_and_never_prints_the_password(self, db, tmp_path, monkeypatch, capsys):
        import src.scripts.reset_chairman as reset_mod
        from src.config import settings

        chairman = _add_user(db, "chairman", "Chairman")
        monkeypatch.setattr(reset_mod, "SessionLocal", lambda: db)
        monkeypatch.setattr(settings, "LOCAL_UPLOAD_DIR", str(tmp_path / "uploads"))
        out_path = tmp_path / "recovery.txt"
        assert reset_mod.main(["--output", str(out_path)]) == 0
        assert out_path.is_file()
        assert stat.S_IMODE(out_path.stat().st_mode) == 0o600
        captured = capsys.readouterr()
        temp = out_path.read_text()
        assert temp not in captured.out
        assert str(out_path) in captured.out
        db.expire_all()
        chairman = db.query(User).filter(User.username == "chairman").one()
        assert verify_password(temp, chairman.hashed_password)


# --------------------------------------------------------------------- m-30

class TestUnlockLoginRestrictions:
    def test_ho_cannot_unlock_chairman_or_self_but_can_unlock_a_ceo(self, client, http_db):
        ho = http_db.query(User).filter(User.username == "ho_staff").one()
        chairman = http_db.query(User).filter(User.username == "chairman").one()
        ceo = http_db.query(User).filter(User.username == "ceo_bu1").one()
        cookie = _login(client, "ho_staff")
        r = client.post(f"/api/admin/users/{chairman.id}/unlock-login", json={},
                        headers={"cookie": cookie})
        assert r.status_code == 403, r.text
        r = client.post(f"/api/admin/users/{ho.id}/unlock-login", json={},
                        headers={"cookie": cookie})
        assert r.status_code == 403, r.text
        r = client.post(f"/api/admin/users/{ceo.id}/unlock-login", json={},
                        headers={"cookie": cookie})
        assert r.status_code == 200, r.text
        assert r.json()["was_locked"] is False

    def test_chairman_cannot_unlock_self(self, client, http_db):
        chairman = http_db.query(User).filter(User.username == "chairman").one()
        cookie = _login(client, "chairman", totp_code=_totp_code())
        r = client.post(f"/api/admin/users/{chairman.id}/unlock-login", json={},
                        headers={"cookie": cookie})
        assert r.status_code == 403, r.text


# --------------------------------------------------------------------- m-17

class TestBootstrapPasswordFile:
    def test_bootstrap_file_is_written_next_to_uploads_not_inside(self, db, tmp_path, monkeypatch):
        from unittest import mock

        from src.config import settings
        from src.main import seed_users

        monkeypatch.setattr(settings, "LOCAL_UPLOAD_DIR", str(tmp_path / "uploads"))
        monkeypatch.setattr(settings, "BOOTSTRAP_CHAIRMAN_PASSWORD", "")
        with mock.patch.object(settings, "ENVIRONMENT", "production"):
            seed_users(db)
        assert (tmp_path / ".bootstrap_chairman_password").is_file()
        assert not (tmp_path / "uploads" / ".bootstrap_chairman_password").exists()

    def test_uploads_route_rejects_dotfiles(self, client, tmp_path, monkeypatch):
        from src.config import settings

        upload_dir = tmp_path / "uploads"
        upload_dir.mkdir()
        (upload_dir / ".hidden").write_text("secret")
        (upload_dir / "a.txt").write_text("hello")
        monkeypatch.setattr(settings, "LOCAL_UPLOAD_DIR", str(upload_dir))
        cookie = _login(client, "ceo_bu1")
        r = client.get("/uploads/.hidden", headers={"cookie": cookie})
        assert r.status_code == 404
        r = client.get("/uploads/a.txt", headers={"cookie": cookie})
        assert r.status_code == 200
        assert r.text == "hello"

    def test_bootstrap_file_deleted_after_first_chairman_login(self, client, http_db, tmp_path, monkeypatch):
        from src.config import settings

        monkeypatch.setattr(settings, "LOCAL_UPLOAD_DIR", str(tmp_path / "uploads"))
        bootstrap = tmp_path / ".bootstrap_chairman_password"
        bootstrap.write_text("temporary-bootstrap-password")
        assert bootstrap.is_file()
        _login(client, "chairman", totp_code=_totp_code())
        assert not bootstrap.exists()


# --------------------------------------------------------------------- m-32

class TestMacroAttachmentsRejected:
    def test_allowlist_has_no_macro_capable_extensions(self):
        from src.services.daily_report import ALLOWED_ATTACHMENT_EXTENSIONS as ceo_exts
        from src.services.daily_report import ALLOWED_ATTACHMENT_EXTENSIONS as ho_exts

        for exts in (ceo_exts, ho_exts):
            assert "doc" not in exts
            assert "xls" not in exts
            assert "docx" in exts
            assert "xlsx" in exts

    def test_ho_submit_rejects_doc_and_xls(self, db):
        import io as _io

        from src.report_forms import build_ho_report_form
        from src.routers.ho import submit_report
        from src.upload_validation import _OLE_MAGIC

        ho_user = _add_user(db, "ho_user", "HO")

        def _submit(attachment):
            payload = {
                "revenue": "100000", "cash_flow": "20000", "receivable": "5000",
                "payable": "3000", "other_amount": "1000", "financial_other_notes": "",
                "meetings_attended": "Standup", "site_visits": "", "key_decisions": "",
                "next_day_schedule": "", "gym_percent": "50", "swimming_percent": "",
                "cycle_percent": "", "activity_other_notes": "", "daily_steps": "8000",
                "heart_rate": "72", "sleep_hours": "7.5",
                "bp_systolic": "120", "bp_diastolic": "80", "health_other_notes": "",
                "event_flag": "true", "meeting_flag": "false", "golf_flag": "false",
                "social_other_notes": "", "attachment": attachment,
            }
            import src.routers.ho as ho_router
            ho_router._rate_limiter.reset()
            request = make_request()
            form = build_ho_report_form(request=request, user=ho_user, db=db,
                                        rate_limiter=ho_router._rate_limiter, **payload)
            return submit_report(request, form=form, user=ho_user, db=db)

        ole = _OLE_MAGIC + b"\x00" * 10
        for filename in ("report.doc", "sheet.xls"):
            with pytest.raises(HTTPException) as exc:
                _submit(UploadFile(filename=filename, file=_io.BytesIO(ole)))
            assert exc.value.status_code == 400
            assert "Unsupported attachment type" in exc.value.detail
