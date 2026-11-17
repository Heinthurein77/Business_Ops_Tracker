"""Workstream C (BOT real-time risk review): PM-2/PM-4, Pm-1/Pm-2/Pm-4,
Cm-5, PM-7, TZ regression tests.

The ERP report-equivalence tests from this file (trial balance, P&L,
balance sheet, consolidated reports, tax, budgets, bank reconciliation,
finance summary, cash flow, aging, customer statements, batch helpers,
audit-gap checks, journal export) were deleted with the ERP removal
(2026-11-17): the endpoints and the tables they read no longer exist.
What remains pins the tracker-side caps/defaults (PM-2/PM-4), the
crontab flock guards (Pm-1), the retention heartbeat (Pm-2), the
pool-wait counter (Cm-5), the utcnow cleanup (Pm-4), the deploy
script + TZ (PM-7/TZ), and the pool settings (PM-5).
"""

import asyncio
import os
import stat
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("ENVIRONMENT", "test")

from src.config import settings
from src.database import Base
from src.jobs.log_scan import (
    check_heartbeats, count_pool_signals, count_signals, stale_jobs,
)
from src.models import ActionItem, AuditLog, BusinessUnit, JobHeartbeat, User
from src.routers.ops import export_excel
from src.utils import utcnow

CHAIRMAN = SimpleNamespace(id=1, role="Chairman", business_unit_id=None,
                           username="chairman")


def _drain(resp):
    """Body bytes of a StreamingResponse (Starlette wraps sync bodies in
    an async iterator, so a plain b"".join(resp.body_iterator) fails)."""
    async def _collect():
        chunks = []
        async for chunk in resp.body_iterator:
            chunks.append(chunk)
        return b"".join(chunks)
    return asyncio.run(_collect())


@pytest.fixture(autouse=True)
def _seed_db(db):
    """Minimal tracker seed: two users + two BUs (the journal /
    document seeding died with the ERP report tests)."""
    db.add_all([
        User(id=1, username="chairman", hashed_password="x", role="Chairman",
             is_active=True, token_version=0),
        User(id=2, username="aye", hashed_password="x", full_name="Aye Aye",
             role="CEO", business_unit_id=1, is_active=True, token_version=0),
    ])
    db.add_all([
        BusinessUnit(id=1, code="BU1", name="Shop One"),
        BusinessUnit(id=2, code="BU2", name="Shop Two"),
    ])
    db.commit()


class TestOpsExport:
    def _seed_ops_rows(self, db):
        now = utcnow()
        db.add_all([
            ActionItem(business_unit_id=1, title="Fresh action", assigned_to_id=2,
                       status="pending", created_at=now),
            ActionItem(business_unit_id=1, title="Old action", assigned_to_id=2,
                       status="pending", created_at=now - timedelta(days=200)),
        ])
        db.commit()

    def _xlsx_rows(self, resp):
        from openpyxl import load_workbook
        from io import BytesIO
        body = _drain(resp)
        wb = load_workbook(BytesIO(body), read_only=True)
        return list(wb.active.iter_rows(values_only=True))

    def test_actions_default_90_days_and_names(self, db):
        self._seed_ops_rows(db)
        rows = self._xlsx_rows(export_excel(tab="actions", user=CHAIRMAN, db=db))
        titles = [r[0] for r in rows[1:]]
        assert "Fresh action" in titles
        assert "Old action" not in titles  # PM-4: 90-day default
        assert any(r[4] == "Aye Aye" for r in rows[1:])  # PM-3: batched names

    def test_actions_wide_range_includes_old(self, db):
        self._seed_ops_rows(db)
        rows = self._xlsx_rows(export_excel(
            tab="actions", from_date=date(2026, 1, 1), user=CHAIRMAN, db=db))
        assert "Old action" in [r[0] for r in rows[1:]]

    def test_export_row_cap(self, db, monkeypatch):
        self._seed_ops_rows(db)
        rows = self._xlsx_rows(export_excel(tab="actions", limit=1, user=CHAIRMAN, db=db))
        assert len(rows) == 2  # header + 1
        import src.routers.ops as ops
        monkeypatch.setattr(ops, "OPS_EXPORT_MAX_ROWS", 1)
        rows = self._xlsx_rows(export_excel(tab="actions", limit=5000, user=CHAIRMAN, db=db))
        assert len(rows) == 2  # clamped to the (patched) max


# ------------------------------------------------------------------ Pm-1 / Pm-2 / Pm-3 / Pm-4


class TestCrontabFlock:
    def test_every_line_is_flock_guarded(self):
        repo = Path(__file__).resolve().parents[1]
        lines = [l for l in (repo / "deploy" / "crontab").read_text().splitlines()
                 if l.strip() and not l.strip().startswith("#")]
        assert len(lines) == 4  # Pm-1: 4 cron lines (the reconcile_journals line
        # was deleted with the ERP removal, 2026-11-17)
        assert not any("reconcile_journals" in l for l in lines)
        for line in lines:
            assert "flock -E 99 -n" in line
            assert "already running" in line
            assert "exit 0" in line  # a skip is not a failure

    def test_header_documents_the_pattern(self):
        repo = Path(__file__).resolve().parents[1]
        text = (repo / "deploy" / "crontab").read_text()
        assert "flock" in text.splitlines()[0] or "flock" in text


