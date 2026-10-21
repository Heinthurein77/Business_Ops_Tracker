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


# --------------------------------------------------------------------- L5

class TestUploadPathCoverage:
    """Every UploadFile route in the codebase must be an edge upload path,
    or the edge's 1 MB default body cap rejects it before the app runs."""

    EXPECTED = {
        "/api/ceo/profile",
        "/api/ceo/submit-report",
        "/api/ho/submit-report",
        "/api/ops/expenses/{expense_id}/receipt",
        "/api/accounting/opening-balance",
    }

    def _upload_routes(self):
        import inspect
        import re as _re

        from src.routers import accounting, ceo, ho, ops

        found = set()
        for router_module in (ceo, ho, ops, accounting):
            for route in router_module.router.routes:
                endpoint = getattr(route, "endpoint", None)
                if endpoint is None:
                    continue
                params = inspect.signature(endpoint).parameters.values()
                if any("UploadFile" in str(p.annotation) for p in params):
                    found.add(route.path)
        return found, _re

    def test_all_upload_routes_are_edge_upload_paths(self):
        from src.edge import is_upload_path

        found, _re = self._upload_routes()
        assert found == self.EXPECTED  # a new upload route must be added to the edge list deliberately
        for path in found:
            concrete = _re.sub(r"\{[^}]+\}", "123", path)
            assert is_upload_path(concrete), f"{path} is not covered by the edge upload paths"

    def test_non_upload_paths_are_not_upload_paths(self):
        from src.edge import is_upload_path

        assert not is_upload_path("/api/ops/expenses")
        assert not is_upload_path("/api/accounting/reports/pl")
        assert not is_upload_path("/api/ops/expenses/123/receipt/extra")


# --------------------------------------------------------------------- L2

async def _dummy_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": b"{}"})


class TestExportBudget:
    def _middleware(self):
        from unittest import mock

        from src.config import settings
        from src.edge import EdgeMiddleware

        patcher = mock.patch.multiple(settings, EDGE_EXPORT_RATE_LIMIT=3, EDGE_EXPORT_RATE_WINDOW=300)
        patcher.start()
        try:
            return EdgeMiddleware(_dummy_app)
        finally:
            patcher.stop()

    def _cookie(self, username):
        from src.auth import create_access_token
        from src.config import settings

        return {"cookie": f"{settings.COOKIE_NAME}={create_access_token(username, token_version=0)}"}

    def test_budget_blocks_after_limit_with_retry_after(self):
        app = self._middleware()
        headers = self._cookie("chairman")
        for _ in range(3):
            status, _, _ = asgi_request(app, "GET", "/api/accounting/reports/pl", headers=headers)
            assert status == 200
        status, out_headers, _ = asgi_request(app, "GET", "/api/accounting/reports/pl", headers=headers)
        assert status == 429
        assert int(out_headers["retry-after"]) >= 1

    def test_budget_is_per_user_and_ip_fallback(self):
        app = self._middleware()
        for _ in range(3):
            asgi_request(app, "GET", "/api/ops/monthly-report", headers=self._cookie("chairman"))
        # A different user has their own budget.
        status, _, _ = asgi_request(app, "GET", "/api/ops/monthly-report", headers=self._cookie("ceo_bu1"))
        assert status == 200
        # Anonymous callers fall back to a per-IP budget.
        for _ in range(3):
            status, _, _ = asgi_request(app, "GET", "/api/ops/export-excel")
            assert status == 200
        status, _, _ = asgi_request(app, "GET", "/api/ops/export-excel")
        assert status == 429

    def test_normal_traffic_is_never_budgeted(self):
        app = self._middleware()
        headers = self._cookie("chairman")
        for _ in range(6):
            status, _, _ = asgi_request(app, "GET", "/api/ops/notifications", headers=headers)
            assert status == 200
            status, _, _ = asgi_request(app, "GET", "/api/accounting/finance-summary", headers=headers)
            assert status == 200

    def test_export_path_matching(self):
        from src.edge import is_export_path

        for path in (
            "/api/accounting/reports/pl",
            "/api/accounting/reports/export",
            "/api/accounting/reports/tax",
            "/api/ops/export-excel",
            "/api/admin/export-excel",
            "/api/ops/monthly-report",
            "/api/ops/sales-customers/42/statement",
            "/api/ops/payroll-runs/3/lines/9/payslip",
        ):
            assert is_export_path(path), path
        for path in ("/api/ops/inventory", "/api/auth/me", "/api/ops/expenses/1/receipt", "/api/admin/users"):
            assert not is_export_path(path), path


# --------------------------------------------------------------------- L1

class TestLocalUploadsGating:
    def test_uploads_route_uses_session_auth_not_a_public_mount(self):
        from starlette.routing import Mount

        from src.auth import get_current_user
        from src.main import app

        assert not any(isinstance(r, Mount) and r.path == "/uploads" for r in app.routes)
        route = next(r for r in app.routes if getattr(r, "path", "") == "/uploads/{file_path:path}")
        assert get_current_user in [d.call for d in route.dependant.dependencies]

    def test_resolve_local_upload(self, tmp_path, monkeypatch):
        from src.config import settings
        from src.main import _resolve_local_upload

        upload_dir = tmp_path / "uploads"
        (upload_dir / "expense-receipts").mkdir(parents=True)
        target = upload_dir / "expense-receipts" / "abc.png"
        target.write_bytes(b"png")
        (tmp_path / "secret.txt").write_text("nope")
        monkeypatch.setattr(settings, "LOCAL_UPLOAD_DIR", str(upload_dir))

        assert _resolve_local_upload("expense-receipts/abc.png") == target.resolve()
        for bad in ("../secret.txt", "expense-receipts/../../secret.txt", "missing.png", ""):
            with pytest.raises(HTTPException) as exc:
                _resolve_local_upload(bad)
            assert exc.value.status_code == 404

    def test_serve_local_upload_returns_the_file(self, tmp_path, monkeypatch):
        from src.config import settings
        from src.main import serve_local_upload

        upload_dir = tmp_path / "uploads"
        upload_dir.mkdir()
        (upload_dir / "a.txt").write_text("hello")
        monkeypatch.setattr(settings, "LOCAL_UPLOAD_DIR", str(upload_dir))

        response = asyncio.run(serve_local_upload("a.txt", user=SimpleNamespace(username="boss")))
        assert str(response.path) == str((upload_dir / "a.txt").resolve())


