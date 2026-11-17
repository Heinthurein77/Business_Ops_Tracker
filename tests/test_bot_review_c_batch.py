"""Review-C batch (2026-11-17 code quality + security review): tests proving
the fixes in workstream C. Each test class is named for its finding.
"""
import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("ENVIRONMENT", "test")

from src.models import Base  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _make_db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    return engine, session


# ---------------------------------------------------------------- M-12 / m-21
# Shared helpers live in src/utils.py (not copied per router), and the
# run_* alert jobs live in src/services/ops_alerts.py (not in a router).

class TestSharedHelpers:
    def test_parse_bool(self):
        from src.utils import parse_bool

        for raw in ("true", "True", "1", "yes", "on", "  YES  "):
            assert parse_bool(raw) is True, raw
        for raw in (None, "", "false", "0", "no", "off", "maybe"):
            assert parse_bool(raw) is False, raw

    def test_parse_month_or_default(self):
        from datetime import date

        from src.utils import parse_month_or_default

        today = date(2026, 11, 17)
        assert parse_month_or_default("2026-03", today) == (2026, 3)
        assert parse_month_or_default("", today) == (2026, 11)
        assert parse_month_or_default("garbage", today) == (2026, 11)
        assert parse_month_or_default("2026-13", today) == (2026, 11)
        assert parse_month_or_default("9999-12", today) == (2026, 11)
        assert parse_month_or_default("1999-01", today) == (2026, 11)

    def test_notify_adds_one_row(self):
        from src.models import Notification
        from src.utils import notify

        _, db = _make_db()
        row = notify(db, 7, "alert", "Title", "Body", link="/admin")
        db.commit()
        saved = db.query(Notification).one()
        assert saved is row
        assert (saved.user_id, saved.type, saved.title, saved.message, saved.link) == (
            7, "alert", "Title", "Body", "/admin")
        db.close()

    def test_bu_lookup_maps_newest_active_ceo_wins(self):
        from src.models import BusinessUnit, User
        from src.utils import bu_lookup_maps

        _, db = _make_db()
        db.add_all([
            BusinessUnit(id=1, code="BU1", name="One"),
            BusinessUnit(id=2, code="BU2", name="Two"),
            User(id=1, username="old", hashed_password="x", role="CEO",
                 business_unit_id=1, is_active=False),
            User(id=2, username="first", hashed_password="x", role="CEO",
                 business_unit_id=1, is_active=True),
            User(id=4, username="other", hashed_password="x", role="CEO",
                 business_unit_id=2, is_active=True),
        ])
        db.commit()
        bus, bu_by_id, ceo_by_bu = bu_lookup_maps(db)
        assert [b.id for b in bus] == [1, 2]
        assert bu_by_id[2].code == "BU2"
        # A deactivated predecessor never shadows the current CEO.
        # (Two ACTIVE CEOs can never share a unit — the partial unique
        # index uq_users_one_active_ceo_per_bu forbids it — so the
        # "newest wins" ordering only ever picks among actives.)
        assert ceo_by_bu[1].username == "first"
        assert ceo_by_bu[2].username == "other"
        db.close()

    def test_submit_rate_limiter(self):
        import time

        from src.utils import SubmitRateLimiter

        limiter = SubmitRateLimiter(limit=2, window_seconds=60)
        assert limiter.is_limited(9) is False
        assert limiter.is_limited(9) is False
        assert limiter.is_limited(9) is True
        assert limiter.is_limited(10) is False  # per-user budget
        fast = SubmitRateLimiter(limit=1, window_seconds=0.05)
        assert fast.is_limited(1) is False
        assert fast.is_limited(1) is True
        time.sleep(0.06)
        assert fast.is_limited(1) is False

    def test_ceo_and_ho_routers_share_the_limiter(self):
        # The routers keep their own instances (separate budgets) but the
        # mechanism is the shared class, not two copies.
        from src.routers import ceo as ceo_router
        from src.routers import ho as ho_router
        from src.utils import SubmitRateLimiter

        assert isinstance(ceo_router._rate_limiter, SubmitRateLimiter)
        assert isinstance(ho_router._rate_limiter, SubmitRateLimiter)
        assert ceo_router._rate_limiter is not ho_router._rate_limiter

    def test_record_job_heartbeat_upserts_and_swallows_failure(self):
        from src.models import JobHeartbeat
        from src.utils import record_job_heartbeat

        _, db = _make_db()
        record_job_heartbeat(db, "test_job", "ok")
        row = db.query(JobHeartbeat).filter_by(job_name="test_job").one()
        assert row.last_status == "ok"
        record_job_heartbeat(db, "test_job", "failed")
        assert db.query(JobHeartbeat).filter_by(job_name="test_job").one().last_status == "failed"
        db.close()
        # A broken session must not raise: the heartbeat never masks the job.
        record_job_heartbeat(db, "test_job", "ok")
        db.close()


