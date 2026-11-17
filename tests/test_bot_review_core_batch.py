"""Regression tests kept from the BOT review core batch (2026-11-09).

The ERP tests from this batch (journals, periods, intercompany,
depreciation, budgets, posting rules, CoA provisioning, opening
balances, HO auto-post) were deleted with the ERP removal (2026-11-17).
What remains is tracker-side: report-log pagination, the export-budget
eviction, user admin, crypto/date handling, login limits, client-IP
headers.

Run with: ENVIRONMENT=test DATABASE_URL=sqlite:// \
    .venv/bin/python -m pytest tests/test_bot_review_core_batch.py
"""
import time
from collections import deque
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from src.auth import hash_password
from src.client_ip import _from_named_header
from src.crypto import EncryptedDate
from src.edge import EdgeMiddleware
from src.models import AuditLog, Base, BusinessUnit, User
from src.routers.admin import report_logs
from src.routers.auth import login as login_endpoint
from src.routers.users import create_user, reset_user_password
from src.schemas import CreateUserRequest, LoginRequest

CHAIRMAN = SimpleNamespace(id=1, role="Chairman", business_unit_id=None, username="boss")
HO = SimpleNamespace(id=2, role="HO", business_unit_id=None, username="ho_staff")
REQUEST = SimpleNamespace(headers={}, client=SimpleNamespace(host="127.0.0.1"))


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([
        BusinessUnit(id=1, code="BU1", name="Shop One"),
        BusinessUnit(id=2, code="BU2", name="Shop Two"),
        User(username="boss", hashed_password="x", role="Chairman", full_name="Boss"),
    ])
    session.commit()
    yield session
    session.close()


# ------------------------------------------------------------------ m4/m5

def test_report_logs_paginated(db):
    for i in range(3):
        db.add(AuditLog(action="DAILY_REPORT_SUBMITTED", username=f"ceo{i}",
                        details=f"date=2026-10-0{i + 1}"))
    db.add(AuditLog(action="LOGIN_SUCCESS", username="boss", details=""))
    db.commit()
    page1 = report_logs(limit=2, offset=0, user=CHAIRMAN, db=db)
    assert len(page1["logs"]) == 2 and page1["total"] == 3
    page2 = report_logs(limit=2, offset=2, user=CHAIRMAN, db=db)
    assert len(page2["logs"]) == 1
    capped = report_logs(limit=9999, offset=0, user=CHAIRMAN, db=db)
    assert capped["limit"] == 500


def test_export_budget_evicts_empty_buckets(db):
    from src.config import settings

    mw = EdgeMiddleware(lambda scope, receive, send: None)
    stale = time.monotonic() - settings.EDGE_EXPORT_RATE_WINDOW - 10
    mw._export_hits["ip:1.2.3.4"] = deque([stale])
    scope = {"type": "http", "http_version": "1.1", "method": "GET", "scheme": "http",
             "path": "/api/admin/export-excel", "query_string": b"", "headers": [],
             "client": ("1.2.3.4", 1234), "server": ("testserver", 80)}
    assert mw._export_budget_retry(scope, {}) is None
    hits = mw._export_hits["ip:1.2.3.4"]
    assert len(hits) == 1 and hits[0] != stale


# ------------------------------------------------------------------ m9/m10

def _boss(db):
    return db.query(User).filter(User.username == "boss").first()


def test_create_user_rejects_bu_for_ho(db):
    with pytest.raises(HTTPException) as e:
        create_user(CreateUserRequest(username="ho_x", full_name="HO X", role="HO",
                                     business_unit_id=1),
                    None, Response(), _boss(db), db)
    assert e.value.status_code == 400


def test_reset_password_blocked_for_deactivated(db):
    out = create_user(CreateUserRequest(username="ceo_z", full_name="CEO Z",
                                       role="CEO", business_unit_id=1),
                      None, Response(), _boss(db), db)
    target = db.query(User).filter(User.id == out["user_id"]).first()
    target.is_active = False
    db.commit()
    with pytest.raises(HTTPException) as e:
        reset_user_password(target.id, None, Response(), _boss(db), db)
    assert e.value.status_code == 400


# ----------------------------------------------------------------- m11/m12/m13

def test_encrypted_date_rejects_non_iso_legacy(db):
    with pytest.raises(RuntimeError):
        EncryptedDate().process_result_value("not-a-date", None)
    assert EncryptedDate().process_result_value("2001-11-23", None) == date(2001, 11, 23)


def test_login_rejects_over_72_byte_password(db):
    db.add(User(username="victim", hashed_password=hash_password("CorrectHorse1!"),
                role="HO", full_name="Victim", is_active=True))
    db.commit()
    with pytest.raises(HTTPException) as e:
        login_endpoint(LoginRequest(username="victim", password="x" * 73),
                       REQUEST, Response(), db=db)
    assert e.value.status_code == 401


def _starlette_request(headers):
    scope = {"type": "http", "http_version": "1.1", "method": "GET", "scheme": "http",
             "path": "/", "query_string": b"", "headers": headers,
             "client": ("9.9.9.9", 1), "server": ("testserver", 80)}
    return Request(scope)


def test_named_header_rejects_comma_values(db):
    multi = _starlette_request([(b"x-forwarded-for", b"1.2.3.4, 5.6.7.8")])
    assert _from_named_header(multi, "x-forwarded-for") is None
    single = _starlette_request([(b"x-forwarded-for", b"1.2.3.4")])
    assert _from_named_header(single, "x-forwarded-for") == "1.2.3.4"
