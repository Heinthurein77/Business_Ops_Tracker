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
from src.models import (
    AuditLog, IntercompanyTransaction, InvoicePayment, JobHeartbeat,
    JournalEntry, Notification, PayrollRun, PosSale, PurchaseOrder,
    SalesInvoice, StaffAdvance, StockMovement, User,
)
from src.utils import utcnow

# A job whose heartbeat is older than this is presumed dead.
STALE_AFTER_HOURS = 36
# Jobs expected to write a heartbeat every run. audit_retention and
# backup are weekly jobs, so they are watched separately (WEEKLY_JOBS)
# with a weekly grace instead of the 36 h daily budget — see
# JOB_MAX_AGE_HOURS. G-3: reconcile_journals (daily 07:30) writes a
# heartbeat on every run, so it belongs here — without it a dead
# reconcile cron never alerts.
EXPECTED_JOBS = ("scheduled_alerts", "log_scan", "reconcile_journals")
# Weekly jobs and their dead-man grace: audit_retention runs Sundays,
# so it gets 8 days (weekly + 1 day) instead of the 36 h daily budget.
# G-4: backup (Sundays 03:30) gets the same 8-day grace.
JOB_MAX_AGE_HOURS = {"scheduled_alerts": STALE_AFTER_HOURS, "log_scan": STALE_AFTER_HOURS,
                     "reconcile_journals": STALE_AFTER_HOURS,
                     "audit_retention": 8 * 24, "backup": 8 * 24}
WEEKLY_JOBS = ("audit_retention", "backup")

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


def count_pool_signals(lines: Iterable[str]) -> dict:
    """Cm-5: pool-wait / lock-timeout signals in log lines. Pure.

    Kept separate from count_signals() (whose exact dict shape is
    pinned by tests): pool pressure is the SM-11/PM-5/Cm-5 cluster, and
    the first month-end burst should be visible in the daily scan, not
    just as generic 503s. "database unavailable" is the 503 detail
    get_db raises on pool exhaustion (and on a genuinely dead DB);
    either way it belongs in this bucket.
    """
    counts = {"pool_waits": 0, "lock_timeouts": 0}
    for line in lines:
        lowered = line.lower()
        if ("queuepool" in lowered or "pool timeout" in lowered
                or "database unavailable" in lowered):
            counts["pool_waits"] += 1
        if ("lock timeout" in lowered or "could not obtain lock" in lowered
                or "deadlock detected" in lowered or "40p01" in lowered
                or "55p03" in lowered):
            counts["lock_timeouts"] += 1
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


def stale_jobs(heartbeats, now=None, max_age_hours: int = STALE_AFTER_HOURS,
               expected=EXPECTED_JOBS, never_ran_is_stale: bool = True) -> list[str]:
    """Names of expected jobs whose heartbeat is stale. Pure.

    ``heartbeats`` is an iterable of (job_name, last_run_at) with
    last_run_at None when the job never wrote a row. A job that never
    ran is stale: a dead-man check that only watches rows that exist
    cannot see a cron that was never installed. Per-job grace comes
    from JOB_MAX_AGE_HOURS (weekly jobs get longer); max_age_hours is
    the fallback for jobs not listed there. never_ran_is_stale=False is
    for weekly jobs: between install and the first weekly run there is
    legitimately no row yet, so only a stale existing row alerts.
    """
    now = now or utcnow()
    seen = {}
    for job_name, last_run_at in heartbeats:
        seen[job_name] = last_run_at
    stale = []
    for job_name in expected:
        last = seen.get(job_name)
        age = JOB_MAX_AGE_HOURS.get(job_name, max_age_hours)
        if last is None:
            if never_ran_is_stale:
                stale.append(job_name)
        elif (now - last) > timedelta(hours=age):
            stale.append(job_name)
    return stale


