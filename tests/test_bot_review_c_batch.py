"""Review-C batch (2026-11-17 code quality + security review): tests proving
the fixes in workstream C. Each test class is named for its finding.
"""
import os
from pathlib import Path

import pytest

os.environ.setdefault("ENVIRONMENT", "test")


ROOT = Path(__file__).resolve().parent.parent


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

    def test_notify_adds_one_row(self, db):
        from src.models import Notification
        from src.utils import notify

        row = notify(db, 7, "alert", "Title", "Body", link="/admin")
        db.commit()
        saved = db.query(Notification).one()
        assert saved is row
        assert (saved.user_id, saved.type, saved.title, saved.message, saved.link) == (
            7, "alert", "Title", "Body", "/admin")

    def test_bu_lookup_maps_newest_active_ceo_wins(self, db):
        from src.models import BusinessUnit, User
        from src.utils import bu_lookup_maps

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

    def test_record_job_heartbeat_upserts_and_swallows_failure(self, db):
        from src.models import JobHeartbeat
        from src.utils import record_job_heartbeat

        record_job_heartbeat(db, "test_job", "ok")
        row = db.query(JobHeartbeat).filter_by(job_name="test_job").one()
        assert row.last_status == "ok"
        record_job_heartbeat(db, "test_job", "failed")
        assert db.query(JobHeartbeat).filter_by(job_name="test_job").one().last_status == "failed"
        db.close()
        # A broken session must not raise: the heartbeat never masks the job.
        record_job_heartbeat(db, "test_job", "ok")


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


# ---------------------------------------------------------------- m-12 (minor)
# The four hot-query indexes must exist in the MIGRATED schema, not just
# in the models (create_all would hide a models/migration drift).

class TestHotQueryIndexes:
    def test_migrated_schema_has_all_four_indexes(self, tmp_path):
        import sqlite3
        import subprocess
        import sys

        db_path = tmp_path / "idx.db"
        url = f"sqlite:///{db_path}"
        env = {**os.environ, "ENVIRONMENT": "test",
               "DATABASE_URL": url, "MIGRATION_DATABASE_URL": url}
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=600)
        assert result.returncode == 0, result.stderr
        conn = sqlite3.connect(db_path)
        try:
            have = {}
            for table in ("notifications", "action_items", "audit_logs", "users"):
                have[table] = {
                    row[1] for row in
                    conn.execute(f"PRAGMA index_list({table})")}
            assert "ix_notifications_user_read_created" in have["notifications"]
            assert "ix_action_items_status_due" in have["action_items"]
            assert "ix_audit_action_ts" in have["audit_logs"]
            assert "ix_users_business_unit_id" in have["users"]
            # And the composite really covers the queried columns, in order.
            cols = [row[2] for row in conn.execute(
                "PRAGMA index_info(ix_notifications_user_read_created)")]
            assert cols == ["user_id", "is_read", "created_at"]
        finally:
            conn.close()


# ---------------------------------------------------------------- M-07
# Admin overview: ~115+ queries per request -> one window-function query
# per latest-row table, one grouped missing-submissions query, MTD-bounded
# totals, one SUM query per day, and per-endpoint parts.

