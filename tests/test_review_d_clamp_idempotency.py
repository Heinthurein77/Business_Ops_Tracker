"""m-01 (clamped limit/offset/days) and m-10 (Idempotency-Key) tests.

m-01: the ops list endpoints used to pass raw limit/offset/days into
.offset().limit() / timedelta() — ?limit=10000000 forced an unbounded
.all() into memory and a huge days overflowed timedelta() into a 500.
clamp_limit / clamp_offset / clamp_days (ops.py) now bound them to
1..500 / >=0 / 1..365, and the same clamp guards run_check_document_expiry
(the cron job calls it directly, bypassing the HTTP wrapper).

m-10: POST /api/ops/action-items and /api/ops/documents accept an
Idempotency-Key header. The same (user, endpoint, key) twice creates
one row and replays the first response; a different key creates a
second row; no key keeps the old behaviour.
"""
import asyncio
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.auth import hash_password as make_hash
from src.database import get_db
from src.main import app
from src.models import (
    ActionItem,
    Base,
    BusinessUnit,
    IdempotencyKey,
    LicenseDocument,
    Notification,
    User,
)
from src.routers.ops import (
    clamp_days,
    clamp_limit,
    clamp_offset,
    run_check_document_expiry,
)
from src.utils import today_local

PW = "TestPass123!"
_PW_HASH = make_hash (PW)  # once per module: bcrypt cost 12 is slow


@pytest.fixture()
def http_db(tmp_path):
    db_path = tmp_path / "clamp_idem.db"
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([
        BusinessUnit(id=1, code="BU1", name="Shop One"),
        User(username="chairman", hashed_password=_PW_HASH,
             role="Chairman", is_active=True, require_password_change=False,
             is_profile_completed=True),
        User(username="ceo_one", hashed_password=_PW_HASH,
             role="CEO", business_unit_id=1, is_active=True,
             require_password_change=False, is_profile_completed=True),
    ])
    session.commit()
    yield session
    session.close()
    engine.dispose()


class ApiClient:
    def __init__(self, http_db):
        self._db = http_db
        self.cookie = None

    def request(self, method, url, **kw):
        headers = dict(kw.pop("headers", {}) or {})
        if self.cookie:
            headers["Cookie"] = self.cookie

        def _override():
            yield self._db

        app.dependency_overrides[get_db] = _override
        try:
            async def _go():
                async with httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app),
                        base_url="http://testserver") as ac:
                    return await ac.request(method, url, headers=headers, **kw)
            return asyncio.run(_go())
        finally:
            app.dependency_overrides.pop(get_db, None)

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, **kw):
        return self.request("POST", url, **kw)

    def login(self, username):
        r = self.post("/api/auth/login",
                      json={"username": username, "password": PW})
        assert r.status_code == 200, r.text
        self.cookie = r.headers.get("set-cookie", "").split(";")[0]
        return r


@pytest.fixture()
def client(http_db):
    return ApiClient(http_db)


@pytest.fixture()
def ceo(http_db):
    c = ApiClient(http_db)
    c.login("ceo_one")
    return c


@pytest.fixture()
def admin(http_db):
    c = ApiClient(http_db)
    c.login("chairman")
    return c


# ---------------------------------------------------------------- m-01: clamps

class TestClampHelpers:
    def test_limit_bounds(self):
        assert clamp_limit(200) == 200
        assert clamp_limit(0) == 1
        assert clamp_limit(-5) == 1
        assert clamp_limit(500) == 500
        assert clamp_limit(10000000) == 500

    def test_offset_bounds(self):
        assert clamp_offset(0) == 0
        assert clamp_offset(10) == 10
        assert clamp_offset(-3) == 0

    def test_days_bounds(self):
        assert clamp_days(30) == 30
        assert clamp_days(0) == 1
        assert clamp_days(-7) == 1
        assert clamp_days(365) == 365
        assert clamp_days(10**9) == 365


class TestClampHttp:
    def test_huge_limit_is_clamped_not_unbounded(self, ceo, http_db):
        # 3 rows exist; ?limit=10000000 must not 500 and must return them.
        for i in range(3):
            http_db.add(ActionItem(business_unit_id=1, title=f"item {i}",
                                   status="pending", created_by_id=2))
        http_db.commit()
        r = ceo.get("/api/ops/action-items?limit=10000000")
        assert r.status_code == 200, r.text
        assert len(r.json()["items"]) == 3

    def test_negative_limit_and_offset_clamped(self, ceo, http_db):
        http_db.add(ActionItem(business_unit_id=1, title="only",
                               status="pending", created_by_id=2))
        http_db.commit()
        r = ceo.get("/api/ops/action-items?limit=-1&offset=-5")
        assert r.status_code == 200, r.text
        assert len(r.json()["items"]) == 1

    def test_huge_days_clamped_on_staff_and_customers(self, ceo):
        assert ceo.get("/api/ops/staff?days=100000000").status_code == 200
        assert ceo.get("/api/ops/customers?days=100000000").status_code == 200
        assert ceo.get("/api/ops/documents?limit=999999&offset=-1").status_code == 200

    def test_huge_days_on_check_expiry_does_not_500(self, admin):
        # Pre-fix: timedelta(days=huge) overflowed -> 500.
        r = admin.post("/api/ops/documents/check-expiry",
                       params={"days": 10**9})
        assert r.status_code == 200, r.text
        assert r.json() == {"checked": 0, "notified": 0}

    def test_run_check_document_expiry_clamps_direct_calls(self, http_db):
        # The cron job calls run_* directly, bypassing the HTTP wrapper.
        assert run_check_document_expiry(http_db, days=10**9) == {
            "checked": 0, "notified": 0}


