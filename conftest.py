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
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DATABASE_URL", "sqlite://")

from src.auth import hash_password  # noqa: E402
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