class TestOpsAlertsService:
    def test_run_functions_live_in_services_not_the_router(self):
        import src.jobs.scheduled_alerts as sched
        import src.routers.ops as ops_router
        import src.services.ops_alerts as svc

        for name in ("run_remind_missing_reports", "run_remind_action_items",
                     "run_check_document_expiry"):
            # The service module defines them; the router re-exports the
            # same objects; the job imports them from the service.
            assert getattr(svc, name) is getattr(ops_router, name)
            assert getattr(sched, name) is getattr(svc, name)
        assert "src.routers.ops" not in sched.__dict__.get("__doc__", "")

    def test_no_router_import_in_services_module(self):
        # The point of the move: the job no longer drags the HTTP layer in.
        # Asserted on the import lines so a future import can't sneak back
        # (the module docstring legitimately names the old location).
        src = (ROOT / "src" / "services" / "ops_alerts.py").read_text()
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")):
                assert "routers" not in stripped, stripped
                assert "fastapi" not in stripped
                assert "r2_storage" not in stripped

class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _FakeConn:
    """Stands in for a DBAPI connection: SHOW returns the configured values."""
    def __init__(self, values):
        self._values = values
        self.asked = []

    def execute(self, stmt):
        name = str(stmt).split()[-1]
        self.asked.append(name)
        return _FakeResult(self._values.get(name))


class TestPoolerTimeoutWarning:
    def test_pooler_host_detected(self):
        from src.database import is_pooler_database_url

        assert is_pooler_database_url(
            "postgresql+psycopg2://u:p@ep-foo-pooler.us-east-2.aws.neon.tech/db")
        assert is_pooler_database_url(
            "postgresql+psycopg2://u:p@host.example.com:25061/db")
        assert not is_pooler_database_url(
            "postgresql+psycopg2://u:p@ep-foo.us-east-2.aws.neon.tech/db")
        assert not is_pooler_database_url("sqlite://")

    def test_unset_timeouts_reported_and_warned(self, caplog):
        import logging

        from src.database import check_pooler_timeouts

        with caplog.at_level(logging.WARNING, logger="business_ops_tracker.database"):
            missing = check_pooler_timeouts(
                _FakeConn({"statement_timeout": "0", "lock_timeout": "0"}))
        assert missing == ["statement_timeout", "lock_timeout"]
        assert "pooler" in caplog.text and "statement_timeout" in caplog.text

    def test_set_timeouts_are_quiet(self, caplog):
        import logging

        from src.database import check_pooler_timeouts

        with caplog.at_level(logging.WARNING, logger="business_ops_tracker.database"):
            missing = check_pooler_timeouts(
                _FakeConn({"statement_timeout": "15s", "lock_timeout": "5s"}))
        assert missing == []
        assert "pooler" not in caplog.text

    def test_verify_is_noop_for_non_pooler_url(self):
        from src.database import verify_pooler_timeouts

        # The test settings use SQLite, so this must return [] without
        # touching any connection.
        assert verify_pooler_timeouts() == []

    def test_env_example_prefers_direct_neon_host(self):
        text = (ROOT / ".env.example").read_text()
        assert "prefer the DIRECT Neon host" in text
        assert "deploy/db_roles.sql" in text


