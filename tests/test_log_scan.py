"""Tests for the log-scan job (A-1 / R-6), src/jobs/log_scan.py.

The counting and staleness functions are pure and unit-tested directly;
main() is tested with a tmp log file and a private in-memory DB whose
SessionLocal is monkeypatched in (the same pattern as the scheduled
alerts tests in tests/test_bot_review_coverage_batch.py).

ERP parts were deleted with the ERP removal (2026-11-17): the
``journal_failed`` bucket, the ``reconcile_journals`` heartbeat watch,
and the audit-gap checks (check_audit_gaps) — all of them compared ERP
business rows against audit rows, and no ERP rows exist any more.
"""
import io
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import src.jobs.log_scan as log_scan
from src.models import Base, JobHeartbeat, Notification, User

NOW = datetime(2026, 11, 9, 12, 0, 0)


def _now():
    """Naive UTC now, matching src.utils.utcnow() (what main() uses for
    its window). R-1: log lines in TestMain must be generated relative
    to the real clock — hard-coded 2026-11-09 stamps fall outside
    main()'s 24 h window a week later and the tests fail."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _stamp(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S,000")


def _lines():
    base = _now() - timedelta(hours=2)
    return [
        f"{_stamp(base)} INFO business_ops_tracker: started",
        f"{_stamp(base + timedelta(minutes=1))} ERROR business_ops_tracker.error: Unhandled exception error_id=abcd",
        f"{_stamp(base + timedelta(minutes=3))} WARNING business_ops_tracker: LOGIN_LOCKOUT user=ceo_bu1",
        f"{_stamp(base + timedelta(minutes=4))} WARNING business_ops_tracker: account lockout triggered for ceo_bu1",
        "plain line with no timestamp at all",
    ]


class TestCountSignals:
    def test_counts_each_bucket(self):
        counts = log_scan.count_signals(_lines())
        assert counts == {"total": 5, "errors": 1, "lockouts": 2}

    def test_empty_input(self):
        assert log_scan.count_signals([]) == {"total": 0, "errors": 0, "lockouts": 0}

    def test_matching_is_case_insensitive_and_error_needs_word_boundary(self):
        counts = log_scan.count_signals([
            "some ERRORLESS word here",  # ERROR as a substring is not a level
            "ERROR: disk full",
        ])
        assert counts["errors"] == 1
        assert "journal_failed" not in counts  # bucket deleted with the ERP removal


class TestLinesInWindow:
    def test_old_stamped_lines_dropped_dateless_lines_kept(self):
        since = datetime(2026, 11, 9, 11, 0, 0)
        kept = log_scan.lines_in_window([
            "2026-11-09 10:00:00,000 INFO old",
            "2026-11-09 11:30:00,000 ERROR recent",
            "no timestamp here",
            "garbage 2026-13-99 99:99:99 not a date",
        ], since)
        assert kept == [
            "2026-11-09 11:30:00,000 ERROR recent",
            "no timestamp here",
            "garbage 2026-13-99 99:99:99 not a date",
        ]


class TestStaleJobs:
    def test_fresh_heartbeat_not_stale(self):
        assert log_scan.stale_jobs(
            [("scheduled_alerts", NOW - timedelta(hours=1)),
             ("log_scan", NOW - timedelta(hours=2))], now=NOW) == []

    def test_older_than_36h_is_stale(self):
        assert log_scan.stale_jobs(
            [("scheduled_alerts", NOW - timedelta(hours=37)),
             ("log_scan", NOW - timedelta(hours=1))], now=NOW) == ["scheduled_alerts"]

    def test_exactly_36h_is_not_stale(self):
        assert log_scan.stale_jobs(
            [("scheduled_alerts", NOW - timedelta(hours=36)),
             ("log_scan", NOW - timedelta(hours=1))], now=NOW) == []

    def test_job_that_never_ran_is_stale(self):
        # A dead-man check that only watches rows that exist cannot see a
        # cron that was never installed.
        assert log_scan.stale_jobs(
            [("log_scan", NOW - timedelta(hours=1))], now=NOW) == [
                "scheduled_alerts"]

    def test_missing_row_and_none_last_run(self):
        assert log_scan.stale_jobs(
            [("scheduled_alerts", None), ("log_scan", NOW)], now=NOW) == ["scheduled_alerts"]

    # G-4: backup is a weekly job (Sundays 03:30) with an 8-day grace,
    # like audit_retention — and, like it, a missing row is not stale
    # (between install and the first weekly run there is legitimately
    # no row yet).
    def test_backup_weekly_grace(self):
        pairs = [("audit_retention", NOW - timedelta(days=1)),
                 ("backup", NOW - timedelta(days=7))]
        assert log_scan.stale_jobs(
            pairs, now=NOW, expected=log_scan.WEEKLY_JOBS,
            never_ran_is_stale=False) == []

    def test_backup_stale_after_8_days(self):
        pairs = [("audit_retention", NOW - timedelta(days=1)),
                 ("backup", NOW - timedelta(days=8, hours=1))]
        assert log_scan.stale_jobs(
            pairs, now=NOW, expected=log_scan.WEEKLY_JOBS,
            never_ran_is_stale=False) == ["backup"]

    def test_backup_never_ran_is_not_stale(self):
        assert log_scan.stale_jobs(
            [("audit_retention", NOW - timedelta(days=1))], now=NOW,
            expected=log_scan.WEEKLY_JOBS, never_ran_is_stale=False) == []


@pytest.fixture()
def scan_db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(User(id=1, username="chairman", hashed_password="x",
                     role="Chairman", is_active=True))
    session.commit()
    yield engine, session
    session.close()
    engine.dispose()


def _fresh_db_session(scan_db):
    engine, _ = scan_db
    return sessionmaker(bind=engine)


def _seed_heartbeat(session, job, age_hours):
    # Relative to the real clock: main() compares against utcnow().
    # Pm-4: naive UTC — JobHeartbeat.last_run_at is a naive column and
    # stale_jobs() compares it against naive utcnow().
    session.merge(JobHeartbeat(job_name=job, last_run_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=age_hours),
                               last_status="ok"))
    session.commit()


def _notifications(engine):
    session = sessionmaker(bind=engine)()
    try:
        return session.query(Notification).all()
    finally:
        session.close()


def _heartbeat(engine, job):
    session = sessionmaker(bind=engine)()
    try:
        return session.query(JobHeartbeat).filter(JobHeartbeat.job_name == job).one_or_none()
    finally:
        session.close()


class TestMain:
    def test_clean_log_and_fresh_heartbeats_returns_zero(self, scan_db, tmp_path, monkeypatch, capsys):
        engine, session = scan_db
        _seed_heartbeat(session, "scheduled_alerts", 1)
        _seed_heartbeat(session, "log_scan", 1)
        # No backup row at all: weekly jobs only alert on a stale
        # existing row, never on a missing one (G-4).
        log_file = tmp_path / "app.log"
        log_file.write_text(f"{_stamp(_now() - timedelta(hours=1))} INFO business_ops_tracker: ok\n")
        monkeypatch.setattr(log_scan, "SessionLocal", _fresh_db_session(scan_db))
        assert log_scan.main(["--log-file", str(log_file), "--hours", "24"]) == 0
        assert _notifications(engine) == []
        assert "stale heartbeats: none" in capsys.readouterr().out

    def test_problems_notify_chairman_and_return_zero(self, scan_db, tmp_path, monkeypatch):
        engine, session = scan_db
        _seed_heartbeat(session, "scheduled_alerts", 40)  # stale
        _seed_heartbeat(session, "log_scan", 1)
        log_file = tmp_path / "app.log"
        log_file.write_text("\n".join(_lines()) + "\n")
        monkeypatch.setattr(log_scan, "SessionLocal", _fresh_db_session(scan_db))
        assert log_scan.main(["--log-file", str(log_file), "--hours", "24"]) == 0
        notes = _notifications(engine)
        assert len(notes) == 1
        assert notes[0].user_id == 1
        assert notes[0].type == "alert"
        assert "ERROR line(s)" in notes[0].message
        assert "scheduled_alerts" in notes[0].message
        # The scan upserted its own heartbeat while it was there.
        assert _heartbeat(engine, "log_scan") is not None

    # G-4: a stale backup heartbeat (weekly job, 8-day grace) must
    # reach the Chairman; a missing one must not alert yet.
    def test_stale_backup_heartbeat_notifies_chairman(self, scan_db, tmp_path, monkeypatch):
        engine, session = scan_db
        _seed_heartbeat(session, "scheduled_alerts", 1)
        _seed_heartbeat(session, "log_scan", 1)
        _seed_heartbeat(session, "backup", 9 * 24)  # stale (> 8 days)
        log_file = tmp_path / "app.log"
        log_file.write_text(f"{_stamp(_now() - timedelta(hours=1))} INFO ok\n")
        monkeypatch.setattr(log_scan, "SessionLocal", _fresh_db_session(scan_db))
        assert log_scan.main(["--log-file", str(log_file), "--hours", "24"]) == 0
        notes = _notifications(engine)
        assert len(notes) == 1
        assert "backup" in notes[0].message

    def test_missing_backup_heartbeat_is_not_stale(self, scan_db, tmp_path, monkeypatch, capsys):
        engine, session = scan_db
        _seed_heartbeat(session, "scheduled_alerts", 1)
        _seed_heartbeat(session, "log_scan", 1)
        log_file = tmp_path / "app.log"
        log_file.write_text(f"{_stamp(_now() - timedelta(hours=1))} INFO ok\n")
        monkeypatch.setattr(log_scan, "SessionLocal", _fresh_db_session(scan_db))
        assert log_scan.main(["--log-file", str(log_file), "--hours", "24"]) == 0
        assert _notifications(engine) == []
        assert "stale heartbeats: none" in capsys.readouterr().out

    def test_app_log_file_env_is_used(self, scan_db, tmp_path, monkeypatch):
        engine, session = scan_db
        _seed_heartbeat(session, "scheduled_alerts", 1)
        _seed_heartbeat(session, "log_scan", 1)
        log_file = tmp_path / "app.log"
        log_file.write_text(f"{_stamp(_now() - timedelta(hours=1))} INFO ok\n")
        monkeypatch.setenv("APP_LOG_FILE", str(log_file))
        monkeypatch.setattr(log_scan, "SessionLocal", _fresh_db_session(scan_db))
        assert log_scan.main([]) == 0

    def test_stdin_when_no_log_file(self, scan_db, monkeypatch):
        engine, session = scan_db
        _seed_heartbeat(session, "scheduled_alerts", 1)
        _seed_heartbeat(session, "log_scan", 1)
        monkeypatch.setattr("sys.stdin", io.StringIO(f"{_stamp(_now() - timedelta(hours=1))} INFO ok\n"))
        monkeypatch.setattr(log_scan, "SessionLocal", _fresh_db_session(scan_db))
        assert log_scan.main(["--log-file", "-"]) == 0

    def test_missing_log_file_returns_one(self, scan_db, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(log_scan, "SessionLocal", _fresh_db_session(scan_db))
        assert log_scan.main(["--log-file", str(tmp_path / "nope.log")]) == 1
        assert "cannot read log file" in capsys.readouterr().err

    def test_no_database_falls_back_to_stderr_and_nonzero(
            self, tmp_path, monkeypatch, capsys):
        log_file = tmp_path / "app.log"
        log_file.write_text(f"{_stamp(_now() - timedelta(hours=1))} ERROR something broke\n")

        def _raise():
            raise RuntimeError("db down")

        monkeypatch.setattr(log_scan, "SessionLocal", _raise)
        assert log_scan.main(["--log-file", str(log_file)]) == 1
        err = capsys.readouterr().err
        assert "ALERT" in err
        assert "ERROR line(s)" in err

    def test_no_chairman_means_stderr_and_nonzero(self, tmp_path, monkeypatch, capsys):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        session = sessionmaker(bind=engine)()
        _seed_heartbeat(session, "scheduled_alerts", 1)
        _seed_heartbeat(session, "log_scan", 1)
        session.commit()
        log_file = tmp_path / "app.log"
        log_file.write_text(f"{_stamp(_now() - timedelta(hours=1))} ERROR something broke\n")
        monkeypatch.setattr(log_scan, "SessionLocal", lambda: sessionmaker(bind=engine)())
        assert log_scan.main(["--log-file", str(log_file)]) == 1
        assert "no active Chairman" in capsys.readouterr().err
        session.close()
        engine.dispose()
