"""Test-suite environment guard.

The suite must never depend on ambient CI/local env: Settings() defaults
ENVIRONMENT to "production" and refuses to start without DATABASE_URL, so a
pytest run without those vars dies at collection with exit 2 (seen on CI
run 44033833796: the pytest step failed in ~3s with exactly that
RuntimeError). pytest loads this conftest before collecting any test
module, so the defaults are in place before any test imports src.

Existing values are never overridden (setdefault), so the postgres-check
workflow keeps its Postgres DATABASE_URL and normal local/CI runs are
unchanged. The config-guard tests are unaffected: they run Settings() in a
subprocess with a scrubbed environment of their own.
"""
import asyncio
import os

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DATABASE_URL", "sqlite://")

from src.auth import hash_password  # noqa: E402
from src.database import get_db  # noqa: E402
from src.main import app  # noqa: E402
from src.models import Base, User  # noqa: E402  (env guard above must run first)


@pytest.fixture()
def db():
    """Shared empty in-memory SQLite session (all tables created).

    m-23: the per-file ``db()`` fixtures were verbatim copies of this
    (``create_engine(\"sqlite://\")`` + ``create_all`` + ``sessionmaker``).
    Files that only need an empty DB use this directly; files that seed
    file-specific rows keep a small autouse seed fixture on top of it
    instead of redefining the engine boilerplate. ``session.test_engine``
    is attached because a few tests open a second session on the same
    engine (concurrency / isolation checks).
    """
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.test_engine = engine
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def http_db(tmp_path):
    """File-backed SQLite DB: FastAPI runs sync endpoints in a worker
    thread, so the in-memory engine (one DB per connection/thread) cannot
    be shared between the fixture thread and the endpoint thread.

    m-23: moved verbatim from tests/test_http_smoke.py (its only
    consumer); the seeded chairman is what the smoke tests log in as.
    """
    db_path = tmp_path / "http.db"
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(User(username="chairman", hashed_password=hash_password("Chairman123!"),
                     role="Chairman", is_active=True, require_password_change=False,
                     is_profile_completed=True))
    session.commit()
    yield session
    session.close()
    engine.dispose()


class ApiClient:
    """Shared sync HTTP client over the ASGI app (T-m1).

    One harness for every HTTP test: overrides ``get_db`` with the
    ``http_db`` session, drives the real app through httpx's
    ASGITransport (routing, middleware and serialization included),
    and keeps the login cookie across calls — each request builds a
    fresh AsyncClient, so the cookie is carried explicitly. An explicit
    ``Cookie`` header on a call always wins over the stored one. The
    override is popped in ``finally`` so a failed request cannot leak
    it into the next test.
    """

    def __init__(self, http_db, password=None):
        self._db = http_db
        self.cookie = None
        self._password = password

    def request(self, method, url, **kw):
        headers = dict(kw.pop("headers", {}) or {})
        if self.cookie and "Cookie" not in headers and "cookie" not in headers:
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

    def put(self, url, **kw):
        return self.request("PUT", url, **kw)

    def patch(self, url, **kw):
        return self.request("PATCH", url, **kw)

    def delete(self, url, **kw):
        return self.request("DELETE", url, **kw)

    def login(self, username, password=None):
        """Log in and store the session cookie for later calls."""
        password = password if password is not None else self._password
        assert password is not None, (
            "ApiClient.login() needs a password: pass one, or build the "
            "client with make_client(password=...)")
        r = self.post("/api/auth/login",
                      json={"username": username, "password": password})
        assert r.status_code == 200, r.text
        self.cookie = r.headers.get("set-cookie", "").split(";")[0]
        assert self.cookie.startswith("access_token="), r.text
        return r


@pytest.fixture()
def make_client(http_db):
    """Build a fresh ApiClient on the test's http_db.

    Tests that log in as two users at once need separate clients (one
    cookie each) — build each with ``make_client()`` instead of sharing
    the single ``client`` fixture.
    """
    def _make(password=None):
        return ApiClient(http_db, password=password)
    return _make


@pytest.fixture()
def client(make_client):
    """The shared HTTP client for the test's http_db (T-m1)."""
    return make_client()