# ---------------------------------------------------------------- m-16
# /readyz must use its own 1-connection pool so probe checkouts can never
# eat the main pool.

class TestReadyzProbePool:
    def test_probe_engine_is_separate_and_bounded(self):
        from src.database import build_probe_engine, engine, probe_engine

        assert probe_engine is not engine
        probe = build_probe_engine(
            "postgresql+psycopg2://u:p@db.example.com:5432/app",
            {"connect_timeout": 10})
        assert probe.pool.size() == 1
        assert probe.pool._max_overflow == 0
        assert probe.pool._timeout == 3
        probe.dispose()

    def test_db_is_ready_uses_probe_engine(self, monkeypatch):
        import src.main as main_mod

        seen = {}

        class _Conn:
            def __enter__(self):
                seen["used"] = True
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, stmt):
                return None

        class _Probe:
            def connect(self):
                return _Conn()

        monkeypatch.setattr(main_mod, "probe_engine", _Probe())
        assert main_mod._db_is_ready() is True
        assert seen.get("used") is True


# ---------------------------------------------------------------- m-25
# Two routers share the /api/admin prefix; a future (path, method)
# collision must fail fast at startup instead of silently shadowing.

class TestUniqueRoutes:
    def test_live_app_has_no_duplicate_routes(self):
        from fastapi import FastAPI

        from src.main import _assert_unique_routes, app

        _assert_unique_routes(app)  # must not raise
        assert isinstance(app, FastAPI)

    def test_duplicate_route_is_rejected(self):
        from fastapi import FastAPI

        from src.main import _assert_unique_routes

        dup = FastAPI()

        @dup.get("/api/admin/users")
        def _a():
            return {}

        @dup.get("/api/admin/users")
        def _b():
            return {}

        with pytest.raises(RuntimeError, match="duplicate API routes"):
            _assert_unique_routes(dup)

    def test_same_path_different_method_is_allowed(self):
        from fastapi import FastAPI

        from src.main import _assert_unique_routes

        ok = FastAPI()

        @ok.get("/api/ops/documents")
        def _a():
            return {}

        @ok.post("/api/ops/documents")
        def _b():
            return {}

        _assert_unique_routes(ok)  # must not raise

class TestDeployConfigReviewC:
    def test_dockerfile_limit_concurrency_matches_pool(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        assert "--limit-concurrency 20" in dockerfile
        # Pool total is 10 (5+5); 20 is the documented headroom, not 100.
        assert "--limit-concurrency 100" not in dockerfile
        assert "pool is 5+5=10" in dockerfile

    def test_caddy_health_aware_routing(self):
        caddyfile = (ROOT / "Caddyfile").read_text()
        assert "health_uri /readyz" in caddyfile
        assert "health_interval" in caddyfile
        assert "reverse_proxy app:8000" in caddyfile

    def test_compose_caddy_waits_for_healthy_app(self):
        # No PyYAML in requirements.txt, so assert on the text: the caddy
        # service must depend on app with condition service_healthy.
        compose = (ROOT / "docker-compose.prod.yml").read_text()
        caddy = compose[compose.index("  caddy:"):]
        assert "depends_on:\n      app:\n        condition: service_healthy" in caddy

    def test_compose_log_rotation_on_all_services(self):
        compose = (ROOT / "docker-compose.prod.yml").read_text()
        block = ('    logging:\n      driver: json-file\n      options:\n'
                 '        max-size: "10m"\n        max-file: "3"')
        assert compose.count(block) == 3, (
            "expected the 10 MB x 3 logging block on app, caddy and migrate")