# ---------------------------------------------------------------- m-10: idempotency

class TestIdempotencyActionItems:
    def test_same_key_twice_creates_one_row_and_replays(self, ceo, http_db):
        headers = {"Idempotency-Key": "key-aaa-1"}
        payload = {"business_unit_id": 1, "title": "Chase supplier",
                   "assigned_to_id": 1, "due_date": today_local().isoformat()}
        r1 = ceo.post("/api/ops/action-items", json=payload, headers=headers)
        assert r1.status_code == 200, r1.text
        r2 = ceo.post("/api/ops/action-items", json=payload, headers=headers)
        assert r2.status_code == 200, r2.text
        assert r2.json() == r1.json()
        assert r2.json()["id"] == r1.json()["id"]
        assert http_db.query(ActionItem).count() == 1
        # The replay must not re-send the assignment notification.
        assert http_db.query(Notification).filter(
            Notification.type == "action_assigned").count() == 1
        assert http_db.query(IdempotencyKey).count() == 1

    def test_different_key_creates_second_row(self, ceo, http_db):
        payload = {"business_unit_id": 1, "title": "Chase supplier"}
        r1 = ceo.post("/api/ops/action-items", json=payload,
                      headers={"Idempotency-Key": "key-bbb-1"})
        r2 = ceo.post("/api/ops/action-items", json=payload,
                      headers={"Idempotency-Key": "key-bbb-2"})
        assert r1.status_code == 200 and r2.status_code == 200
        assert r1.json()["id"] != r2.json()["id"]
        assert http_db.query(ActionItem).count() == 2

    def test_no_key_keeps_old_behaviour(self, ceo, http_db):
        payload = {"business_unit_id": 1, "title": "No key item"}
        assert ceo.post("/api/ops/action-items", json=payload).status_code == 200
        assert ceo.post("/api/ops/action-items", json=payload).status_code == 200
        assert http_db.query(ActionItem).count() == 2
        assert http_db.query(IdempotencyKey).count() == 0

    def test_same_key_is_scoped_per_user(self, ceo, admin, http_db):
        # Chairman and CEO reusing the same key string get separate rows:
        # the dedupe scope is (user, endpoint, key).
        payload = {"business_unit_id": 1, "title": "Scoped item"}
        headers = {"Idempotency-Key": "shared-key"}
        assert ceo.post("/api/ops/action-items", json=payload,
                        headers=headers).status_code == 200
        assert admin.post("/api/ops/action-items", json=payload,
                          headers=headers).status_code == 200
        assert http_db.query(ActionItem).count() == 2

    def test_overlong_key_rejected(self, ceo, http_db):
        r = ceo.post("/api/ops/action-items",
                     json={"business_unit_id": 1, "title": "x"},
                     headers={"Idempotency-Key": "k" * 129})
        assert r.status_code == 400
        assert http_db.query(ActionItem).count() == 0


class TestIdempotencyDocuments:
    def _doc(self):
        return {"business_unit_id": 1, "title": "Trade licence",
                "expiry_date": (today_local() + timedelta(days=30)).isoformat()}

    def test_same_key_twice_creates_one_document(self, ceo, http_db):
        headers = {"Idempotency-Key": "doc-key-1"}
        r1 = ceo.post("/api/ops/documents", json=self._doc(), headers=headers)
        assert r1.status_code == 200, r1.text
        r2 = ceo.post("/api/ops/documents", json=self._doc(), headers=headers)
        assert r2.status_code == 200, r2.text
        assert r2.json() == r1.json()
        assert http_db.query(LicenseDocument).count() == 1

    def test_different_key_creates_second_document(self, ceo, http_db):
        assert ceo.post("/api/ops/documents", json=self._doc(),
                        headers={"Idempotency-Key": "doc-key-2"}).status_code == 200
        assert ceo.post("/api/ops/documents", json=self._doc(),
                        headers={"Idempotency-Key": "doc-key-3"}).status_code == 200
        assert http_db.query(LicenseDocument).count() == 2
