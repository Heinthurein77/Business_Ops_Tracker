"""Tests for the BOT security-review backend-reliability batch
(D-1/D-2/D-3/D-8/D-10/D-12/D-13).

Run with: ENVIRONMENT=test DATABASE_URL=sqlite:// \
    .venv/bin/python -m pytest tests/test_sec_reliability_core.py

Style follows the rest of the suite: in-memory SQLite, router functions
called directly, plus raw-ASGI drives for the edge middleware and the
readiness endpoint (the venv has no httpx/TestClient).
"""
import asyncio
import logging
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DATABASE_URL", "sqlite://")

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from src.models import AuditLog, Base, BusinessUnit, User

ROOT = Path(__file__).resolve().parent.parent




@pytest.fixture()
def db_file(tmp_path):
    """File-backed SQLite: sessions get separate pooled connections (true
    transaction isolation), unlike :memory: where one connection is shared."""
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


# ------------------------------------------------------- raw-ASGI driver

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


async def _dummy_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": b"{}"})


# --------------------------------------------------------------------- D-1

class TestReadyz:
    def test_readyz_200_when_db_up(self):
        from src.main import app

        status, _, body = asgi_request(app, "GET", "/readyz")
        assert status == 200
        assert b'"ok"' in body

    def test_readyz_503_when_db_down_but_healthz_still_200(self, monkeypatch):
        from src.main import app

        bad_engine = create_engine("sqlite:////nonexistent_dir_xyz_12345/x.db")
        # m-16: /readyz pings through the dedicated probe_engine, not the
        # main pool — patch the engine the probe actually uses.
        monkeypatch.setattr("src.main.probe_engine", bad_engine)
        try:
            status, _, _ = asgi_request(app, "GET", "/readyz")
            assert status == 503
            # Liveness is only "process up": it must not touch the DB.
            status, _, body = asgi_request(app, "GET", "/healthz")
            assert status == 200
            assert b'"ok"' in body
        finally:
            bad_engine.dispose()


# --------------------------------------------------------------------- D-2

def _unreachable():
    return OperationalError("SELECT 1", {}, Exception("could not connect to server"))


class TestStartupRetry:
    def test_retries_then_succeeds(self, caplog):
        from src.main import _run_startup_step

        calls = {"n": 0}
        sleeps = []

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise _unreachable()
            return "done"

        caplog.set_level(logging.WARNING, logger="business_ops_tracker.seed")
        out = _run_startup_step(
            "migrations", flaky, attempts=5, delays=(5, 10, 15, 30), sleep=sleeps.append
        )
        assert out == "done"
        assert calls["n"] == 3
        assert sleeps == [5, 10]
        assert "database unreachable" in caplog.text

    def test_gives_up_after_all_attempts(self):
        from src.main import _run_startup_step

        sleeps = []

        def always_down():
            raise _unreachable()

        with pytest.raises(OperationalError):
            _run_startup_step(
                "seeding", always_down, attempts=3, delays=(1, 2), sleep=sleeps.append
            )
        assert sleeps == [1, 2]

    def test_migration_failure_logs_distinctly_from_unreachable(self, caplog):
        from src.main import _run_startup_step

        caplog.set_level(logging.WARNING, logger="business_ops_tracker.seed")
        with pytest.raises(RuntimeError, match="bad migration"):
            _run_startup_step(
                "migrations",
                lambda: (_ for _ in ()).throw(RuntimeError("bad migration")),
                attempts=2, delays=(0,), sleep=lambda s: None,
            )
        assert "migration failed" in caplog.text
        assert "database unreachable" not in caplog.text

    def test_lifespan_retries_migrations(self, monkeypatch, caplog):
        import time

        from src.main import lifespan

        monkeypatch.setattr(time, "sleep", lambda s: None)
        caplog.set_level(logging.WARNING, logger="business_ops_tracker.seed")
        calls = {"migrations": 0, "seed_bus": 0, "seed_users": 0}

        def flaky_migrations():
            calls["migrations"] += 1
            if calls["migrations"] < 3:
                raise _unreachable()

        monkeypatch.setattr("src.main.run_migrations", flaky_migrations)
        monkeypatch.setattr(
            "src.main.seed_business_units", lambda db: calls.__setitem__("seed_bus", 1)
        )
        monkeypatch.setattr(
            "src.main.seed_users", lambda db: calls.__setitem__("seed_users", 1)
        )
        monkeypatch.setattr("src.main.SessionLocal", lambda: SimpleNamespace(close=lambda: None))

        async def run():
            async with lifespan(SimpleNamespace()):
                pass

        asyncio.run(run())
        assert calls["migrations"] == 3
        assert calls["seed_bus"] == 1 and calls["seed_users"] == 1
        assert "database unreachable" in caplog.text


# --------------------------------------------------------------------- D-3

