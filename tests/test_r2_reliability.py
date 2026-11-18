"""D-4 / D-5 / D-6: R2 attachment reliability, short-lived view URLs, decrypt grace.

- D-4: a failed R2 upload is logged and fails loud (503) for receipts and
  daily-report submits; the profile photo keeps its warn-the-user behaviour.
- D-5: newly minted presigned URLs expire in 900s; attachments are viewed
  through the session-gated /api/ops/uploads/view endpoint (BU ownership
  checked), never via the stored URL.
- D-6: a CEO profile row encrypted under a lost key shows "unavailable"
  instead of 500ing the profile page.

Run with: ENVIRONMENT=test DATABASE_URL=sqlite:// .venv/bin/python -m pytest tests/test_r2_reliability.py -q
"""
import io
import logging
from datetime import date
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError
from fastapi import HTTPException, UploadFile
from sqlalchemy import text
from starlette.requests import Request

from src import r2_storage as r2_mod
from src.auth import hash_password
from src.config import settings
from src.models import (ActivityLog, BusinessUnit, CeoProfile, DailyFinancial, HeadOfficeActivityLog, User)
from src.r2_storage import (
    PRESIGNED_URL_EXPIRY_SECONDS, extract_r2_key, presigned_url_for_key,
    upload_fileobj,
)
from src.report_forms import build_ceo_report_form, build_ho_report_form
from src.routers.ceo import get_profile, set_profile, submit_report as ceo_submit_report
from src.routers.ho import submit_report as ho_submit_report
from src.routers.ops import view_upload
from src.utils import today_local

PASSWORD = "TestPass123!"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF_BYTES = b"%PDF-1.4 fake pdf content"
REQUEST = SimpleNamespace(headers={}, client=SimpleNamespace(host="127.0.0.1"))


class _FakeS3:
    """Fake R2 client that records the presign expiry it was asked for."""

    def __init__(self, fail=False):
        self.fail = fail
        self.presigned = "https://presigned.example/object"
        self.last_expires_in = None

    def upload_fileobj(self, fileobj, bucket, key, ExtraArgs=None):
        if self.fail:
            raise ClientError({"Error": {"Code": "NoSuchBucket", "Message": "boom"}},
                              "UploadPart")
        fileobj.read()

    def generate_presigned_url(self, op, Params=None, ExpiresIn=None):
        self.last_expires_in = ExpiresIn
        return self.presigned


@pytest.fixture(autouse=True)
def _seed_db(db):
    db.add_all([
        BusinessUnit(id=1, code="BU1", name="Shop One"),
        BusinessUnit(id=2, code="BU2", name="Shop Two"),
    ])
    ceo = User(id=801, username="ceo_one", hashed_password=hash_password(PASSWORD),
               role="CEO", business_unit_id=1, is_active=True)
    ceo2 = User(id=802, username="ceo_two", hashed_password=hash_password(PASSWORD),
                role="CEO", business_unit_id=2, is_active=True)
    ho = User(id=803, username="ho_user", hashed_password=hash_password(PASSWORD),
              role="HO", is_active=True)
    chairman = User(id=804, username="chairman", hashed_password=hash_password(PASSWORD),
                    role="Chairman", is_active=True)
    db.add_all([ceo, ceo2, ho, chairman])
    db.commit()
    db.test_users = {"ceo": ceo, "ceo2": ceo2, "ho": ho, "chairman": chairman}


def _configured_r2(monkeypatch):
    """Make R2 look configured (creds + bucket, no public base) so the
    presigned-URL path is exercised instead of the local fallback."""
    monkeypatch.setattr(settings, "R2_ACCOUNT_ID", "test-acct")
    monkeypatch.setattr(settings, "R2_ACCESS_KEY_ID", "test-key")
    monkeypatch.setattr(settings, "R2_SECRET_ACCESS_KEY", "test-secret")
    monkeypatch.setattr(settings, "R2_BUCKET_NAME", "test-bucket")
    monkeypatch.setattr(settings, "R2_PUBLIC_BASE_URL", "")

def _failing_r2(monkeypatch):
    monkeypatch.setattr(r2_mod, "_get_client", lambda: _FakeS3(fail=True))


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
    request = Request({"type": "http", "headers": [], "client": ("8.8.8.8", 5555)})
    form = build_ceo_report_form(request=request, user=user, db=db,
                                 rate_limiter=ceo_router._rate_limiter, **payload)
    return ceo_submit_report(request=request, form=form, user=user, db=db)


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
    request = Request({"type": "http", "headers": [], "client": ("8.8.8.8", 5555)})
    form = build_ho_report_form(request=request, user=user, db=db,
                                rate_limiter=ho_router._rate_limiter, **payload)
    return ho_submit_report(request, form=form, user=user, db=db)


