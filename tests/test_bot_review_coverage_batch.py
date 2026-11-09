"""BOT review workstream D — coverage batch (report section (f) step 9).

Smoke coverage for areas the review found untested: src/crypto.py,
src/r2_storage.py, src/upload_validation.py, src/routers/auth.py,
src/routers/ho.py (including the HO daily-financial auto-post),
src/jobs/scheduled_alerts.py. Tests are fast and SQLite-safe; nothing
here needs a live service (R2 is faked, the alert job runs against a
test database).

Run with: ENVIRONMENT=test DATABASE_URL=sqlite:// \
    .venv/bin/python -m pytest tests/test_bot_review_coverage_batch.py
"""
import io
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError
from cryptography.fernet import Fernet, MultiFernet
from fastapi import HTTPException, Response
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

import src.crypto as crypto
import src.jobs.scheduled_alerts as scheduled_alerts
import src.r2_storage as r2_storage
import src.routers.ho as ho_router
from src import r2_storage as r2_mod
from src.accounting_templates import template_accounts
from src.auth import hash_password, verify_password
from src.config import settings
from src.models import (
    Account, AuditLog, Base, BusinessUnit, DailyFinancial,
    HeadOfficeActivityLog, HeadOfficeDailyFinancial, HeadOfficeHealthStatus,
    HeadOfficeSocialLog, InventoryItem, JournalEntry, LicenseDocument,
    Notification, PasswordResetRequest, StockBatch, User,
)
from src.routers.auth import (
    forgot_password, force_password_change, login, logout,
)
from src.routers.ho import ho_dashboard, set_monthly_target, submit_report
from src.schemas import (
    ForgotPasswordRequest, ForcePasswordChangeRequest, LoginRequest,
    MonthlyTargetRequest,
)
from src.upload_validation import content_matches_extension
from src.utils import today_local

PASSWORD = "S3cure-Horse-99!"
NEW_PASSWORD = "N3w-Horse-77!"


def make_request(peer="8.8.8.8", headers=None):
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "headers": raw, "client": (peer, 5555) if peer else None})


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.test_engine = engine
    yield session
    session.close()
    engine.dispose()


def _add_user(db, username, role, password=PASSWORD, business_unit_id=None,
              is_active=True):
    user = User(username=username, hashed_password=hash_password(password),
                role=role, business_unit_id=business_unit_id, is_active=is_active)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _provision_ho_bu(db):
    bu = BusinessUnit(name="Head Office", code="HO")
    db.add(bu)
    db.commit()
    id_by_code = {}
    for code, name, acc_type, parent_code, is_header in template_accounts("retail"):
        acc = Account(business_unit_id=bu.id, code=code, name=name,
                      account_type=acc_type, is_header=is_header, is_system=True,
                      parent_id=id_by_code.get(parent_code) if parent_code else None)
        db.add(acc)
        db.flush()
        id_by_code[code] = acc.id
    db.commit()
    return bu


# ------------------------------------------------------------------ crypto

@pytest.fixture()
def fernet(monkeypatch):
    key_new = Fernet.generate_key()
    key_old = Fernet.generate_key()
    monkeypatch.setattr(crypto, "_FERNET",
                        MultiFernet([Fernet(key_new), Fernet(key_old)]))
    monkeypatch.setattr(crypto, "_PRIMARY", Fernet(key_new))
    return key_new, key_old


def test_crypto_round_trip(fernet):
    assert crypto.encryption_enabled()
    token = crypto.encrypt_text("09-123-456")
    assert token.startswith("enc:v1:")
    assert crypto.is_encrypted(token)
    assert crypto.decrypt_text(token) == "09-123-456"


def test_crypto_tokens_are_salted(fernet):
    assert crypto.encrypt_text("same") != crypto.encrypt_text("same")


def test_crypto_tampered_ciphertext_rejected(fernet):
    token = crypto.encrypt_text("secret")
    tampered = token[:-2] + ("AA" if token[-2:] != "AA" else "BB")
    assert tampered != token
    with pytest.raises(RuntimeError, match="Could not decrypt"):
        crypto.decrypt_text(tampered)
    with pytest.raises(RuntimeError, match="Could not decrypt"):
        crypto.decrypt_text("enc:v1:not-a-fernet-token")


def test_crypto_legacy_plaintext_passes_through(fernet):
    assert crypto.decrypt_text("plain old value") == "plain old value"
    assert not crypto.is_encrypted("plain old value")
    assert not crypto.is_encrypted(None)