class _FlakySession:
    """Session whose SELECT 1 fails once, then works."""

    calls = 0

    def __init__(self):
        pass

    def execute(self, *args, **kwargs):
        type(self).calls += 1
        if type(self).calls == 1:
            raise OperationalError("SELECT 1", {}, Exception("could not connect"))
        return None

    def rollback(self):
        pass

    def close(self):
        pass


class _DeadSession:
    def execute(self, *args, **kwargs):
        raise OperationalError("SELECT 1", {}, Exception("could not connect"))

    def rollback(self):
        pass

    def close(self):
        pass


class TestGetDbRetry:
    def test_retry_once_with_fresh_session(self, monkeypatch):
        import src.database as database

        monkeypatch.setattr(database, "SessionLocal", _FlakySession)
        monkeypatch.setattr(database, "DB_RETRY_BACKOFF_SECONDS", 0)
        _FlakySession.calls = 0
        gen = database.get_db()
        db = next(gen)
        assert isinstance(db, _FlakySession)
        assert _FlakySession.calls == 2
        gen.close()

    def test_persistent_failure_is_503_not_500(self, monkeypatch):
        import src.database as database

        monkeypatch.setattr(database, "SessionLocal", _DeadSession)
        monkeypatch.setattr(database, "DB_RETRY_BACKOFF_SECONDS", 0)
        with pytest.raises(Exception) as exc:
            next(database.get_db())
        assert getattr(exc.value, "status_code", None) == 503
        assert "unavailable" in getattr(exc.value, "detail", "")

    def test_transient_mid_request_error_becomes_503(self):
        import src.database as database

        gen = database.get_db()
        next(gen)
        with pytest.raises(Exception) as exc:
            gen.throw(OperationalError("SELECT 1", {}, Exception("server closed the connection")))
        assert getattr(exc.value, "status_code", None) == 503

    def test_non_transient_error_passes_through(self):
        import src.database as database

        gen = database.get_db()
        next(gen)
        with pytest.raises(ValueError, match="app bug"):
            gen.throw(ValueError("app bug"))


# --------------------------------------------------------------------- D-8

class TestLogAuditIsolation:
    def test_audit_does_not_commit_callers_pending_work(self, db_file):
        from src.utils import log_audit

        user = User(username="d8_user", hashed_password="x", role="CEO")
        db_file.add(user)  # pending, deliberately NOT committed
        log_audit(db_file, user, "D8_TEST", "hello")
        # The audit row is written (own session, committed) ...
        checker = sessionmaker(bind=db_file.get_bind())()
        try:
            assert checker.query(AuditLog).filter(AuditLog.action == "D8_TEST").count() == 1
        finally:
            checker.close()
        # ... while the caller's pending work is still uncommitted ...
        assert user in db_file.new
        # ... and a rollback of the caller drops the business row only.
        db_file.rollback()
        checker = sessionmaker(bind=db_file.get_bind())()
        try:
            assert checker.query(User).filter(User.username == "d8_user").count() == 0
            assert checker.query(AuditLog).filter(AuditLog.action == "D8_TEST").count() == 1
        finally:
            checker.close()

    def test_audit_write_failure_does_not_fail_request(self, caplog):
        from src.utils import log_audit

        # An engine with no tables at all: the audit write must fail, and
        # log_audit must swallow it instead of raising.
        empty_engine = create_engine("sqlite://")
        broken_db = sessionmaker(bind=empty_engine)()
        caplog.set_level(logging.WARNING, logger="business_ops_tracker.audit")
        assert log_audit(broken_db, None, "D8_FAIL", "x", None, username="u") is None
        assert "Audit log write failed" in caplog.text
        broken_db.close()
        empty_engine.dispose()


# -------------------------------------------------------------------- D-10