# ------------------------------------------------------------------ D-5 expiry

def test_presigned_url_expires_in_900s(monkeypatch):
    assert PRESIGNED_URL_EXPIRY_SECONDS == 900
    fake = _FakeS3()
    monkeypatch.setattr(r2_mod, "_get_client", lambda: fake)
    monkeypatch.setattr(settings, "R2_BUCKET_NAME", "test-bucket")
    monkeypatch.setattr(settings, "R2_PUBLIC_BASE_URL", "")
    assert upload_fileobj(io.BytesIO(PNG_BYTES), "pic.png") == fake.presigned
    assert fake.last_expires_in == 900
    assert presigned_url_for_key("activity-attachments/abc.png") == fake.presigned
    assert fake.last_expires_in == 900


def test_extract_r2_key_shapes():
    assert extract_r2_key(
        "https://test-bucket.r2.dev/activity-attachments/abc123.png?X-Amz-Expires=900"
    ) == "activity-attachments/abc123.png"
    assert extract_r2_key(
        "https://files.example.com/expense-receipts/def456.pdf"
    ) == "expense-receipts/def456.pdf"
    assert extract_r2_key("/uploads/profile-photos/ghi789.png") == "profile-photos/ghi789.png"
    assert extract_r2_key(None) is None
    assert extract_r2_key("javascript:alert(1)") is None
    assert extract_r2_key("https://example.com/single") is None


# ------------------------------------------------------------------ D-4 failures

def test_r2_failure_is_logged_with_bucket_and_key(monkeypatch, caplog):
    _failing_r2(monkeypatch)
    monkeypatch.setattr(settings, "R2_BUCKET_NAME", "test-bucket")
    with caplog.at_level(logging.ERROR, logger="src.r2_storage"):
        assert upload_fileobj(io.BytesIO(PNG_BYTES), "pic.png") is None
    assert "R2 upload failed" in caplog.text
    assert "test-bucket" in caplog.text


