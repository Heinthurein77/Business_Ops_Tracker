"""Regression tests for the BOT security + reliability review minors batch
(workstream 1, backend code): P3-2, P3-4, P3-6, P3-8, D-m1, D-m2, D-m3, D-m7.

The ERP tests from this batch (journal export, opening-balance template,
payslip filename, payment/return allocation, journal-failure surfacing,
serial JSON, journal deletes, PO/inventory BU scoping) were deleted with
the ERP removal (2026-11-17). In-memory SQLite, calling router functions
directly, except the edge tests, which use the raw-ASGI harness from
tests/test_edge_surface.py.
"""
import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

from src.config import settings
from src.main import app as fastapi_app
from src.main import lifespan, seed_business_units
from src.models import BusinessUnit
from src.utils import utcnow

CHAIRMAN = SimpleNamespace(id=1, role="Chairman", business_unit_id=None)


@pytest.fixture(autouse=True)
def _seed_db(db):
    bu1 = BusinessUnit(id=1, code="BU1", name="Shop One")
    bu2 = BusinessUnit(id=2, code="BU2", name="Shop Two")
    db.add_all([bu1, bu2])
    db.commit()
    db.test_bu = bu1
    db.test_bu2 = bu2


# ---------------------------------------------------------------- P3-2


# ---------------------------------------------------------------- P3-3


# ---------------------------------------------------------------- P3-6
def test_p3_6_utcnow_is_naive_utc_like_the_old_call():
    now = utcnow()
    assert now.tzinfo is None
    assert abs((datetime.now() - now).total_seconds()) < 5


def test_p3_6_no_datetime_utcnow_call_sites_remain_in_src():
    pattern = re.compile(r"datetime\.utcnow\s*[\(\,]")
    offenders = []
    for path in Path("src").rglob("*.py"):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path}:{lineno}: {line.strip()}")
    assert offenders == []


# ---------------------------------------------------------------- P3-7


# ---------------------------------------------------------------- P3-8
def test_p3_8_origin_check_rejects_host_outside_allowed_hosts(monkeypatch):
    from src.edge import EdgeMiddleware

    async def _dummy(scope, receive, send):
        pass

    monkeypatch.setattr(settings, "ALLOWED_HOSTS", ["app.example.com"])
    monkeypatch.setattr(settings, "ALLOWED_ORIGINS", [])
    mw = EdgeMiddleware(_dummy)
    good = {b"origin": b"https://app.example.com", b"host": b"app.example.com"}
    assert mw._origin_ok(good) is True
    # Same origin string, but the Host is not one the app answers to: the
    # fallback must not trust it.
    bad = {b"origin": b"https://app.example.com", b"host": b"evil.example.com"}
    assert mw._origin_ok(bad) is False


# ---------------------------------------------------------------- D-m1
def test_dm1_r2_client_has_timeouts_and_retries(monkeypatch):
    from src import r2_storage as r2_mod

    monkeypatch.setattr(settings, "R2_ACCOUNT_ID", "test-account")
    monkeypatch.setattr(settings, "R2_ACCESS_KEY_ID", "test-key")
    monkeypatch.setattr(settings, "R2_SECRET_ACCESS_KEY", "test-secret")
    monkeypatch.setattr(settings, "R2_ENDPOINT_URL", "https://example.r2.cloudflarestorage.com")
    client = r2_mod._get_client()
    assert client is not None
    assert client.meta.config.connect_timeout == 10
    assert client.meta.config.read_timeout == 30
    # botocore normalises retries={"max_attempts": 2} (legacy mode) to
    # total_max_attempts = max_attempts + 1, i.e. 2 retries after the
    # first attempt.
    assert client.meta.config.retries["total_max_attempts"] == 3


# ---------------------------------------------------------------- D-m2
def test_dm2_startup_logs_client_ip_source(monkeypatch, caplog):
    monkeypatch.setattr("src.main._run_startup_step", lambda *a, **k: None)

    async def _run():
        async with lifespan(fastapi_app):
            pass

    with caplog.at_level(logging.INFO):
        asyncio.run(_run())
    assert any("CLIENT_IP_HEADER" in r.message and "TRUSTED_PROXY_HOPS" in r.message
               for r in caplog.records)


# ---------------------------------------------------------------- D-m3
def test_dm3_bu_seed_survives_integrity_race(db, monkeypatch):
    real_commit = db.commit
    calls = {"n": 0}

    def flaky_commit():
        calls["n"] += 1
        if calls["n"] == 1:
            raise IntegrityError("INSERT", {}, Exception("duplicate key value"))
        return real_commit()

    monkeypatch.setattr(db, "commit", flaky_commit)
    seed_business_units(db)  # must not raise: the loser rolls back and moves on
    assert calls["n"] == 1


def test_dm3_bu_seed_is_idempotent(db):
    seed_business_units(db)
    seed_business_units(db)
    codes = [bu.code for bu in db.query(BusinessUnit).all()]
    assert len(codes) == len(set(codes))


# ---------------------------------------------------------------- D-m7
def _asgi_request(app, method, path, headers=None, body=b""):
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http", "http_version": "1.1", "method": method, "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
        "headers": raw_headers, "client": ("127.0.0.1", 50000), "server": ("testserver", 80),
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


async def _dummy_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200,
                "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": b"{}"})


def _upload_middleware(monkeypatch, count_limit, bytes_limit):
    from src.edge import EdgeMiddleware

    monkeypatch.setattr(settings, "EDGE_UPLOAD_DAILY_COUNT_LIMIT", count_limit)
    monkeypatch.setattr(settings, "EDGE_UPLOAD_DAILY_BYTES_LIMIT", bytes_limit)
    return EdgeMiddleware(_dummy_app)


def test_dm7_daily_upload_count_budget_429s(monkeypatch):
    mw = _upload_middleware(monkeypatch, count_limit=3, bytes_limit=10 ** 9)
    headers = {"host": "testserver", "content-length": "10"}
    for _ in range(3):
        status, _ = _asgi_request(mw, "POST", "/api/ceo/submit-report", headers=headers, body=b"x" * 10)
        assert status == 200
    status, payload = _asgi_request(mw, "POST", "/api/ceo/submit-report", headers=headers, body=b"x" * 10)
    assert status == 429
    assert "upload budget" in payload.decode().lower()


def test_dm7_daily_upload_size_budget_429s(monkeypatch):
    mw = _upload_middleware(monkeypatch, count_limit=100, bytes_limit=100)
    headers = {"host": "testserver", "content-length": "60"}
    status, _ = _asgi_request(mw, "POST", "/api/ceo/submit-report", headers=headers, body=b"x" * 60)
    assert status == 200
    status, _ = _asgi_request(mw, "POST", "/api/ceo/submit-report", headers=headers, body=b"x" * 60)
    assert status == 429


def test_dm7_non_upload_paths_do_not_consume_upload_budget(monkeypatch):
    mw = _upload_middleware(monkeypatch, count_limit=1, bytes_limit=10 ** 9)
    headers = {"host": "testserver", "content-length": "10"}
    for _ in range(5):
        status, _ = _asgi_request(mw, "POST", "/api/ops/action-items", headers=headers, body=b"x" * 10)
        assert status == 200


# ---------------------------------------------------------------- P3-5 (direct-call complement)


