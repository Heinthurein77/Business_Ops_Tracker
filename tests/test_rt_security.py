"""Regression tests for the 2026-11-09 real-time risk review, Workstream B
(security & failure modes + frontend API surface).

Run with: ENVIRONMENT=test DATABASE_URL="sqlite://" \\
    .venv/bin/python -m pytest tests/test_rt_security.py

Covers, at API level:
  SM-1  assert_token_current — a session revoked mid-request must 401
        instead of committing.
  SM-3  login breaker — a single-IP flood must NOT trip the global breaker
        (per-IP / per-username rules stop it); a distributed flood must,
        and the trip is logged at ERROR.
  Sm-4  upload paths without Content-Length are rejected 411.
  SM-7  an upload the app rejects with 4xx does not burn a daily-budget slot.
  (SC-1 journal-failures view and SM-13 auto-post surfacing were deleted
  with the ERP removal, 2026-11-17: no journals, no auto-post.)
"""
import asyncio
import os
import re
from datetime import datetime, timezone

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DATABASE_URL", "sqlite://")

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src import throttle
from src.auth import assert_token_current, bump_token_version
from src.edge import EdgeMiddleware
from src.models import AuditLog, Base, BusinessUnit, User

PUBLIC_IP = "8.8.8.8"


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([
        BusinessUnit(id=1, code="BU1", name="Shop One"),
        BusinessUnit(id=2, code="BU2", name="Shop Two"),
    ])
    ceo = User(id=801, username="ceo_one", hashed_password="x", role="CEO",
               business_unit_id=1, is_active=True)
    ho = User(id=803, username="ho_user", hashed_password="x", role="HO", is_active=True)
    chairman = User(id=804, username="chairman", hashed_password="x", role="Chairman", is_active=True)
    session.add_all([ceo, ho, chairman])
    session.commit()
    session.test_users = {"ceo": ceo, "ho": ho, "chairman": chairman}
    yield session
    session.close()
    engine.dispose()


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def add_failures(db, username, count, ip="1.1.1.1", when=None):
    when = when or _now()
    for _ in range(count):
        db.add(AuditLog(action="LOGIN_FAILED", username=username, ip_address=ip, timestamp=when))
    db.commit()


# --------------------------------------------------------------------- SM-1

class TestAssertTokenCurrent:
    def test_passes_when_token_is_current(self, db):
        user = db.test_users["ceo"]
        user._token_tv = user.token_version
        assert_token_current(db, user)  # no raise

    def test_401_after_logout_or_password_change_bump(self, db):
        user = db.test_users["ceo"]
        user._token_tv = user.token_version
        bump_token_version(db, user)
        with pytest.raises(HTTPException) as excinfo:
            assert_token_current(db, user)
        assert excinfo.value.status_code == 401

    def test_does_not_discard_uncommitted_changes(self, db):
        # The check reads the live version with a scalar SELECT, never
        # db.refresh(user): a caller that already staged changes on the
        # user (or other objects) must not lose them.
        user = db.test_users["ceo"]
        user._token_tv = user.token_version
        user.username = "renamed_before_commit"
        assert_token_current(db, user)
        assert user.username == "renamed_before_commit"


# --------------------------------------------------------------------- SM-3

class TestLoginBreaker:
    def test_single_ip_flood_does_not_trip_global_breaker(self, db):
        # 301 failures from one IP at one username is far past the breaker
        # limit (300) — but with no breadth it must NOT deny logins to
        # everyone. The per-IP rule stops the attacker instead.
        now = _now()
        add_failures(db, "victim", 301, ip="1.2.3.4", when=now)
        blocked = throttle.check_login_throttle(db, "victim", "1.2.3.4", now=now)
        assert blocked is not None
        assert blocked.retry_after == 900  # per-IP rule, capped
        # An unrelated user from an unrelated IP is unaffected.
        assert throttle.check_login_throttle(db, "innocent", "5.6.7.8", now=now) is None

    def test_distributed_flood_trips_breaker(self, db):
        # 360 failures spread over 12 IPs and 60 usernames: breadth is
        # real, so the breaker refuses even a fresh login, cheaply.
        now = _now()
        for i in range(60):
            add_failures(db, f"user_{i:02d}", 6, ip=f"10.0.0.{i % 12 + 1}", when=now)
        blocked = throttle.check_login_throttle(db, "fresh-user", "9.9.9.9", now=now)
        assert blocked is not None
        assert blocked.retry_after == throttle.LOGIN_BREAKER_WINDOW_SECONDS

    def test_breaker_trip_logs_error(self, db, caplog):
        # The trip must be visible: the daily log scan counts ERROR lines
        # and alerts the Chairman. Reset the once-per-window alert gate
        # so this test is deterministic regardless of run order.
        import logging

        throttle._breaker_alerted_at = 0.0
        with caplog.at_level(logging.ERROR, logger="src.throttle"):
            throttle._alert_breaker_trip("LOGIN_FAILED", 360, 12, 60)
        assert any("Login breaker tripped" in r.message for r in caplog.records)
        assert any(r.levelno == logging.ERROR for r in caplog.records)


