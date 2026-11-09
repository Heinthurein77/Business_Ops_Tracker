"""Daily log-scan job (A-1): count failure signals in the app logs and
watch the scheduled jobs' dead-man heartbeats.

Reads a log file (``APP_LOG_FILE`` env, ``--log-file``, or stdin with
``-``), counts ``ERROR`` / ``journal failed`` / lockout lines over a
window (default: the last 24 h), and checks ``job_heartbeats`` for stale
rows (> 36 h = dead man, or a job that never wrote a row).

Run once daily from cron on the server, e.g.:

    # 7:00 AM every day (server time): scan the last 24 h of app logs.
    # The app logs to stdout, so the cron pipes `docker compose logs`
    # into the job's stdin; the job itself runs inside the container so
    # it can reach the database and notify the Chairman.
    0 7 * * * cd ~/business-ops-tracker && docker compose -f docker-compose.prod.yml logs --since 24h app 2>/dev/null | docker compose -f docker-compose.prod.yml exec -T app python -m src.jobs.log_scan >> ~/bot-logscan.log 2>&1

Alerting: when the database is reachable, the Chairman is notified with
an in-app Notification row (the same mechanism scheduled_alerts uses —
there is no separate notification service in this codebase). When the
database is NOT reachable, the alert goes to stderr and the job exits
non-zero so cron's own mail / log shows it.

Exit codes: 0 = clean, or alerts were delivered as Notifications;
1 = alerts found but nobody could be notified (no DB / no Chairman),
or the log file could not be read.
"""
import argparse
import os
import re
import sys
from datetime import datetime, timedelta
from typing import Iterable

from src.database import SessionLocal
from src.models import JobHeartbeat, Notification, User

# A job whose heartbeat is older than this is presumed dead.
STALE_AFTER_HOURS = 36
# Jobs expected to write a heartbeat every run. audit_retention is NOT
# listed: it is a weekly script that never upserts a heartbeat, so
# expecting one would alert every day.
EXPECTED_JOBS = ("scheduled_alerts", "log_scan")

_ERROR_RE = re.compile(r"\bERROR\b")
_TIMESTAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})")


# ------------------------------------------------------------------ pure bits

def count_signals(lines: Iterable[str]) -> dict:
    """Count failure signals in log lines. Pure: no I/O, no clock, no DB.

    Returns {"total", "errors", "journal_failed", "lockouts"}. A line can
    count in more than one bucket (an ERROR line about a journal failure
    is both). Matching is deliberately textual: the app's failure sites
    log ``logger.warning("PO journal failed ...")`` /
    ``logger.error(...)`` and the throttle writes ``LOGIN_LOCKOUT`` audit
    rows, so "journal failed" and "lockout" (case-insensitive) are the
    signals the review (A-1) names.
    """
    counts = {"total": 0, "errors": 0, "journal_failed": 0, "lockouts": 0}
    for line in lines:
        counts["total"] += 1
        lowered = line.lower()
        if _ERROR_RE.search(line):
            counts["errors"] += 1
        if "journal failed" in lowered:
            counts["journal_failed"] += 1
        if "lockout" in lowered:
            counts["lockouts"] += 1
    return counts


def _parse_timestamp(line: str):
    """Leading ``YYYY-MM-DD HH:MM:SS`` (the app's log format), or None."""
    match = _TIMESTAMP_RE.match(line)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def lines_in_window(lines: Iterable[str], since: datetime) -> list[str]:
    """Keep lines from the window. Lines with no parseable timestamp are
    kept — their age can't be judged, and dropping them would silently
    hide failures. (The recommended cron already bounds the input with
    `docker compose logs --since 24h`, so this is a second net.)"""
    kept = []
    for line in lines:
        stamped = _parse_timestamp(line)
        if stamped is None or stamped >= since:
            kept.append(line)
    return kept


def stale_jobs(heartbeats, now=None, max_age_hours: int = STALE_AFTER_HOURS) -> list[str]:
    """Names of expected jobs whose heartbeat is stale. Pure.

    ``heartbeats`` is an iterable of (job_name, last_run_at) with
    last_run_at None when the job never wrote a row. A job that never
    ran is stale: a dead-man check that only watches rows that exist
    cannot see a cron that was never installed.
    """
    now = now or datetime.utcnow()
    seen = {}
    for job_name, last_run_at in heartbeats:
        seen[job_name] = last_run_at
    stale = []
    for job_name in EXPECTED_JOBS:
        last = seen.get(job_name)
        if last is None or (now - last) > timedelta(hours=max_age_hours):
            stale.append(job_name)
    return stale