def test_crypto_no_key_passes_through_and_refuses_ciphertext(monkeypatch):
    monkeypatch.setattr(crypto, "_FERNET", None)
    monkeypatch.setattr(crypto, "_PRIMARY", None)
    assert not crypto.encryption_enabled()
    assert crypto.encrypt_text("plain") == "plain"
    with pytest.raises(RuntimeError, match="DATA_ENCRYPTION_KEY is not configured"):
        crypto.decrypt_text("enc:v1:AAAA")


def test_crypto_rotation_rekeys_under_primary(fernet):
    key_new, key_old = fernet
    old_token = "enc:v1:" + MultiFernet([Fernet(key_old)]).encrypt(b"rotate me").decode()
    assert not crypto.is_under_primary_key(old_token)
    rotated = crypto.rotate_text(old_token)
    assert rotated != old_token
    assert crypto.is_under_primary_key(rotated)
    assert crypto.decrypt_text(rotated) == "rotate me"
    # Already under the primary key, and plaintext, are left alone.
    assert crypto.rotate_text(rotated) == rotated
    assert crypto.rotate_text("plain") == "plain"


def test_encrypted_text_type_round_trip(fernet):
    t = crypto.EncryptedText()
    assert t.process_bind_param(None, None) is None
    assert t.process_result_value(None, None) is None
    stored = t.process_bind_param("my note", None)
    assert stored.startswith("enc:v1:")
    assert t.process_result_value(stored, None) == "my note"


def test_encrypted_date_type_round_trip_and_bad_legacy_value(fernet):
    t = crypto.EncryptedDate()
    stored = t.process_bind_param(date(2001, 11, 23), None)
    assert t.process_result_value(stored, None) == date(2001, 11, 23)
    assert t.process_result_value("2001-11-23", None) == date(2001, 11, 23)
    # m11: a legacy non-ISO value raises the decrypt-style RuntimeError,
    # not an unhandled ValueError that would 500 every read of the row.
    with pytest.raises(RuntimeError, match="not a valid ISO-8601 date"):
        t.process_result_value("23rd Nov 2001", None)


# ------------------------------------------------------------------ upload validation

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 10
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 10
GIF89 = b"GIF89a" + b"\x00" * 10
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 10
PDF = b"%PDF-1.7\n" + b"\x00" * 10
ZIP = b"PK\x03\x04" + b"\x00" * 10
OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 10


@pytest.mark.parametrize("ext,data", [
    ("png", PNG), ("jpg", JPEG), ("jpeg", JPEG), ("gif", GIF89),
    ("webp", WEBP), ("pdf", PDF), ("docx", ZIP), ("xlsx", ZIP),
    ("doc", OLE), ("xls", OLE),
    ("csv", b"a,b,c\n1,2,3\n"), ("txt", b"hello world\n"),
    ("txt", "myanmar စာသား\n".encode("utf-8")),
    ("csv", b"\xef\xbb\xbfheader\nvalue\n"),  # UTF-8 BOM
])
def test_upload_validation_accepts(ext, data):
    assert content_matches_extension(ext, io.BytesIO(data))


@pytest.mark.parametrize("ext,data", [
    ("png", JPEG), ("jpg", PNG), ("pdf", b"<html><body>hi</body></html>"),
    ("pdf", b"%PDF"),  # truncated magic
    ("png", b""), ("docx", OLE), ("doc", ZIP),
    ("exe", b"MZ" + b"\x00" * 10), ("txt", b"has\x00nul"),
    ("txt", b"\xff\xfe invalid utf-8"),
    ("csv", b"\xff\xfe invalid utf-8"),
    ("webp", b"RIFF\x00\x00\x00\x00JPEG"),
])
def test_upload_validation_rejects(ext, data):
    assert not content_matches_extension(ext, io.BytesIO(data))


def test_upload_validation_rewinds_file():
    f = io.BytesIO(PNG)
    f.read(4)
    assert content_matches_extension("png", f)
    assert f.tell() == 0


# ------------------------------------------------------------------ r2 storage

class _FakeS3:
    def __init__(self, fail=False):
        self.fail = fail
        self.uploads = []
        self.presigned = "https://presigned.example/object"

    def upload_fileobj(self, fileobj, bucket, key, ExtraArgs=None):
        if self.fail:
            raise ClientError({"Error": {"Code": "NoSuchBucket", "Message": "boom"}},
                              "UploadPart")
        self.uploads.append({"bucket": bucket, "key": key, "extra": ExtraArgs,
                             "body": fileobj.read()})

    def generate_presigned_url(self, op, Params=None, ExpiresIn=None):
        return self.presigned