# SM-14: money operations that must each leave one audit row, with the
# business-side timestamp that proves the operation happened. The
# optional 4th element is a status the business row must be in to count
# (approved_at is also stamped by reject_advance — it means "decided
# at" — so only approved rows count as expected APPROVE_ADVANCE rows).
# The optional 5th element is an extra SQLAlchemy filter on the
# business rows. It is needed where the timestamp alone does not
# identify the operation:
# - REVERSE_JOURNAL: a reversal is a new JournalEntry with
#   reversal_of_id set (no reversal timestamp exists on the original),
#   so only reversal entries count.
# - TRANSFER_STOCK: StockMovement rows are written by many operations
#   (receive, sell, adjust, production); only the quick transfer's
#   "Transfer to BU …" out-leg is a TRANSFER_STOCK — the same reason
#   marker reconcile_journals uses to identify quick transfers.
# - COMPLETE_PRODUCTION: ProductionOrder has no completion timestamp
#   (production_date is set at creation), so the finished-goods
#   "Production order #N completed" in-movement — exactly one per
#   completion — is the business-side proof.
# SELL_UNIT is deliberately NOT covered: PropertyUnit has no sold_at /
# updated_at and the sell only flips status to "reserved" and links a
# draft invoice, so there is no business-side timestamp to compare
# against the window — wiring it would need a schema change.
AUDIT_GAP_CHECKS = (
    ("RECEIVE_PO", PurchaseOrder, PurchaseOrder.received_at, None, None),
    ("PAY_PO", PurchaseOrder, PurchaseOrder.paid_at, None, None),
    ("SEND_INVOICE", SalesInvoice, SalesInvoice.sent_at, None, None),
    ("RECORD_PAYMENT", InvoicePayment, InvoicePayment.created_at, None, None),
    ("PAY_PAYROLL", PayrollRun, PayrollRun.paid_at, None, None),
    ("APPROVE_ADVANCE", StaffAdvance, StaffAdvance.approved_at, "approved", None),
    ("SETTLE_INTERCOMPANY", IntercompanyTransaction, IntercompanyTransaction.settled_at,
     "settled", None),
    ("REVERSE_JOURNAL", JournalEntry, JournalEntry.created_at, None,
     JournalEntry.reversal_of_id.isnot(None)),
    ("TRANSFER_STOCK", StockMovement, StockMovement.created_at, None,
     StockMovement.reason.like("Transfer to BU %")),
    ("COMPLETE_PRODUCTION", StockMovement, StockMovement.created_at, None,
     StockMovement.reason.like("Production order #% completed")),
    ("CREATE_POS_SALE", PosSale, PosSale.created_at, "completed", None),
)


def check_audit_gaps(db, since: datetime) -> list[str]:
    """SM-14: expected vs written audit rows for money operations.

    For each money action, the business rows timestamped in the window
    are the expected audit rows; the AuditLog rows with that action in
    the same window are what was actually written. A deficit means an
    audit write was dropped (e.g. pool exhaustion swallowing log_audit
    — SM-11/SM-14) and the trail no longer covers the money that moved.
    Returns human-readable gap lines (empty when everything matches).
    Boundary note: the business timestamp and the audit timestamp are
    written milliseconds apart, so an operation landing exactly on the
    window edge can count on one side only — a gap of 1 is noise, a
    larger deficit is a real signal.
    """
    gaps = []
    for action, model, ts_attr, status, extra in AUDIT_GAP_CHECKS:
        q = db.query(model).filter(ts_attr >= since)
        if status is not None:
            q = q.filter(model.status == status)
        if extra is not None:
            q = q.filter(extra)
        expected = q.count()
        written = db.query(AuditLog).filter(
            AuditLog.action == action, AuditLog.timestamp >= since).count()
        if expected > written:
            gaps.append(f"audit gap: {action} expected {expected}, wrote {written}")
    return gaps


# ------------------------------------------------------------------ DB bits

def check_heartbeats(db, now=None) -> list[str]:
    """Stale-job names from the database's heartbeat table.

    Daily jobs use the 36 h budget and alert when they never ran;
    weekly jobs (audit_retention, backup) use their own grace and only
    alert on a stale existing row — between install and the first weekly
    run there is legitimately no row yet.
    """
    rows = db.query(JobHeartbeat).all()
    pairs = [(row.job_name, row.last_run_at) for row in rows]
    return (stale_jobs(pairs, now=now)
            + stale_jobs(pairs, now=now, expected=WEEKLY_JOBS, never_ran_is_stale=False))


def _record_heartbeat(db, job_name: str, status: str) -> None:
    """Best effort: a heartbeat write must not fail the scan itself."""
    try:
        db.merge(JobHeartbeat(job_name=job_name, last_run_at=utcnow(), last_status=status))
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

    since = utcnow() - timedelta(hours=args.hours)
    window_lines = lines_in_window(raw_lines, since)
    counts = count_signals(window_lines)
    pool_counts = count_pool_signals(window_lines)

    # Dead-man check + own heartbeat + audit-gap check. A DB failure
    # here must not kill the scan: the log counts are still worth
    # reporting, and the fallback (stderr + non-zero exit) is the alert
    # path.
    stale: list[str] = []
    gaps: list[str] = []
    db_available = False
    db = None
    try:
        db = SessionLocal()
        stale = check_heartbeats(db)
        gaps = check_audit_gaps(db, since)
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
    if pool_counts["pool_waits"]:
        problems.append(f"{pool_counts['pool_waits']} pool-wait line(s)")
    if pool_counts["lock_timeouts"]:
        problems.append(f"{pool_counts['lock_timeouts']} lock-timeout line(s)")
    if stale:
        problems.append(f"stale job heartbeat(s): {', '.join(stale)} (>{STALE_AFTER_HOURS}h or never ran)")
    problems.extend(gaps)

    summary = (
        f"log_scan: {counts['total']} line(s) in window, {counts['errors']} ERROR, "
        f"{counts['journal_failed']} journal-failed, {counts['lockouts']} lockout; "
        f"pool_waits: {pool_counts['pool_waits']}, lock_timeouts: {pool_counts['lock_timeouts']}; "
        f"audit gaps: {'; '.join(gaps) if gaps else 'none'}; "
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
