"""Tests for the 2026-10-21 security-audit fixes (M1, L1–L5).

Run with: ENVIRONMENT=test DATABASE_URL=sqlite:// \
    .venv/bin/python -m pytest tests/test_security_fixes.py

Style follows the rest of the suite: in-memory SQLite, router functions
called directly, plus raw-ASGI drives for the edge middleware (the venv has
no httpx/TestClient).
"""
import asyncio
import os
from datetime import datetime, timedelta
from types import SimpleNamespace

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DATABASE_URL", "sqlite://")

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src import throttle
from src.auth import InsufficientRoleException
from src.models import AuditLog, Base, Notification, User

PUBLIC_IP = "8.8.8.8"


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


def add_user(db, username, role, is_active=True):
    user = User(username=username, hashed_password="x", role=role, is_active=is_active)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def add_failures(db, username, count, when=None):
    when = when or datetime.utcnow()
    for _ in range(count):
        db.add(AuditLog(action="LOGIN_FAILED", username=username, ip_address="1.1.1.1", timestamp=when))
    db.commit()


def lock_account(db, username):
    add_failures(db, username, throttle.LOGIN_USER_LIMIT)
    blocked = throttle.check_login_throttle(db, username, PUBLIC_IP)
    assert blocked is not None
    return blocked


# --------------------------------------------------------------------- M1

class TestUnlockEndpoint:
    def test_unlock_clears_lock_and_audits(self, db):
        from src.routers.users import unlock_user_login

        victim = add_user(db, "victim_ceo", "CEO")
        boss = add_user(db, "boss", "Chairman")
        lock_account(db, "victim_ceo")

        out = unlock_user_login(victim.id, None, boss, db)
        assert out == {"ok": True, "was_locked": True}
        assert throttle.check_login_throttle(db, "victim_ceo", PUBLIC_IP) is None

        marker = db.query(AuditLog).filter(AuditLog.action == "LOGIN_UNLOCK", AuditLog.username == "victim_ceo").all()
        assert len(marker) == 1
        audit = db.query(AuditLog).filter(AuditLog.action == "USER_LOGIN_UNLOCKED", AuditLog.username == "boss").all()
        assert len(audit) == 1
        assert "victim_ceo" in (audit[0].details or "")

    def test_unlock_when_not_locked_is_a_noop(self, db):
        from src.routers.users import unlock_user_login

        victim = add_user(db, "calm_ceo", "CEO")
        boss = add_user(db, "boss", "Chairman")
        out = unlock_user_login(victim.id, None, boss, db)
        assert out == {"ok": True, "was_locked": False}
        assert db.query(AuditLog).filter(AuditLog.action == "LOGIN_UNLOCK").count() == 0

    def test_unlock_unknown_user_404(self, db):
        from src.routers.users import unlock_user_login

        boss = add_user(db, "boss", "Chairman")
        with pytest.raises(HTTPException) as exc:
            unlock_user_login(9999, None, boss, db)
        assert exc.value.status_code == 404

    def test_unlock_role_guard(self):
        from src.routers.users import router

        route = next(r for r in router.routes if getattr(r, "path", "") == "/api/admin/users/{user_id}/unlock-login")
        guard = next(d.call for d in route.dependant.dependencies if getattr(d.call, "__name__", "") == "dependency")
        for role in ("CEO", "Accountant"):
            with pytest.raises(InsufficientRoleException):
                guard(user=SimpleNamespace(role=role, username="x"))
        for role in ("Chairman", "HO"):
            assert guard(user=SimpleNamespace(role=role, username="x")).role == role

    def test_user_list_login_locked_flag(self, db):
        from src.routers.users import list_users, unlock_user_login

        victim = add_user(db, "flagged_ceo", "CEO")
        boss = add_user(db, "boss", "Chairman")

        def flag():
            entries = list_users(user=boss, db=db)["users"]
            return next(e["login_locked"] for e in entries if e["id"] == victim.id)

        assert flag() is False
        lock_account(db, "flagged_ceo")
        assert flag() is True
        unlock_user_login(victim.id, None, boss, db)
        assert flag() is False


# ------------------------------------------------------- ASGI test driver

def asgi_request(app, method, path, headers=None, body=b""):
    """Minimal raw-ASGI drive: returns (status, headers dict, body bytes)."""
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": raw_headers,
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }
    sent = []
    request_messages = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive():
        return request_messages.pop(0) if request_messages else {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    out_headers = {}
    for m in sent:
        if m["type"] == "http.response.start":
            for k, v in m["headers"]:
                out_headers[k.decode()] = v.decode()
    payload = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, out_headers, payload