# -------------------------------------------------------------- Sm-4 / SM-7

def _asgi_request(app, method, path, headers=None, body=b"", client=("127.0.0.1", 50000)):
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http", "http_version": "1.1", "method": method, "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
        "headers": raw_headers, "client": client, "server": ("testserver", 80),
    }
    sent = []
    request_messages = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive():
        return request_messages.pop(0) if request_messages else {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    payload = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, payload


def _app_with_status(status):
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": b"{}"})
    return app


class TestUploadBudget:
    def test_upload_without_content_length_rejected_411(self):
        edge = EdgeMiddleware(_app_with_status(200))
        status, _ = _asgi_request(edge, "POST", "/api/ceo/submit-report", headers={})
        assert status == 411

    def test_rejected_upload_does_not_burn_budget_slot(self):
        # SM-7: the app answers 400 (bad file) — the recorded hit is
        # refunded, so the caller's 50/day budget is untouched.
        edge = EdgeMiddleware(_app_with_status(400))
        status, _ = _asgi_request(
            edge, "POST", "/api/ceo/submit-report",
            headers={"content-length": "100"}, body=b"x" * 100,
        )
        assert status == 400
        assert not edge._upload_hits

    def test_accepted_upload_consumes_budget_slot(self):
        edge = EdgeMiddleware(_app_with_status(200))
        status, _ = _asgi_request(
            edge, "POST", "/api/ceo/submit-report",
            headers={"content-length": "100"}, body=b"x" * 100,
        )
        assert status == 200
        assert edge._upload_hits  # one hit recorded, not refunded


# -------------------------------------------------------------------- Sm-9

# Stored-URL fields rendered anywhere in the frontend. Old R2 URLs are
# bearer links (anyone with the link can read the file), so they must
# only ever be rendered through viewUploadUrl(), which rewrites them to
# the authenticated /api/ops/view-upload redirect. Generated API URLs
# (api.*Url(...)) are fresh same-origin endpoints, not stored links,
# and in-page anchors (#...) are not URLs at all — neither is in scope.
_STORED_URL_FIELDS = (
    "document_url", "receipt_url", "profile_pic_url", "attachment_url",
    "photo_url", "existingPhotoUrl",
)


class TestStoredUrlsGoThroughViewUploadUrl:
    def test_all_stored_url_renders_use_view_upload_url(self):
        frontend = os.path.join(os.path.dirname(__file__), "..", "frontend", "src")
        expr_re = re.compile(r"(?:href|src)=\{([^}]*)\}|window\.open\(([^)]*)\)")
        field_re = re.compile(r"\b(?:" + "|".join(_STORED_URL_FIELDS) + r")\b")
        violations = []
        checked = 0
        for root, _dirs, files in os.walk(frontend):
            for name in files:
                if not name.endswith(".jsx"):
                    continue
                path = os.path.join(root, name)
                with open(path, encoding="utf-8") as fh:
                    for lineno, line in enumerate(fh, start=1):
                        for m in expr_re.finditer(line):
                            expr = m.group(1) or m.group(2) or ""
                            if not field_re.search(expr):
                                continue
                            checked += 1
                            if "viewUploadUrl(" not in expr:
                                violations.append(f"{path}:{lineno}: {expr.strip()}")
        assert checked > 0, "expected at least one stored-URL render to check"
        assert not violations, (
            "stored URLs rendered without viewUploadUrl() (Sm-9):\n" + "\n".join(violations)
        )