def test_ceo_submit_with_attachment_failing_r2_returns_503(db, monkeypatch):
    _failing_r2(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        _ceo_submit(db, db.test_users["ceo"],
                    attachment=UploadFile(filename="evidence.pdf",
                                          file=io.BytesIO(PDF_BYTES)))
    assert exc.value.status_code == 503
    # Nothing was saved: the report must not exist as if evidence were attached.
    assert db.query(DailyFinancial).filter(
        DailyFinancial.business_unit_id == 1,
        DailyFinancial.report_date == today_local()).count() == 0


def test_ho_submit_with_attachment_failing_r2_returns_503(db, monkeypatch):
    _failing_r2(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        _ho_submit(db, db.test_users["ho"],
                   attachment=UploadFile(filename="evidence.pdf",
                                         file=io.BytesIO(PDF_BYTES)))
    assert exc.value.status_code == 503


def test_profile_photo_failing_r2_warns_but_saves_profile(db, monkeypatch):
    _failing_r2(monkeypatch)
    # R2 looks configured (so the "couldn't be uploaded" branch is taken),
    # but every upload throws.
    monkeypatch.setattr(settings, "R2_ACCOUNT_ID", "test-acct")
    monkeypatch.setattr(settings, "R2_ACCESS_KEY_ID", "test-key")
    monkeypatch.setattr(settings, "R2_SECRET_ACCESS_KEY", "test-secret")
    out = set_profile(
        REQUEST, full_name="CEO One", executive_title="Chief Executive Officer",
        phone_number="091234567", email="ceo@example.com",
        date_of_birth="2001-11-23", address="Yangon", business_details="Retail",
        profile_photo=UploadFile(filename="photo.png", file=io.BytesIO(PNG_BYTES)),
        user=db.test_users["ceo"], db=db)
    assert out["ok"] is True
    assert out["photo_warning"] is not None
    assert "couldn't be uploaded" in out["photo_warning"]
    db.expire_all()
    assert db.test_users["ceo"].is_profile_completed is True
    assert db.query(CeoProfile).one().profile_pic_url is None


# ------------------------------------------------------------------ D-5 view endpoint

def _seed_activity_attachment(db, url, bu_id=1):
    # Was _seed_receipt (ExpenseRequest): the expense receipt branch of
    # view_upload died with the ERP removal (2026-11-17). Tracker
    # attachments live on ActivityLog.document_url instead.
    row = ActivityLog(business_unit_id=bu_id, report_date=today_local(),
                      document_url=url)
    db.add(row)
    db.commit()
    return row


def test_view_upload_own_bu_redirects_to_fresh_presigned(db, monkeypatch):
    fake = _FakeS3()
    monkeypatch.setattr(r2_mod, "_get_client", lambda: fake)
    _configured_r2(monkeypatch)
    _seed_activity_attachment(db, "https://bucket.r2.dev/activity-attachments/abc123.png")
    resp = view_upload(key="activity-attachments/abc123.png", user=db.test_users["ceo"], db=db)
    assert resp.status_code == 302
    assert resp.headers["location"] == fake.presigned
    assert fake.last_expires_in == 900


def test_view_upload_other_bu_forbidden(db, monkeypatch):
    monkeypatch.setattr(r2_mod, "_get_client", lambda: _FakeS3())
    _seed_activity_attachment(db, "https://bucket.r2.dev/activity-attachments/abc123.png", bu_id=1)
    with pytest.raises(HTTPException) as exc:
        view_upload(key="activity-attachments/abc123.png", user=db.test_users["ceo2"], db=db)
    assert exc.value.status_code == 403


def test_view_upload_unknown_key_404_and_bad_key_400(db):
    with pytest.raises(HTTPException) as exc:
        view_upload(key="activity-attachments/nope.png", user=db.test_users["ceo"], db=db)
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        view_upload(key="../etc/passwd", user=db.test_users["ceo"], db=db)
    assert exc.value.status_code == 400


def test_view_upload_profile_photo_owner_only(db, monkeypatch):
    fake = _FakeS3()
    monkeypatch.setattr(r2_mod, "_get_client", lambda: fake)
    _configured_r2(monkeypatch)
    db.add(CeoProfile(user_id=801, executive_title="CEO", date_of_birth=date(2001, 11, 23),
                      phone_number="091234567", address="Yangon",
                      profile_pic_url="https://bucket.r2.dev/profile-photos/me.png"))
    db.commit()
    resp = view_upload(key="profile-photos/me.png", user=db.test_users["ceo"], db=db)
    assert resp.status_code == 302
    with pytest.raises(HTTPException) as exc:
        view_upload(key="profile-photos/me.png", user=db.test_users["ceo2"], db=db)
    assert exc.value.status_code == 403
    # Chairman/HO may view any profile photo.
    assert view_upload(key="profile-photos/me.png",
                       user=db.test_users["chairman"], db=db).status_code == 302


def test_view_upload_ho_attachment_admin_only(db, monkeypatch):
    fake = _FakeS3()
    monkeypatch.setattr(r2_mod, "_get_client", lambda: fake)
    _configured_r2(monkeypatch)
    db.add(HeadOfficeActivityLog(report_date=today_local(), meetings_attended="x",
                                 document_url="https://bucket.r2.dev/activity-attachments/ho.pdf"))
    db.commit()
    with pytest.raises(HTTPException) as exc:
        view_upload(key="activity-attachments/ho.pdf", user=db.test_users["ceo"], db=db)
    assert exc.value.status_code == 403
    resp = view_upload(key="activity-attachments/ho.pdf",
                       user=db.test_users["ho"], db=db)
    assert resp.status_code == 302
    assert resp.headers["location"] == fake.presigned


def test_view_upload_local_fallback_redirects_to_uploads_route(db, monkeypatch):
    monkeypatch.setattr(r2_mod, "is_configured", lambda: False)
    _seed_activity_attachment(db, "/uploads/activity-attachments/abc123.png")
    resp = view_upload(key="activity-attachments/abc123.png", user=db.test_users["ceo"], db=db)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/uploads/activity-attachments/abc123.png"


# ------------------------------------------------------------------ D-6 decrypt grace

def test_profile_with_undecryptable_field_shows_unavailable(db):
    ceo = db.test_users["ceo"]
    db.add(CeoProfile(user_id=ceo.id, executive_title="Chief Executive Officer",
                      phone_number="091234567", email="ceo@example.com",
                      date_of_birth=date(2001, 11, 23),
                      address="Yangon", business_details="Retail"))
    db.commit()
    # Simulate a row encrypted under a lost/rotated-out key: ciphertext the
    # current key cannot open. Planted with raw SQL so the ORM never
    # re-encrypts it on the way in.
    db.execute(text(
        "UPDATE ceo_profiles SET phone_number = 'enc:v1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA' "
        "WHERE user_id = :uid"), {"uid": ceo.id})
    db.commit()
    out = get_profile(user=ceo, db=db)
    assert out["phone_number"] == "unavailable"
    assert out["email"] == "unavailable"
    assert out["address"] == "unavailable"
    assert out["profile_fields_unavailable"] is True
    # Plaintext columns on the same row still read fine.
    assert out["executive_title"] == "Chief Executive Officer"
    assert out["business_details"] == "Retail"
    assert out["date_of_birth"] is None
    assert out["age"] is None