# ------------------------------------------------------------------ DB bits

def check_heartbeats(db, now=None) -> list[str]:
    """Stale-job names from the database's heartbeat table."""
    rows = db.query(JobHeartbeat).all()
    return stale_jobs([(row.job_name, row.last_run_at) for row in rows], now=now)


def _record_heartbeat(db, job_name: str, status: str) -> None:
    """Best effort: a heartbeat write must not fail the scan itself."""
    try:
        db.merge(JobHeartbeat(job_name=job_name, last_run_at=datetime.utcnow(), last_status=status))
        db.commit()
    except Exception as exc:  # noqa: BLE001 - heartbeat must not fail the job
        db.rollback()
        print(f"heartbeat update failed for {job_name}: {exc}", file=sys.stderr)


def _notify_chairman(db, title: str, message: str) -> bool:
    """One in-app Notification per active Chairman. Returns True when at
    least one Chairman exists to notify."""
    chairmen = db.query(User).filter(User.role == "Chairman", User.is_active == True).all()  # noqa: E712
    for chairman in chairmen:
        db.add(Notification(user_id=chairman.id, type="alert", title=title, message=message, link="/admin"))
    if chairmen:
        db.commit()
    return bool(chairmen)


# ------------------------------------------------------------------ entry point

def _read_lines(log_file: str) -> list[str]:
    if log_file == "-":
        return sys.stdin.read().splitlines()
    with open(log_file, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read().splitlines()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--log-file", default=None,
                        help="log file to scan (default: $APP_LOG_FILE, else stdin)")
    parser.add_argument("--hours", type=int, default=24,
                        help="only count lines from the last N hours (default 24)")
    args = parser.parse_args(argv)
    if args.hours < 1:
        parser.error("--hours must be at least 1")

    log_file = args.log_file or os.getenv("APP_LOG_FILE") or "-"
    try:
        raw_lines = _read_lines(log_file)
    except OSError as exc:
        print(f"log_scan: cannot read log file {log_file!r}: {exc}", file=sys.stderr)
        return 1

    since = datetime.utcnow() - timedelta(hours=args.hours)
    counts = count_signals(lines_in_window(raw_lines, since))

    # Dead-man check + own heartbeat. A DB failure here must not kill the
    # scan: the log counts are still worth reporting, and the fallback
    # (stderr + non-zero exit) is the alert path.
    stale: list[str] = []
    db_available = False
    db = None
    try:
        db = SessionLocal()
        stale = check_heartbeats(db)
        _record_heartbeat(db, "log_scan", "ok")
        db_available = True
    except Exception as exc:  # noqa: BLE001 - DB is optional for the scan
        print(f"log_scan: heartbeat check unavailable ({exc}); continuing on log counts only",
              file=sys.stderr)
        if db is not None:
            try:
                db.close()
            except Exception:
                pass
            db = None

    problems = []
    if counts["errors"]:
        problems.append(f"{counts['errors']} ERROR line(s)")
    if counts["journal_failed"]:
        problems.append(f"{counts['journal_failed']} journal-failure line(s)")
    if counts["lockouts"]:
        problems.append(f"{counts['lockouts']} lockout line(s)")
    if stale:
        problems.append(f"stale job heartbeat(s): {', '.join(stale)} (>{STALE_AFTER_HOURS}h or never ran)")

    summary = (
        f"log_scan: {counts['total']} line(s) in window, {counts['errors']} ERROR, "
        f"{counts['journal_failed']} journal-failed, {counts['lockouts']} lockout; "
        f"stale heartbeats: {', '.join(stale) if stale else 'none'}"
    )
    print(summary)
    if not problems:
        if db is not None:
            db.close()
        return 0

    alert = "Log scan found problems: " + "; ".join(problems) + ". " + summary
    if db_available and db is not None:
        try:
            if _notify_chairman(db, "Log scan: problems found", alert):
                print("log_scan: Chairman notified", file=sys.stderr)
                return 0
            print("log_scan: no active Chairman to notify", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 - fall through to stderr alert
            print(f"log_scan: Chairman notification failed ({exc})", file=sys.stderr)
        finally:
            db.close()
    print(f"ALERT: {alert}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