def test_r2_not_configured_by_default():
    assert not r2_storage.is_configured()


def test_r2_local_fallback_writes_file(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "LOCAL_UPLOAD_DIR", str(tmp_path))
    url = r2_storage.upload_fileobj(io.BytesIO(b"hello"), "photo.png")
    assert url is not None and url.startswith("/uploads/activity-attachments/")
    assert url.endswith(".png")
    saved = tmp_path / url[len("/uploads/"):]
    assert saved.read_bytes() == b"hello"


def test_r2_returns_none_in_production_without_r2(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    assert r2_storage.upload_fileobj(io.BytesIO(b"hello"), "photo.png") is None


def test_r2_configured_client_sets_safe_content_type(monkeypatch):
    fake = _FakeS3()
    monkeypatch.setattr(r2_mod, "_get_client", lambda: fake)
    monkeypatch.setattr(settings, "R2_BUCKET_NAME", "test-bucket")
    monkeypatch.setattr(settings, "R2_PUBLIC_BASE_URL", "https://cdn.example.com")

    url = r2_storage.upload_fileobj(io.BytesIO(b"png-bytes"), "pic.png", folder="x")
    assert url == "https://cdn.example.com/x/" + fake.uploads[0]["key"].split("/", 1)[1]
    assert fake.uploads[0]["extra"] == {
        "ContentType": "image/png", "ContentDisposition": "inline"}

    url2 = r2_storage.upload_fileobj(io.BytesIO(b"sheet"), "report.xlsx", folder="x")
    assert url2.startswith("https://cdn.example.com/x/")
    assert fake.uploads[1]["extra"] == {
        "ContentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "ContentDisposition": "attachment"}
    # An unlisted extension never renders inline in a browser.
    r2_storage.upload_fileobj(io.BytesIO(b"bin"), "data.bin", folder="x")
    assert fake.uploads[2]["extra"] == {
        "ContentType": "application/octet-stream", "ContentDisposition": "attachment"}


def test_r2_presigned_url_when_no_public_base(monkeypatch):
    fake = _FakeS3()
    monkeypatch.setattr(r2_mod, "_get_client", lambda: fake)
    monkeypatch.setattr(settings, "R2_BUCKET_NAME", "test-bucket")
    monkeypatch.setattr(settings, "R2_PUBLIC_BASE_URL", "")
    assert r2_storage.upload_fileobj(io.BytesIO(b"png-bytes"), "pic.png") == fake.presigned


def test_r2_client_error_returns_none(monkeypatch):
    monkeypatch.setattr(r2_mod, "_get_client", lambda: _FakeS3(fail=True))
    assert r2_storage.upload_fileobj(io.BytesIO(b"png-bytes"), "pic.png") is None


# ------------------------------------------------------------------ auth router

@pytest.fixture()
def auth_db(db):
    db.test_alice = _add_user(db, "alice", "HO")
    db.test_chairman = _add_user(db, "chairman", "Chairman")
    db.test_inactive = _add_user(db, "mallory", "HO", is_active=False)
    return db


def test_login_success_sets_cookie(auth_db):
    db = auth_db
    response = Response()
    out = login(LoginRequest(username="alice", password=PASSWORD),
                make_request(), response, db=db)
    assert out["user"]["username"] == "alice"
    assert out["redirect_to"]
    assert "set-cookie" in response.headers
    assert "no-store" in response.headers.get("cache-control", "")
    assert db.test_alice.last_login_at is not None
    assert db.query(AuditLog).filter(AuditLog.action == "LOGIN_SUCCESS").count() == 1


def test_login_wrong_password_401_and_audit(auth_db):
    db = auth_db
    with pytest.raises(HTTPException) as exc:
        login(LoginRequest(username="alice", password="Wrong-Horse-88!"),
              make_request(), Response(), db=db)
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid username or password"
    row = db.query(AuditLog).filter(AuditLog.action == "LOGIN_FAILED").one()
    assert row.username == "alice"


def test_login_unknown_user_401_same_detail(auth_db):
    db = auth_db
    with pytest.raises(HTTPException) as exc:
        login(LoginRequest(username="nobody", password="Whatever-123!"),
              make_request(), Response(), db=db)
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid username or password"


def test_login_deactivated_user_401(auth_db):
    db = auth_db
    with pytest.raises(HTTPException) as exc:
        login(LoginRequest(username="mallory", password=PASSWORD),
              make_request(), Response(), db=db)
    assert exc.value.status_code == 401


def test_login_over_72_byte_password_rejected(auth_db):
    # m12: bcrypt silently truncates past byte 72, so a 73-byte password
    # whose first 72 bytes match must NOT authenticate.
    db = auth_db
    long_pw = "K7m!pQ2$xZ9w" * 6  # exactly 72 bytes
    assert len(long_pw.encode()) == 72
    user = _add_user(db, "longpw", "HO", password=long_pw)
    assert verify_password(long_pw, user.hashed_password)
    with pytest.raises(HTTPException) as exc:
        login(LoginRequest(username="longpw", password=long_pw + "!"),
              make_request(), Response(), db=db)
    assert exc.value.status_code == 401


def test_login_lockout_after_repeated_failures(auth_db):
    db = auth_db
    for _ in range(5):
        with pytest.raises(HTTPException) as exc:
            login(LoginRequest(username="alice", password="Wrong-Horse-88!"),
                  make_request(), Response(), db=db)
        assert exc.value.status_code == 401
    with pytest.raises(HTTPException) as exc:
        login(LoginRequest(username="alice", password="Wrong-Horse-88!"),
              make_request(), Response(), db=db)
    assert exc.value.status_code == 429
    assert exc.value.headers.get("Retry-After")


def test_forgot_password_same_response_everywhere(auth_db):
    db = auth_db
    expected = {
        "ok": True,
        "message": ("If this account exists, a reset request has been sent to the Chairman. "
                    "You will receive a temporary password once it is approved."),
    }
    for username in ("nobody", "chairman", "mallory", "alice"):
        out = forgot_password(ForgotPasswordRequest(username=username),
                              make_request(), db=db)
        assert out == expected
    # Only the real, active, non-Chairman account got a Pending request —
    # and only one, even though it was asked for twice.
    rows = db.query(PasswordResetRequest).all()
    assert len(rows) == 1
    assert rows[0].username == "alice" and rows[0].status == "Pending"
    out = forgot_password(ForgotPasswordRequest(username="alice"),
                          make_request(), db=db)
    assert out == expected
    assert db.query(PasswordResetRequest).count() == 1


def test_force_password_change_success(auth_db):
    db = auth_db
    user = db.test_alice
    before = user.token_version
    out = force_password_change(
        ForcePasswordChangeRequest(current_password=PASSWORD,
                                   new_password=NEW_PASSWORD,
                                   confirm_password=NEW_PASSWORD),
        make_request(), Response(), user=user, db=db)
    assert out["ok"] is True
    assert out["redirect_to"]
    assert verify_password(NEW_PASSWORD, user.hashed_password)
    assert not verify_password(PASSWORD, user.hashed_password)
    assert user.token_version == before + 1
    assert user.require_password_change is False


@pytest.mark.parametrize("current,new,confirm,detail", [
    ("Wrong-Horse-88!", NEW_PASSWORD, NEW_PASSWORD, "Current password is incorrect"),
    (PASSWORD, NEW_PASSWORD, "Different-99!", "Passwords do not match"),
    (PASSWORD, "short", "short", "at least 12 characters"),
    (PASSWORD, PASSWORD, PASSWORD, "must be different"),
])
def test_force_password_change_rejections(auth_db, current, new, confirm, detail):
    db = auth_db
    with pytest.raises(HTTPException) as exc:
        force_password_change(
            ForcePasswordChangeRequest(current_password=current, new_password=new,
                                       confirm_password=confirm),
            make_request(), Response(), user=db.test_alice, db=db)
    assert exc.value.status_code == 400
    assert detail in exc.value.detail


def test_logout_bumps_token_version(auth_db):
    db = auth_db
    user = db.test_alice
    before = user.token_version
    out = logout(make_request(), Response(), user=user, db=db)
    assert out == {"ok": True}
    assert user.token_version == before + 1
    # Anonymous logout is still a plain ok.
    assert logout(make_request(), Response(), user=None, db=db) == {"ok": True}


# ------------------------------------------------------------------ ho router

@pytest.fixture()
def ho_db(db):
    db.test_ho = _add_user(db, "ho_user", "HO")
    ho_router._report_submit_timestamps.clear()
    return db


def _submit(db, ho_user, **overrides):
    # Every form field must be passed explicitly: called directly (not
    # through FastAPI), an omitted field keeps its Form(...) default object
    # instead of "".
    payload = {
        "revenue": "100000", "cash_flow": "20000", "receivable": "5000",
        "payable": "3000", "other_amount": "1000", "financial_other_notes": "",
        "meetings_attended": "Standup", "site_visits": "", "key_decisions": "",
        "next_day_schedule": "", "gym_percent": "50", "swimming_percent": "",
        "cycle_percent": "", "activity_other_notes": "", "daily_steps": "8000",
        "heart_rate": "72", "sleep_hours": "7.5",
        "bp_systolic": "120", "bp_diastolic": "80", "health_other_notes": "",
        "event_flag": "true", "meeting_flag": "false", "golf_flag": "false",
        "social_other_notes": "", "attachment": None,
    }
    payload.update(overrides)
    return submit_report(make_request(), user=ho_user, db=db, **payload)


def test_ho_dashboard_empty(ho_db):
    out = ho_dashboard(user=ho_db.test_ho, db=ho_db)
    assert out["financial"] is None
    assert out["already_submitted_today"] is False
    assert out["monthly_target"] is None
    assert out["financial_rows"] == []


def test_ho_monthly_target_set_and_locked(ho_db):
    db, ho_user = ho_db, ho_db.test_ho
    out = set_monthly_target(
        MonthlyTargetRequest(target_month="2026-11-01", monthly_revenue_target=1000000,
                             monthly_cash_flow_target=0, monthly_max_receivable=0,
                             monthly_max_payable=0),
        make_request(), user=ho_user, db=db)
    assert out == {"ok": True, "target_month": "2026-11-01"}
    with pytest.raises(HTTPException) as exc:
        set_monthly_target(
            MonthlyTargetRequest(target_month="2026-11-01", monthly_revenue_target=2000000,
                                 monthly_cash_flow_target=0, monthly_max_receivable=0,
                                 monthly_max_payable=0),
            make_request(), user=ho_user, db=db)
    assert exc.value.status_code == 403
    assert "already been set" in exc.value.detail


def test_ho_submit_report_saves_all_sections(ho_db):
    db, ho_user = ho_db, ho_db.test_ho
    out = _submit(db, ho_user)
    assert out == {"ok": True}
    fin = db.query(HeadOfficeDailyFinancial).one()
    assert float(fin.revenue) == 100000 and float(fin.cash_flow) == 20000
    assert db.query(HeadOfficeActivityLog).one().meetings_attended == "Standup"
    health = db.query(HeadOfficeHealthStatus).one()
    assert health.daily_steps == 8000 and health.heart_rate == 72
    assert db.query(HeadOfficeSocialLog).one().event_flag is True
    assert db.query(AuditLog).filter(
        AuditLog.action == "HO_DAILY_REPORT_SUBMITTED").count() == 1


def test_ho_submit_report_missing_mandatory_field_400(ho_db):
    with pytest.raises(HTTPException) as exc:
        _submit(ho_db, ho_db.test_ho, revenue="")
    assert exc.value.status_code == 400
    assert "Revenue" in exc.value.detail


def test_ho_submit_report_resubmit_updates(ho_db):
    db, ho_user = ho_db, ho_db.test_ho
    assert _submit(db, ho_user) == {"ok": True}
    assert _submit(db, ho_user, revenue="150000") == {"ok": True}
    assert db.query(HeadOfficeDailyFinancial).count() == 1
    assert float(db.query(HeadOfficeDailyFinancial).one().revenue) == 150000
    assert db.query(AuditLog).filter(
        AuditLog.action == "HO_DAILY_REPORT_SUBMITTED").count() == 2


def test_ho_submit_report_rejects_bad_attachment(ho_db):
    from fastapi import UploadFile

    with pytest.raises(HTTPException) as exc:
        _submit(ho_db, ho_db.test_ho,
                attachment=UploadFile(filename="evil.exe", file=io.BytesIO(b"MZ...")))
    assert exc.value.status_code == 400
    assert "Unsupported attachment type" in exc.value.detail


def test_ho_submit_report_auto_posts_to_ho_bu(ho_db):
    db, ho_user = ho_db, ho_db.test_ho
    _provision_ho_bu(db)
    assert _submit(db, ho_user) == {"ok": True}
    entries = db.query(JournalEntry).filter(
        JournalEntry.source == "ho_daily_financial").all()
    assert len(entries) == 1
    assert entries[0].status == "posted"
    assert float(sum(l.debit for l in entries[0].lines)) == 100000
    # Resubmission reverses the first posting and posts the corrected one.
    assert _submit(db, ho_user, revenue="200000") == {"ok": True}
    entries = db.query(JournalEntry).filter(
        JournalEntry.source == "ho_daily_financial").order_by(JournalEntry.id).all()
    assert entries[0].reversed_by_id is not None
    live = [e for e in entries if e.status == "posted" and e.reversed_by_id is None]
    assert len(live) == 1
    assert float(sum(l.debit for l in live[0].lines)) == 200000


def test_ho_dashboard_after_submit(ho_db):
    db, ho_user = ho_db, ho_db.test_ho
    _submit(db, ho_user)
    out = ho_dashboard(user=ho_user, db=db)
    assert out["already_submitted_today"] is True
    assert out["financial"]["revenue"] == 100000
    assert len(out["financial_rows"]) == 1


# ------------------------------------------------------------------ scheduled alerts

@pytest.fixture()
def alerts_db(db):
    engine = db.test_engine
    bu = BusinessUnit(name="Shop", code="S1")
    db.add(bu)
    db.commit()
    db.test_bu = bu
    _add_user(db, "ceo1", "CEO", business_unit_id=bu.id)
    _add_user(db, "chair1", "Chairman")
    session2 = sessionmaker(bind=engine)()
    yield db, session2
    session2.close()


def _run_job(monkeypatch, session2):
    monkeypatch.setattr(scheduled_alerts, "SessionLocal", lambda: session2)
    return scheduled_alerts.main()


def test_scheduled_alerts_empty_db_returns_zero(monkeypatch, db):
    session2 = sessionmaker(bind=db.test_engine)()
    try:
        assert _run_job(monkeypatch, session2) == 0
    finally:
        session2.close()


def test_scheduled_alerts_missing_report_reminder(monkeypatch, alerts_db):
    db, session2 = alerts_db
    assert _run_job(monkeypatch, session2) == 0
    notes = db.query(Notification).filter(Notification.type == "missing_report").all()
    assert len(notes) == 1
    assert notes[0].user_id == db.query(User).filter(User.username == "ceo1").one().id
    # Once the CEO submits, the next run sends nothing. (today_local, not
    # date.today: the job keys "today" in Asia/Yangon.)
    db.add(DailyFinancial(business_unit_id=db.test_bu.id, report_date=today_local(),
                          revenue=Decimal("1"), expenses=Decimal("0"),
                          net_profit=Decimal("0"), cash_in_hand=Decimal("0")))
    db.commit()
    session3 = sessionmaker(bind=db.test_engine)()
    try:
        assert _run_job(monkeypatch, session3) == 0
    finally:
        session3.close()
    assert db.query(Notification).filter(Notification.type == "missing_report").count() == 1


def test_scheduled_alerts_document_and_batch_expiry(monkeypatch, alerts_db):
    db, session2 = alerts_db
    db.add(LicenseDocument(business_unit_id=db.test_bu.id, title="Trade licence",
                           document_type="license",
                           expiry_date=date.today() + timedelta(days=10)))
    item = InventoryItem(business_unit_id=db.test_bu.id, name="Cream", current_stock=5)
    db.add(item)
    db.flush()
    db.add(StockBatch(item_id=item.id, batch_no="B-1",
                      expiry_date=date.today() + timedelta(days=5), quantity=5))
    db.commit()
    assert _run_job(monkeypatch, session2) == 0
    assert db.query(Notification).filter(Notification.type == "document_expiry").count() == 2
    assert db.query(Notification).filter(Notification.type == "batch_expiry").count() == 2
    # Second run the same day notifies nobody (7-day throttle).
    session3 = sessionmaker(bind=db.test_engine)()
    try:
        assert _run_job(monkeypatch, session3) == 0
    finally:
        session3.close()
    assert db.query(Notification).filter(Notification.type == "document_expiry").count() == 2
    assert db.query(Notification).filter(Notification.type == "batch_expiry").count() == 2


def test_scheduled_alerts_failure_returns_one(monkeypatch, alerts_db):
    db, session2 = alerts_db
    monkeypatch.setattr(scheduled_alerts, "run_remind_missing_reports",
                        lambda db: (_ for _ in ()).throw(RuntimeError("db down")))
    monkeypatch.setattr(scheduled_alerts, "SessionLocal", lambda: session2)
    assert scheduled_alerts.main() == 1
