"""M-14 (Postgres variant): two-thread race on POST /api/ceo/submit-report.

Same race as tests/test_review_d_concurrency.py (SQLite), executed
against real Postgres. SKIPPED unless DATABASE_URL points at Postgres.
It runs in CI (.github/workflows/postgres-check.yml): a
postgres:16-alpine service, `alembic upgrade head`, then this file —
mirroring the deleted CM-3 pattern (tests/test_rt_postgres_concurrency.py),
which raced ERP endpoints that no longer exist.

NOT verified locally: no Postgres is available in this VM and there is
no docker binary here. Postgres execution is pending CI.

Each test seeds its own scratch rows (ids in the 90000 range, so they
cannot collide with dev seed data) and deletes them afterwards.
"""
import asyncio
import os
import threading
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from src.auth import hash_password as make_hash
from src.database import get_db
from src.main import app
from src.models import (
    ActivityLog,
    AuditLog,
    BusinessUnit,
    DailyFinancial,
    HealthStatus,
    SocialLog,
    User,
)
from src.routers.ceo import _report_submit_timestamps
from src.utils import today_local

import httpx

PW = "TestPass123!"
_PW_HASH = make_hash (PW)

BU_ID = 90001
USER_ID = 90001
FORM = {"revenue": "1000", "cash_flow": "100", "receivable": "0", "payable": "0"}


def _is_postgres():
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return False
    try:
        return make_url(url).get_dialect().name == "postgresql"
    except Exception:
        return "postgres" in url


pytestmark = pytest.mark.skipif(
    not _is_postgres(),
    reason="Postgres-only checks: run under the postgres-check CI workflow "
           "(DATABASE_URL must point at Postgres)",
)


@pytest.fixture()
def pg_engine():
    """Engine on the CI Postgres DB + seeded CEO/BU, get_db overridden
    per request with a fresh session. Tears the scratch rows down."""
    eng = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    maker = sessionmaker(bind=eng)
    with maker() as s:
        s.add_all([
            BusinessUnit(id=BU_ID, code="BU90001", name="Race Shop"),
            User(id=USER_ID, username="ceo_race_pg",
                 hashed_password=_PW_HASH,
                 role="CEO", business_unit_id=BU_ID, is_active=True,
                 require_password_change=False, is_profile_completed=True),
        ])
        s.commit()

    def _override():
        session = maker()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override
    yield eng
    app.dependency_overrides.pop(get_db, None)
    with maker() as s:
        for model, col in (
                (DailyFinancial, DailyFinancial.business_unit_id),
                (ActivityLog, ActivityLog.business_unit_id),
                (HealthStatus, HealthStatus.business_unit_id),
                (SocialLog, SocialLog.business_unit_id)):
            s.query(model).filter(col == BU_ID).delete()
        s.query(AuditLog).filter(AuditLog.user_id == USER_ID).delete()
        s.query(User).filter(User.id == USER_ID).delete()
        s.query(BusinessUnit).filter(BusinessUnit.id == BU_ID).delete()
        s.commit()
    eng.dispose()


def _login_cookie():
    async def _go():
        async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver") as ac:
            return await ac.post("/api/auth/login", json={
                "username": "ceo_race_pg", "password": PW})
    r = asyncio.run(_go())
    assert r.status_code == 200, r.text
    return r.headers.get("set-cookie", "").split(";")[0]


def _post_submit(cookie, report_date):
    async def _go():
        async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver") as ac:
            return await ac.post(
                "/api/ceo/submit-report",
                data={**FORM, "report_date": report_date.isoformat()},
                headers={"Cookie": cookie})
    return asyncio.run(_go())


def _race_once(cookie, report_date):
    barrier = threading.Barrier(2)
    results = {}

    def _run(slot):
        barrier.wait()
        results[slot] = _post_submit(cookie, report_date)

    threads = [threading.Thread(target=_run, args=(i,)) for i in (0, 1)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return sorted(r.status_code for r in results.values())


def test_double_submit_race_returns_409_loser_pg(pg_engine):
    """On Postgres the loser's commit must hit the per-day unique
    constraint and surface as 409 (never a 500, never two rows)."""
    cookie = _login_cookie()
    outcomes = []
    for attempt in range(8):
        _report_submit_timestamps.clear()
        raced_date = today_local() - timedelta(days=attempt)
        statuses = _race_once(cookie, raced_date)
        outcomes.append((str(raced_date), statuses))
        assert all(s in (200, 409) for s in statuses), outcomes
        with sessionmaker(bind=pg_engine)() as s:
            rows = s.query(DailyFinancial).filter(
                DailyFinancial.business_unit_id == BU_ID,
                DailyFinancial.report_date == raced_date).count()
        assert rows == 1, (outcomes, rows)
        if 409 in statuses:
            assert statuses == [200, 409], outcomes
            return
    pytest.fail(f"guard never fired: no 409 in 8 race attempts: {outcomes}")