class TestAdminOverviewQueries:
    def _seed_23_bus(self, db):
        from datetime import date, timedelta
        from decimal import Decimal

        from src.models import (
            ActivityLog, BusinessUnit, DailyFinancial, HealthStatus,
            SocialLog, User,
        )
        from src.utils import today_local

        today = today_local()
        month_start = date(today.year, today.month, 1)
        days = [today - timedelta(days=2), today - timedelta(days=1), today]
        for i in range(1, 24):
            db.add(BusinessUnit(id=i, code=f"BU{i}", name=f"Unit {i}"))
        db.add(User(id=100, username="ceo_bu1", hashed_password="x",
                    role="CEO", business_unit_id=1, is_active=True))
        # A huge pre-month row: MTD totals must exclude it.
        db.add(DailyFinancial(business_unit_id=1, report_date=date(2025, 6, 15),
                              revenue=Decimal("1000000000"), expenses=Decimal("0"),
                              net_profit=Decimal("0"), cash_in_hand=Decimal("0")))
        for i in range(1, 24):
            for offset, d in enumerate(days):
                # BU23 submits nothing today (alerts + missing-submissions).
                if i == 23 and d == today:
                    continue
                db.add(DailyFinancial(
                    business_unit_id=i, report_date=d,
                    revenue=Decimal(str((offset + 1) * 1000 * i)),
                    expenses=Decimal(str(500 * i)),
                    net_profit=Decimal("0"), cash_in_hand=Decimal(str(50 * i)),
                    cash_flow=Decimal(str(100 * i)),
                    receivable=Decimal(str(10 * i)),
                    payable=Decimal(str(5 * i))))
                db.add(ActivityLog(business_unit_id=i, report_date=d))
            db.add(SocialLog(business_unit_id=i, report_date=today))
            db.add(HealthStatus(
                business_unit_id=i, report_date=today, daily_steps=8000,
                physical_health="Sick" if i == 2 else "Healthy"))
        db.commit()
        return today, month_start, days

    def _counted(self, engine, fn):
        from sqlalchemy import event

        count = {"n": 0}

        def _before(conn, cursor, statement, parameters, context, executemany):
            count["n"] += 1

        event.listen(engine, "before_cursor_execute", _before)
        try:
            result = fn()
        finally:
            event.remove(engine, "before_cursor_execute", _before)
        return result, count["n"]

    def test_overview_few_queries_and_correct_payload(self, db):
        from src.routers.admin import gather_admin_overview

        engine = db.test_engine
        today, month_start, days = self._seed_23_bus(db)
        ov, n = self._counted(engine, lambda: gather_admin_overview(db))
        # Was ~115+ sequential round-trips for 23 BUs (4 latest-row loops
        # + 23 missing-submission queries + ~12 aggregates + unbounded SUM).
        assert n <= 25, f"overview still runs {n} queries"
        # MTD totals exclude the 2025 row (revenue 1e9) and BU23's
        # missing today row; computed from the seed with the month bound
        # so the test stays correct on the 1st/2nd of a month too.
        expected_mtd = sum(
            (offset + 1) * 1000 * i
            for i in range(1, 24)
            for offset, d in enumerate(days)
            if not (i == 23 and d == today) and d >= month_start)
        assert ov["kpi"]["total_revenue"] == float(expected_mtd)
        assert ov["kpi"]["total_revenue"] < 1e9
        # Latest-row window queries: BU1's latest financial is today's row.
        bu1 = next(c for c in ov["comparison_data"] if c["bu_id"] == 1)
        assert bu1["revenue"] == 3000.0
        assert bu1["report_date"] == today.isoformat()
        # BU23 missed today: financial + activity alerts, and it is the
        # only BU in missing_submissions.
        assert {a["bu_id"] for a in ov["financial_alerts"]} == {23}
        assert {a["bu_id"] for a in ov["activity_alerts"]} == {23}
        assert [m["bu_id"] for m in ov["missing_submissions"]] == [23]
        assert ov["missing_submissions"][0]["missing_count"] >= 1
        assert ov["submission_compliance"]["submitted"] == 22
        assert ov["submission_compliance"]["total"] == 23
        # Wellbeing: BU2 is Sick -> red, and is the only critical CEO.
        bu2 = next(w for w in ov["wellbeing"] if w["bu_id"] == 2)
        assert bu2["risk"] == "red"
        assert [c["bu_id"] for c in ov["critical_ceos"]] == [2]
        # Trend: 14 labels; BU1 revenue series ends 1000/2000/3000.
        assert len(ov["trend_labels"]) == 14
        bu1_trend = next(t for t in ov["trend_by_bu"] if t["bu_id"] == 1)
        assert bu1_trend["revenue"][-3:] == [1000.0, 2000.0, 3000.0]
        assert bu1_trend["revenue"][:-3] == [0.0] * 11

    def test_parts_limit_work_and_queries(self, db):
        from src.routers.admin import gather_admin_overview

        engine = db.test_engine
        self._seed_23_bus(db)
        ops, n_ops = self._counted(
            engine, lambda: gather_admin_overview(db, parts={"operations"}))
        assert set(ops) == {"today", "today_display", "operations"}
        assert len(ops["operations"]) == 23
        assert n_ops <= 14, f"operations still runs {n_ops} queries"
        comp, n_comp = self._counted(
            engine, lambda: gather_admin_overview(db, parts={"trend", "comparison"}))
        assert set(comp) == {"today", "today_display", "trend_labels",
                             "trend_cash_flow_by_bu", "trend_by_bu",
                             "bar_categories", "bar_receivable", "bar_payable",
                             "comparison_data"}
        assert n_comp <= 15, f"comparison still runs {n_comp} queries"


# ---------------------------------------------------------------- M-10
# Admin Excel export: COUNT the merged keys first, 400 before any .all().

