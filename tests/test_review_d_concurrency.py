"""M-14: concurrency/race proof for the tracker write path.

tests/test_rt_concurrency.py kept only 2 unrelated helper tests after
the ERP removal, and the Postgres proof file was deleted with it — so
the double-submit guard in ceo.py submit-report (IntegrityError on the
per-day unique constraint -> 409, ceo.py:1312-1329) had zero
concurrent-execution proof. This file races two threads against
POST /api/ceo/submit-report for the same (BU, report_date) behind a
threading.Barrier, on a file-backed SQLite DB (the http_db fixture
pattern from tests/test_http_smoke.py — in-memory SQLite cannot be
shared between the fixture thread and the endpoint threadpool).

The race is attempted up to 8 times with a fresh report_date per
attempt: whether the loser trips the unique constraint (409) or
serialises behind the winner and resubmits cleanly (200) depends on
thread scheduling, so a single attempt cannot prove the guard. An
attempt that yields a 409 proves the guard fired; every attempt must
yield no 500 and exactly one DailyFinancial row for the raced date.
If no attempt yields a 409, the test fails — the guard was never
exercised.
"""
import asyncio
import threading
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.auth import hash_password as make_hash
from src.database import get_db
from src.main import app
from src.models import Base, BusinessUnit, DailyFinancial, User
from src.routers.ceo import _report_submit_timestamps
from src.utils import today_local

PW = "TestPass123!"
_PW_HASH = make_hash (PW)

FORM = {"revenue": "1000", "cash_flow": "100", "receivable": "0", "payable": "0"}


@pytest.fixture()
def race_db(tmp_path):
    """File-backed engine + seeded CEO/BU. get_db is overridden once
    for the whole test with a generator yielding a FRESH session per
    request from this engine: the two racing threads must not share
    one Session object (and must not race the override itself — it is
    set here, not per call)."""
    db_path = tmp_path / "race.db"
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([
        BusinessUnit(id=1, code="BU1", name="Shop One"),
        User(username="ceo_one", hashed_password=_PW_HASH,
             role="CEO", business_unit_id=1, is_active=True,
             require_password_change=False, is_profile_completed=True),
    ])
    session.commit()
    session.close()

    maker = sessionmaker(bind=engine)

    def _override():
        s = maker()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override
    yield engine
    app.dependency_overrides.pop(get_db, None)
    engine.dispose()


def _login_cookie(engine):
    """Log in once (main thread) and return the session cookie string
    both racing threads send."""
    async def _go():
        async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver") as ac:
            return await ac.post("/api/auth/login", json={
                "username": "ceo_one", "password": PW})
    r = asyncio.run(_go())
    assert r.status_code == 200, r.text
    return r.headers.get("set-cookie", "").split(";")[0]


def _post_submit(engine, cookie, report_date):
    """One submit-report POST from this thread (own event loop)."""
    async def _go():
        async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver") as ac:
            return await ac.post(
                "/api/ceo/submit-report",
                data={**FORM, "report_date": report_date.isoformat()},
                headers={"Cookie": cookie})
    return asyncio.run(_go())


def _race_once(engine, cookie, report_date):
    """Two threads, released at the same instant by a Barrier."""
    barrier = threading.Barrier(2)
    results = {}

    def _run(slot):
        barrier.wait()
        results[slot] = _post_submit(engine, cookie, report_date)

    threads = [threading.Thread(target=_run, args=(i,)) for i in (0, 1)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return sorted(r.status_code for r in results.values())


def test_double_submit_race_returns_409_loser(race_db):
    cookie = _login_cookie(race_db)
    outcomes = []
    for attempt in range(8):
        _report_submit_timestamps.clear()  # 5 submits / 60 s rate limit
        raced_date = today_local() - timedelta(days=attempt)
        statuses = _race_once(race_db, cookie, raced_date)
        outcomes.append((str(raced_date), statuses))
        assert all(s in (200, 409) for s in statuses), outcomes
        with sessionmaker(bind=race_db)() as s:
            rows = s.query(DailyFinancial).filter(
                DailyFinancial.business_unit_id == 1,
                DailyFinancial.report_date == raced_date).count()
        assert rows == 1, (outcomes, rows)
        if 409 in statuses:
            assert statuses == [200, 409], outcomes
            return
    pytest.fail(f"guard never fired: no 409 in 8 race attempts: {outcomes}")


def test_sequential_double_submit_is_allowed_resubmit(race_db):
    """Baseline: the same report filed twice in sequence is a legal
    resubmit (200 + 200, still one row) — the 409 only fires on a true
    concurrent collision."""
    cookie = _login_cookie(race_db)
    raced_date = today_local()
    first = _post_submit(race_db, cookie, raced_date)
    assert first.status_code == 200, first.text
    _report_submit_timestamps.clear()
    second = _post_submit(race_db, cookie, raced_date)
    assert second.status_code == 200, second.text
    with sessionmaker(bind=race_db)() as s:
        assert s.query(DailyFinancial).filter(
            DailyFinancial.business_unit_id == 1,
            DailyFinancial.report_date == raced_date).count() == 1