# --------------------------------------------------------------------- L3

CHAIRMAN_NS = SimpleNamespace(id=1, role="Chairman", business_unit_id=None)


class TestPeriodValidation:
    def test_helper(self):
        from src.utils import validate_period

        assert validate_period(None) is None
        assert validate_period("") == ""
        assert validate_period("2026-10") == "2026-10"
        assert validate_period("1999-01") == "1999-01"
        for bad in ("garbage", "2026-13", "2026-00", "2026-1", "26-10", "2026/10", "2026-10-01"):
            with pytest.raises(HTTPException) as exc:
                validate_period(bad)
            assert exc.value.status_code == 422

    def test_accounting_endpoints_422(self, db):
        from src.models import BusinessUnit
        from src.routers import accounting as acct

        bu = BusinessUnit(code="BU1", name="Shop")
        db.add(bu)
        db.commit()

        calls = [
            lambda: acct.profit_loss(bu.id, period="garbage", user=CHAIRMAN_NS, db=db),
            lambda: acct.balance_sheet(bu.id, period="garbage", user=CHAIRMAN_NS, db=db),
            lambda: acct.trial_balance(bu.id, period="2026-13", user=CHAIRMAN_NS, db=db),
            lambda: acct.ledger(1, period="garbage", user=CHAIRMAN_NS, db=db),
            lambda: acct.list_journal(bu.id, period="garbage", user=CHAIRMAN_NS, db=db),
            lambda: acct.consolidated_pl(period="garbage", user=CHAIRMAN_NS, db=db),
            lambda: acct.consolidated_bs(period="garbage", user=CHAIRMAN_NS, db=db),
            lambda: acct.bu_comparison(period="garbage", user=CHAIRMAN_NS, db=db),
            lambda: acct.tax_report(bu.id, period="garbage", user=CHAIRMAN_NS, db=db),
            lambda: acct.cash_flow_report(bu.id, period="garbage", user=CHAIRMAN_NS, db=db),
            lambda: acct.finance_summary(period="garbage", user=CHAIRMAN_NS, db=db),
            lambda: acct.export_report("pl", business_unit_id=bu.id, period="garbage", user=CHAIRMAN_NS, db=db),
        ]
        for call in calls:
            with pytest.raises(HTTPException) as exc:
                call()
            assert exc.value.status_code == 422

        # Valid periods keep working on an empty set of books.
        assert acct.profit_loss(bu.id, period="2026-10", user=CHAIRMAN_NS, db=db)["total_income"] == 0
        assert acct.consolidated_pl(period="2026-10", user=CHAIRMAN_NS, db=db)["net_profit"] == 0
        assert acct.finance_summary(period="2026-10", user=CHAIRMAN_NS, db=db)["period"] == "2026-10"

    def test_ops_and_modules_endpoints_422(self, db):
        from datetime import date
        from decimal import Decimal

        from src.models import BusinessUnit, Customer, LeaseContract, PropertyUnit
        from src.routers.modules import bill_lease, principal_report
        from src.routers.ops import list_attendance, monthly_report

        bu = BusinessUnit(code="BU1", name="Shop")
        db.add(bu)
        db.commit()
        tenant = Customer(business_unit_id=bu.id, name="Tenant")
        unit = PropertyUnit(business_unit_id=bu.id, unit_no="A1")
        db.add_all([tenant, unit])
        db.commit()
        lease = LeaseContract(
            business_unit_id=bu.id, unit_id=unit.id, tenant_id=tenant.id,
            monthly_rent=Decimal("1000"), start_date=date(2026, 1, 1), status="active",
        )
        db.add(lease)
        db.commit()

        calls = [
            lambda: monthly_report(month="garbage", user=CHAIRMAN_NS, db=db),
            lambda: monthly_report(month="2026-13", user=CHAIRMAN_NS, db=db),
            lambda: list_attendance(month="garbage", user=CHAIRMAN_NS, db=db),
            lambda: principal_report(period="garbage", user=CHAIRMAN_NS, db=db),
            lambda: bill_lease(lease.id, period="garbage", user=CHAIRMAN_NS, db=db),
        ]
        for call in calls:
            with pytest.raises(HTTPException) as exc:
                call()
            assert exc.value.status_code == 422

    def test_body_period_patterns_reject_month_13(self):
        from pydantic import ValidationError

        from src.routers.accounting import BudgetIn, DepreciateIn, PeriodAction
        from src.routers.ops import PayrollCreate

        for build in (
            lambda: PeriodAction(business_unit_id=1, period="2026-13"),
            lambda: DepreciateIn(business_unit_id=1, period="2026-13"),
            lambda: BudgetIn(business_unit_id=1, period="2026-13", account_code="1110", amount=1),
            lambda: PayrollCreate(business_unit_id=1, period="2026-13"),
        ):
            with pytest.raises(ValidationError):
                build()
        assert PeriodAction(business_unit_id=1, period="2026-10").period == "2026-10"