class TestAdminExportCap:
    def _seed_wide(self, db, n_rows=10001):
        from datetime import date, timedelta
        from decimal import Decimal

        from src.models import ActivityLog, BusinessUnit, DailyFinancial, User

        db.add(BusinessUnit(id=1, code="BU1", name="Unit 1"))
        admin = User(id=900, username="admin", hashed_password="x",
                     role="Admin", is_active=True)
        db.add(admin)
        base = date(2020, 1, 1)
        db.add_all([
            DailyFinancial(business_unit_id=1, report_date=base + timedelta(days=i),
                           revenue=Decimal("10"), expenses=Decimal("1"),
                           net_profit=Decimal("0"), cash_in_hand=Decimal("0"))
            for i in range(n_rows)
        ])
        db.add(ActivityLog(business_unit_id=1, report_date=base))
        db.commit()
        return admin, base

    def _counted(self, engine, fn):
        from sqlalchemy import event

        count = {"n": 0}

        def _before(conn, cursor, statement, parameters, context, executemany):
            count["n"] += 1

        event.listen(engine, "before_cursor_execute", _before)
        try:
            result = fn()
        finally:
            event.remove(engine, "before_cursor_execute", _before)
        return result, count["n"]

    def test_wide_range_400_without_loading_rows(self, db):
        from fastapi import HTTPException
        from starlette.requests import Request

        from src.routers.admin import export_excel

        engine = db.test_engine
        admin, base = self._seed_wide(db)
        request = Request({"type": "http", "headers": [], "client": ("8.8.8.8", 5555)})

        def _call():
            return export_excel(request=request, start_date="2020-01-01",
                                end_date="2099-12-31", user=admin, db=db)

        with pytest.raises(HTTPException) as exc:
            self._counted(engine, _call)
        assert exc.value.status_code == 400

    def test_wide_range_statement_count(self, db):
        from fastapi import HTTPException
        from starlette.requests import Request

        from src.routers.admin import export_excel

        engine = db.test_engine
        admin, base = self._seed_wide(db)
        request = Request({"type": "http", "headers": [], "client": ("8.8.8.8", 5555)})
        try:
            self._counted(
                engine,
                lambda: export_excel(request=request, start_date="2020-01-01",
                                     end_date="2099-12-31", user=admin, db=db))
            raise AssertionError("expected HTTPException 400")
        except HTTPException as exc:
            assert exc.status_code == 400
        # Re-run with a fresh counter to measure exactly what the 400 path
        # executes: 2 BU-lookup queries + 1 COUNT union. Any .all() would
        # add 4 more statements.
        _, n = self._counted(
            engine,
            lambda: pytest.raises(HTTPException, export_excel, request=request,
                                  start_date="2020-01-01", end_date="2099-12-31",
                                  user=admin, db=db))
        # pytest.raises inside _counted swallows the exception; n counts the
        # statements of that call.
        assert n == 3, f"400 path ran {n} statements (expected 3: lookups + COUNT)"

    def test_exactly_10000_rows_succeeds(self, db):
        from datetime import timedelta
        from io import BytesIO

        from openpyxl import load_workbook
        from starlette.requests import Request

        from src.routers.admin import export_excel

        admin, base = self._seed_wide(db)
        request = Request({"type": "http", "headers": [], "client": ("8.8.8.8", 5555)})
        end = base + timedelta(days=9999)
        import asyncio

        async def _drain(resp):
            chunks = []
            async for chunk in resp.body_iterator:
                chunks.append(chunk)
            return b"".join(chunks)

        response = export_excel(request=request, start_date=base.isoformat(),
                                end_date=end.isoformat(), user=admin, db=db)
        wb = load_workbook(BytesIO(asyncio.run(_drain(response))))
        sheet = wb.active
        assert sheet.max_row == 10001  # header + exactly 10,000 data rows


# ---------------------------------------------------------------- m-13
# Submit handlers must not hold a pooled connection (or row locks)
# while the R2 upload runs: validate -> db.close() -> upload -> writes.