class TestRetentionHeartbeat:
    def _seed_file_db(self, path):
        engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(engine)
        session = sessionmaker(bind=engine)()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        session.add(AuditLog(action="LOGIN_SUCCESS", username="ceo_one",
                             ip_address="1.2.3.4", timestamp=now - timedelta(days=100)))
        session.commit()
        session.close()
        engine.dispose()
        return f"sqlite:///{path}"

    def test_real_run_writes_heartbeat(self, tmp_path, monkeypatch):
        from src.scripts.audit_retention import run
        url = self._seed_file_db(tmp_path / "audit.db")
        monkeypatch.setattr(settings, "MIGRATION_DATABASE_URL", url)
        deleted, cleared = run(90, 30, dry_run=False)
        assert (deleted, cleared) == (1, 0)
        engine = create_engine(url)
        try:
            session = sessionmaker(bind=engine)()
            row = session.query(JobHeartbeat).filter(
                JobHeartbeat.job_name == "audit_retention").one()
            assert row.last_status == "ok"
            assert row.last_run_at is not None
            # Fresh row: the weekly job is not stale (the daily jobs never
            # ran in this file DB, so they are legitimately flagged).
            assert "audit_retention" not in check_heartbeats(session)
        finally:
            engine.dispose()

    def test_dry_run_writes_no_heartbeat(self, tmp_path, monkeypatch):
        from src.scripts.audit_retention import run
        url = self._seed_file_db(tmp_path / "audit.db")
        monkeypatch.setattr(settings, "MIGRATION_DATABASE_URL", url)
        run(90, 30, dry_run=True)
        engine = create_engine(url)
        try:
            session = sessionmaker(bind=engine)()
            assert session.query(JobHeartbeat).filter(
                JobHeartbeat.job_name == "audit_retention").count() == 0
        finally:
            engine.dispose()


class TestLongReportTimeout:

    def test_weekly_heartbeat_grace(self, db):
        # Pm-2: audit_retention is weekly — a missing row is NOT stale
        # between install and the first Sunday run; a stale row IS.
        from src.jobs.log_scan import JOB_MAX_AGE_HOURS
        now = utcnow()
        assert stale_jobs([], now=now, expected=("audit_retention",),
                          never_ran_is_stale=False) == []
        old = now - timedelta(hours=JOB_MAX_AGE_HOURS["audit_retention"] + 1)
        assert stale_jobs([("audit_retention", old)], now=now,
                          expected=("audit_retention",),
                          never_ran_is_stale=False) == ["audit_retention"]
        fresh = now - timedelta(hours=1)
        assert stale_jobs([("audit_retention", fresh)], now=now,
                          expected=("audit_retention",),
                          never_ran_is_stale=False) == []


class TestNoUtcnow:
    def test_no_datetime_utcnow_call_sites(self):
        # Pm-4: the literal is built by concatenation so this file itself
        # does not contain the call it forbids.
        needle = "datetime.utcnow" + "("
        repo = Path(__file__).resolve().parents[1]
        hits = []
        for root in (repo / "src", repo / "tests"):
            for path in root.rglob("*.py"):
                if needle in path.read_text():
                    hits.append(str(path.relative_to(repo)))
        assert hits == []


# ------------------------------------------------------------------ Cm-5 / SM-14


class TestPoolSignals:
    def test_counts_pool_waits_and_lock_timeouts(self):
        lines = [
            "2026-11-09 ERROR QueuePool limit 10 overflow 5 reached, connection timed out",
            "sqlalchemy.exc.TimeoutError: QueuePool ... pool timeout",
            "503 Database unavailable — please retry shortly",
            "psycopg2.errors.LockNotAvailable: lock timeout",
            "ERROR: deadlock detected (40P01)",
            "could not obtain lock on row (55P03)",
            "INFO request ok",
        ]
        assert count_pool_signals(lines) == {"pool_waits": 3, "lock_timeouts": 3}
        assert count_pool_signals(["INFO all quiet"]) == {"pool_waits": 0, "lock_timeouts": 0}

    def test_count_signals_shape_unchanged(self):
        # Cm-5 kept the pool counter separate: the old dict is pinned.
        assert count_signals(["ERROR disk full", "LOGIN_LOCKOUT x"]) == {
            "total": 2, "errors": 1, "lockouts": 1}


# ------------------------------------------------------------------ PM-5 / PM-7 / TZ


class TestPoolAndDeploy:
    def test_pool_settings(self):
        # PM-5: modest raise once reports were cheap — 5+5 per instance,
        # 10 per instance, 20 peak across two instances of a ~22-connection
        # budget. pool_pre_ping stays (the SELECT 1 probe is pinned by
        # TestGetDbRetry and keeps the one-retry path).
        repo = Path(__file__).resolve().parents[1]
        text = (repo / "src" / "database.py").read_text()
        assert "pool_size=5" in text
        assert "max_overflow=5" in text
        assert '"pool_pre_ping": True' in text

    def test_deploy_script(self):
        repo = Path(__file__).resolve().parents[1]
        script = repo / "scripts" / "deploy.sh"
        assert script.exists()
        assert script.stat().st_mode & stat.S_IXUSR  # executable
        text = script.read_text()
        assert "stop app" in text
        assert "run --rm" in text and "migrate" in text
        assert "up -d" in text
        assert "/readyz" in text
        assert "downtime" in text.lower()

    def test_timezone_defence_in_depth(self):
        repo = Path(__file__).resolve().parents[1]
        assert "TZ=Asia/Yangon" in (repo / "Dockerfile").read_text()
        compose = (repo / "docker-compose.prod.yml").read_text()
        assert compose.count("TZ: Asia/Yangon") >= 2  # app + migrate services
        assert "scripts/deploy.sh" in compose
