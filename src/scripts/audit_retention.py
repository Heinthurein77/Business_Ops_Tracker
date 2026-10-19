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
"""
import argparse
import sys
from datetime import datetime, timedelta

import sqlalchemy as sa

from src.config import settings

SHORT_LIVED_ACTIONS = ("LOGIN_SUCCESS", "LOGIN_FAILED", "LOGOUT")


def run(login_days: int, ip_days: int, dry_run: bool) -> tuple[int, int]:
    """Returns (rows deleted, IP addresses cleared)."""
    now = datetime.utcnow()
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
            return int(to_delete), int(cleared or 0)
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