class TestSubmitUploadHoldsNoSession:
    """m-13: the R2 upload must run with no pooled connection held.

    get_db checks a pooled connection out (SELECT 1) before the handler
    runs. The spy below records engine.pool.checkedout() during the
    upload — 0 means the pool slot is free, 1 means the upload is
    holding it (the old code also held the day's row locks). The test
    engine is file-backed with an explicit QueuePool so checkedout()
    is meaningful (in-memory SQLite uses a SingletonThreadPool).
    """

    PDF_BYTES = b"%PDF-1.4 fake pdf content"

    def _make_file_db(self, tmp_path):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import QueuePool

        from src.models import Base

        engine = create_engine(f"sqlite:///{tmp_path}/m13.db",
                               poolclass=QueuePool, pool_size=2, max_overflow=0)
        Base.metadata.create_all(engine)
        return engine, sessionmaker(bind=engine)()

    def _seed(self, db):
        from src.models import BusinessUnit, User

        db.add(BusinessUnit(id=1, code="BU1", name="Unit 1"))
        ceo = User(id=701, username="ceo_one", hashed_password="x",
                   role="CEO", business_unit_id=1, is_active=True)
        ho = User(id=702, username="ho_user", hashed_password="x",
                  role="HO", business_unit_id=1, is_active=True)
        db.add_all([ceo, ho])
        db.commit()
        return ceo, ho

    def _validated_session(self, engine, db):
        # get_db checks a pooled connection out with SELECT 1 before the
        # handler runs — reproduce that so the spy can tell whether the
        # upload still holds the session's connection.
        from sqlalchemy import text

        db.execute(text("SELECT 1"))
        assert engine.pool.checkedout() == 1

    def test_ceo_upload_runs_with_no_connection_held(self, tmp_path, monkeypatch):
        import io

        from fastapi import UploadFile
        from starlette.requests import Request

        import src.routers.ceo as ceo_router
        from src.models import ActivityLog, DailyFinancial
        from src.utils import today_local

        engine, db = self._make_file_db(tmp_path)
        ceo, _ho = self._seed(db)
        self._validated_session(engine, db)
        seen = {}
        ceo_router._rate_limiter.reset()

        def fake_upload(file_obj, filename):
            seen["checkedout"] = engine.pool.checkedout()
            return "https://bucket.r2.dev/activity-attachments/evidence.pdf"

        monkeypatch.setattr(ceo_router, "upload_attachment_or_503", fake_upload)
        request = Request({"type": "http", "headers": [], "client": ("8.8.8.8", 5555)})
        ceo_router.submit_report(
            request=request, report_date="", revenue="100000", cash_flow="0",
            receivable="0", payable="0", other_amount="", financial_other_notes="",
            meetings_attended="Standup", meetings_count="", site_visits="",
            key_decisions="", key_achievement="", next_day_schedule="",
            problems_today="", help_needed="", gym_percent="", swimming_percent="",
            cycle_percent="", activity_other_notes="", daily_steps="",
            heart_rate="", sleep_hours="", bp_systolic="", bp_diastolic="",
            stress_level="", energy_mood="", weight_kg="", is_sick="false",
            health_gym_percent=None, health_swimming_percent=None,
            health_cycle_percent=None, health_other_notes="", event_flag="false",
            meeting_flag="false", golf_flag="false", social_other_notes="",
            attachment=UploadFile(filename="evidence.pdf",
                                  file=io.BytesIO(self.PDF_BYTES)),
            user=ceo, db=db)
        assert seen == {"checkedout": 0}, "upload ran while a pooled connection was held"
        # The submit still succeeds end to end, with the URL stored.
        fin = db.query(DailyFinancial).filter(
            DailyFinancial.business_unit_id == 1,
            DailyFinancial.report_date == today_local()).one()
        assert float(fin.revenue) == 100000.0
        act = db.query(ActivityLog).filter(
            ActivityLog.business_unit_id == 1,
            ActivityLog.report_date == today_local()).one()
        assert act.document_url == "https://bucket.r2.dev/activity-attachments/evidence.pdf"

    def test_ho_upload_runs_with_no_connection_held(self, tmp_path, monkeypatch):
        import io

        from fastapi import UploadFile
        from starlette.requests import Request

        import src.routers.ho as ho_router
        from src.models import HeadOfficeActivityLog, HeadOfficeDailyFinancial
        from src.utils import today_local

        engine, db = self._make_file_db(tmp_path)
        _ceo, ho = self._seed(db)
        self._validated_session(engine, db)
        seen = {}
        ho_router._rate_limiter.reset()

        def fake_upload(file_obj, filename):
            seen["checkedout"] = engine.pool.checkedout()
            return "https://bucket.r2.dev/activity-attachments/ho.pdf"

        monkeypatch.setattr(ho_router, "upload_attachment_or_503", fake_upload)
        request = Request({"type": "http", "headers": [], "client": ("8.8.8.8", 5555)})
        ho_router.submit_report(
            request, revenue="100000", cash_flow="20000", receivable="5000",
            payable="3000", other_amount="1000", financial_other_notes="",
            meetings_attended="Standup", site_visits="", key_decisions="",
            next_day_schedule="", gym_percent="50", swimming_percent="",
            cycle_percent="", activity_other_notes="", daily_steps="8000",
            heart_rate="72", sleep_hours="7.5", bp_systolic="120",
            bp_diastolic="80", health_other_notes="", event_flag="true",
            meeting_flag="false", golf_flag="false", social_other_notes="",
            attachment=UploadFile(filename="evidence.pdf",
                                  file=io.BytesIO(self.PDF_BYTES)),
            user=ho, db=db)
        assert seen == {"checkedout": 0}, "upload ran while a pooled connection was held"
        fin = db.query(HeadOfficeDailyFinancial).filter(
            HeadOfficeDailyFinancial.report_date == today_local()).one()
        assert float(fin.revenue) == 100000.0
        act = db.query(HeadOfficeActivityLog).filter(
            HeadOfficeActivityLog.report_date == today_local()).one()
        assert act.document_url == "https://bucket.r2.dev/activity-attachments/ho.pdf"
