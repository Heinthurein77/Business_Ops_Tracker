"""Audit trail: tracker-side checks (revenue anomaly + meta-test).

ERP audit tests (PO/journal/invoice/payroll/expense/stock/production/
intercompany/supplier) were deleted with the ERP removal (2026-11-17):
those endpoints and their audit actions no longer exist. What remains is
the generic audit infrastructure plus the tracker anomaly tests.

Run with: ENVIRONMENT=test DATABASE_URL=sqlite:// .venv/bin/python -m pytest tests/test_audit_trail.py -q
"""
import ast
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from src.auth import hash_password
from src.models import (
    Base, BusinessUnit, DailyFinancial, Notification, User,
)
from src.routers.ceo import submit_report as ceo_submit_report
from src.utils import today_local

PASSWORD = "TestPass123!"


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    bu1 = BusinessUnit(id=1, code="BU1", name="Shop One")
    bu2 = BusinessUnit(id=2, code="BU2", name="Shop Two")
    session.add_all([bu1, bu2])
    chairman = User(id=701, username="chairman", hashed_password=hash_password(PASSWORD),
                    role="Chairman", is_active=True)
    ho = User(id=702, username="ho_user", hashed_password=hash_password(PASSWORD),
              role="HO", is_active=True)
    ceo = User(id=703, username="ceo_one", hashed_password=hash_password(PASSWORD),
               role="CEO", business_unit_id=1, is_active=True)
    session.add_all([chairman, ho, ceo])
    session.commit()
    session.test_bu = bu1
    session.test_bu2 = bu2
    session.test_users = {"chairman": chairman, "ho": ho, "ceo": ceo}
    yield session
    session.close()
    engine.dispose()


# ------------------------------------------------------------------ D-14(b): anomaly flag

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
    return ceo_submit_report(request=request, user=user, db=db, **payload)


def _seed_history(db, revenue, days=14):
    today = today_local()
    for i in range(1, days + 1):
        db.add(DailyFinancial(business_unit_id=1, report_date=today - timedelta(days=i),
                              revenue=Decimal(str(revenue)), expenses=Decimal("0"),
                              net_profit=Decimal("0"), cash_in_hand=Decimal("0")))
    db.commit()


def test_revenue_anomaly_flagged_and_ho_notified(db):
    ceo = db.test_users["ceo"]
    ho = db.test_users["ho"]
    _seed_history(db, 100000)
    out = _ceo_submit(db, ceo, revenue="200000")
    assert out == {"ok": True}
    fin = db.query(DailyFinancial).filter(
        DailyFinancial.business_unit_id == 1,
        DailyFinancial.report_date == today_local()).one()
    assert fin.revenue_anomaly is True
    notes = db.query(Notification).filter(
        Notification.user_id == ho.id, Notification.type == "alert").all()
    assert len(notes) == 1
    assert "Revenue anomaly" in notes[0].title
    assert "200,000" in notes[0].message


def test_revenue_within_band_not_flagged(db):
    ceo = db.test_users["ceo"]
    ho = db.test_users["ho"]
    _seed_history(db, 100000)
    assert _ceo_submit(db, ceo, revenue="110000") == {"ok": True}
    fin = db.query(DailyFinancial).filter(
        DailyFinancial.business_unit_id == 1,
        DailyFinancial.report_date == today_local()).one()
    assert fin.revenue_anomaly is False
    assert db.query(Notification).filter(
        Notification.user_id == ho.id, Notification.type == "alert").count() == 0


# ------------------------------------------------------------------ T-2 meta-test

# Endpoints whose mutation is genuinely not auditable: flipping a
# notification's own read state. Everything else that mutates must call
# the audit wrapper.
_META_ALLOWLIST = {
    ("POST", "/notifications/{notification_id}/read"),
    ("POST", "/notifications/read-all"),
}


def _mutating_handlers_without_audit(path):
    src = Path(path).read_text()
    tree = ast.parse(src)
    missing = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
                continue
            method = dec.func.attr
            if method not in ("post", "patch", "put", "delete"):
                continue
            route = dec.args[0].value if dec.args and isinstance(dec.args[0], ast.Constant) else "?"
            seg = ast.get_source_segment(src, node) or ""
            if "log_audit" not in seg and (method.upper(), route) not in _META_ALLOWLIST:
                missing.append(f"{method.upper()} {route} ({node.name})")
    return missing


def test_meta_every_mutating_endpoint_audits():
    # Tracker-only: accounting.py and modules.py were deleted with the ERP
    # removal (2026-11-17); only ops.py remains to scan.
    root = Path(__file__).resolve().parents[1] / "src" / "routers"
    missing = []
    for name in ("ops.py",):
        missing += [f"{name}: {m}" for m in _mutating_handlers_without_audit(root / name)]
    assert missing == [], f"mutating endpoints without an audit call: {missing}"
