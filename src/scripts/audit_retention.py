"""
Retention for the audit log — keeps it from growing without limit and stops
IP addresses (personal data) being kept forever.

    python -m src.scripts.audit_retention [--login-days 90] [--ip-days 30] [--dry-run]

  * Sign-in / sign-out / failed-sign-in rows older than --login-days are deleted
    (they are high-volume and only useful for recent investigation and for the
    throttle windows, which look back hours or days, not months).
  * The IP address on every remaining row older than --ip-days is cleared.
  * Report-submission, target, account-management and password rows are KEPT:
    they are the tamper-evidence trail for financial edits and account changes.

Run it on a schedule (weekly is plenty), as the schema-owner role: the runtime
role deliberately cannot UPDATE or DELETE audit_logs (see deploy/db_roles.sql),
so this connects with MIGRATION_DATABASE_URL (falling back to DATABASE_URL when
no separate owner role is configured).

Pm-2: each run upserts a `job_heartbeats` row for `audit_retention`, so
the log-scan dead-man check (which watches weekly jobs with an 8-day
grace) notices if this job silently dies. A dry run writes no heartbeat.
"""
import argparse
import sys
from datetime import datetime, timedelta

import sqlalchemy as sa

from src.config import settings
from src.utils import utcnow

SHORT_LIVED_ACTIONS = ("LOGIN_SUCCESS", "LOGIN_FAILED", "LOGOUT")


def _record_heartbeat(engine) -> None:
    """Pm-2: upsert this run into job_heartbeats (R-6 dead-man check).

    Same upsert shape scheduled_alerts uses, but over this script's own
    owner-role engine (no ORM session here). Best effort by design: a
    heartbeat failure must never mask the retention run's own result.
    The INSERT ... ON CONFLICT form works on both Postgres and SQLite.
    """
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO job_heartbeats (job_name, last_run_at, last_status) "
                    "VALUES (:job, :now, :status) "
                    "ON CONFLICT (job_name) DO UPDATE SET "
                    "last_run_at = EXCLUDED.last_run_at, last_status = EXCLUDED.last_status"
                ),
                {"job": "audit_retention", "now": utcnow(), "status": "ok"},
            )
    except Exception as exc:  # noqa: BLE001 - heartbeat must not fail the job
        print(f"audit_retention: heartbeat update failed ({exc})", file=sys.stderr)


def run(login_days: int, ip_days: int, dry_run: bool) -> tuple[int, int]:
    """Returns (rows deleted, IP addresses cleared)."""
    now = utcnow()
    login_cutoff = now - timedelta(days=login_days)
    ip_cutoff = now - timedelta(days=ip_days)
    engine = sa.create_engine(settings.MIGRATION_DATABASE_URL, hide_parameters=True)
    try:
        with engine.begin() as conn:
            delete_where = "action IN :actions AND timestamp < :cutoff"
            clear_where = "ip_address IS NOT NULL AND timestamp < :cutoff"
            delete_params = {"actions": SHORT_LIVED_ACTIONS, "cutoff": login_cutoff}
            clear_params = {"cutoff": ip_cutoff}
            delete_stmt = sa.text(f"DELETE FROM audit_logs WHERE {delete_where}").bindparams(
                sa.bindparam("actions", expanding=True)
            )
            count_stmt = sa.text(f"SELECT count(*) FROM audit_logs WHERE {delete_where}").bindparams(
                sa.bindparam("actions", expanding=True)
            )
            to_delete = conn.execute(count_stmt, delete_params).scalar() or 0
            # Count what the second step will touch AFTER the first has removed its rows, so
            # the two numbers add up to what actually changed.
            if dry_run:
                to_clear = conn.execute(
                    sa.text(
                        f"SELECT count(*) FROM audit_logs WHERE {clear_where} "
                        "AND NOT (action IN :actions AND timestamp < :login_cutoff)"
                    ).bindparams(sa.bindparam("actions", expanding=True)),
                    {**clear_params, "actions": SHORT_LIVED_ACTIONS, "login_cutoff": login_cutoff},
                ).scalar() or 0
                return int(to_delete), int(to_clear)
            conn.execute(delete_stmt, delete_params)
            cleared = conn.execute(
                sa.text(f"UPDATE audit_logs SET ip_address = NULL WHERE {clear_where}"), clear_params
            ).rowcount
            deleted, cleared = int(to_delete), int(cleared or 0)
        _record_heartbeat(engine)
        return deleted, cleared
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--login-days", type=int, default=90, help="delete sign-in/out rows older than this (default 90)")
    parser.add_argument("--ip-days", type=int, default=30, help="clear IP addresses older than this (default 30)")
    parser.add_argument("--dry-run", action="store_true", help="report what would change, change nothing")
    args = parser.parse_args()
    if args.login_days < 1 or args.ip_days < 1:
        parser.error("day counts must be at least 1")
    deleted, cleared = run(args.login_days, args.ip_days, args.dry_run)
    verb = "would delete" if args.dry_run else "deleted"
    verb2 = "would clear" if args.dry_run else "cleared"
    print(f"{verb} {deleted} sign-in/out row(s); {verb2} {cleared} IP address(es)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