class TestWriteBudget:
    def _middleware(self):
        from src.config import settings
        from src.edge import EdgeMiddleware

        patcher = mock.patch.multiple(
            settings, EDGE_WRITE_RATE_LIMIT=3, EDGE_WRITE_RATE_WINDOW=300
        )
        patcher.start()
        try:
            return EdgeMiddleware(_dummy_app)
        finally:
            patcher.stop()

    def _cookie(self, username):
        from src.auth import create_access_token
        from src.config import settings

        return {"cookie": f"{settings.COOKIE_NAME}={create_access_token(username, token_version=0)}"}

    def test_budget_blocks_mutating_calls_with_retry_after(self):
        app = self._middleware()
        headers = self._cookie("chairman")
        for _ in range(3):
            status, _, _ = asgi_request(app, "POST", "/api/ops/action-items", headers=headers)
            assert status == 200
        status, out_headers, _ = asgi_request(app, "POST", "/api/ops/action-items", headers=headers)
        assert status == 429
        assert int(out_headers["retry-after"]) >= 1

    def test_budget_is_per_user_and_gets_are_never_budgeted(self):
        app = self._middleware()
        for _ in range(3):
            asgi_request(app, "POST", "/api/ops/action-items", headers=self._cookie("chairman"))
        # A different user has their own budget.
        status, _, _ = asgi_request(app, "POST", "/api/ops/action-items", headers=self._cookie("ceo_bu1"))
        assert status == 200
        # GETs never consume the write budget.
        for _ in range(6):
            status, _, _ = asgi_request(app, "GET", "/api/ops/action-items", headers=self._cookie("chairman"))
            assert status == 200

    def test_unauthenticated_and_forged_cookies_fall_back_to_ip(self):
        app = self._middleware()
        for _ in range(3):
            status, _, _ = asgi_request(app, "POST", "/api/ops/action-items")
            assert status == 200
        status, _, _ = asgi_request(app, "POST", "/api/ops/action-items")
        assert status == 429
        # A forged cookie budgets the caller's own IP bucket, not someone else's.
        forged = {"cookie": "access_token=not-a-real-token"}
        status, _, _ = asgi_request(app, "POST", "/api/ops/action-items", headers=forged)
        assert status == 429
        # A different IP has its own budget.
        status, _, _ = asgi_request(
            app, "POST", "/api/ops/action-items", headers={"x-forwarded-for": "8.8.8.8"}
        )
        assert status == 200

    def test_sustained_429s_from_a_user_are_logged(self, caplog):
        app = self._middleware()
        caplog.set_level(logging.WARNING, logger="business_ops_tracker.error")
        headers = self._cookie("chairman")
        for _ in range(3):
            asgi_request(app, "POST", "/api/ops/action-items", headers=headers)
        for _ in range(3):  # three 429s = "sustained"
            status, _, _ = asgi_request(app, "POST", "/api/ops/action-items", headers=headers)
            assert status == 429
        assert "Sustained write rate limiting for user:chairman" in caplog.text

    def test_sustained_429s_log_immediately_on_a_fresh_process(self, caplog):
        # Regression: warned_at defaulted to 0.0, and time.monotonic() is
        # uptime-based, so on a freshly started process the first sustained
        # burst was silently suppressed for a whole window (the suite went
        # red when run within 5 minutes of boot). Freezing the clock near
        # boot must still log.
        app = self._middleware()
        caplog.set_level(logging.WARNING, logger="business_ops_tracker.error")
        headers = self._cookie("chairman")
        with mock.patch("time.monotonic", return_value=10.0):
            for _ in range(3):
                asgi_request(app, "POST", "/api/ops/action-items", headers=headers)
            for _ in range(3):
                status, _, _ = asgi_request(app, "POST", "/api/ops/action-items", headers=headers)
                assert status == 429
        assert "Sustained write rate limiting for user:chairman" in caplog.text


# -------------------------------------------------------------------- D-12

def _make_bus(db, code):
    bu = BusinessUnit(code=code, name=code)
    db.add(bu)
    db.commit()
    db.refresh(bu)
    return bu


# -------------------------------------------------------------------- D-13

class TestBootstrapChairmanPassword:
    def _run_settings(self, bootstrap_password):
        env = {
            **os.environ,
            "ENVIRONMENT": "test",
            "DATABASE_URL": "sqlite://",
            "BOOTSTRAP_CHAIRMAN_PASSWORD": bootstrap_password,
        }
        return subprocess.run(
            [sys.executable, "-c", "from src.config import Settings; Settings()"],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=60,
        )

    def test_weak_bootstrap_password_refuses_startup(self):
        for weak in ("short", "Chairman123!", "password123456"):
            result = self._run_settings(weak)
            assert result.returncode != 0, f"{weak!r} should refuse startup"
            assert "BOOTSTRAP_CHAIRMAN_PASSWORD is too weak" in result.stderr

    def test_strong_bootstrap_password_boots(self):
        result = self._run_settings("Str0ng-Bootstrap-Pass-2026!")
        assert result.returncode == 0, result.stderr

    def test_generated_password_goes_to_file_not_logs(self, db, tmp_path, monkeypatch, caplog):
        from src.auth import verify_password
        from src.config import settings
        from src.main import seed_users

        monkeypatch.setattr(settings, "ENVIRONMENT", "production")
        monkeypatch.setattr(settings, "BOOTSTRAP_CHAIRMAN_PASSWORD", "")
        monkeypatch.setattr(settings, "LOCAL_UPLOAD_DIR", str(tmp_path))
        caplog.set_level(logging.WARNING, logger="business_ops_tracker.seed")
        seed_users(db)

        chairman = db.query(User).filter(User.username == "chairman").one()
        assert chairman.require_password_change is True
        # m-17: the file lives next to the uploads directory, never inside
        # it (the uploads directory is served in non-production).
        password_file = tmp_path.parent / ".bootstrap_chairman_password"
        assert password_file.is_file()
        assert stat.S_IMODE(password_file.stat().st_mode) == 0o600
        full_password = password_file.read_text()
        assert len(full_password) >= 12
        assert verify_password(full_password, chairman.hashed_password)
        # The log carries the file path only — never the full value, and
        # not even a prefix hint (m-17).
        assert full_password not in caplog.text
        assert full_password[:4] not in caplog.text
        assert str(password_file) in caplog.text
