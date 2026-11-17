"""Scheduled alert job: missing-report reminders + document-expiry alerts.

Run once daily from cron on the server, e.g.:

    # 8:30 AM every day (server time), inside the running container:
    30 8 * * * cd ~/business-ops-tracker && docker compose -f docker-compose.prod.yml exec -T app python -m src.jobs.scheduled_alerts >> ~/bot-alerts.log 2>&1

Both checks are idempotent/throttled in src/routers/ops.py:
- Missing reports: one notification per CEO per run for today's missing report.
- Document expiry: one alert per document per 7 days.
"""
import sys
from datetime import datetime

from src.database import SessionLocal
from src.models import JobHeartbeat
from src.utils import utcnow
from src.routers.ops import run_check_document_expiry, run_remind_missing_reports


def _record_heartbeat(db, job_name: str, status: str) -> None:
    """Upsert this run into job_heartbeats (R-6 dead-man check). Best
    effort by design: a heartbeat write must never mask the job's own
    result, so any failure here is logged and swallowed."""
    try:
        db.merge(JobHeartbeat(job_name=job_name, last_run_at=utcnow(), last_status=status))
        db.commit()
    except Exception as exc:  # noqa: BLE001 - heartbeat must not fail the job
        db.rollback()
        print(f"heartbeat update failed for {job_name}: {exc}", file=sys.stderr)


def main() -> int:
    db = SessionLocal()
    try:
        reminded = run_remind_missing_reports(db)
        expiry = run_check_document_expiry(db, days=30)
        print(f"missing-report reminders sent: {reminded}")
        print(f"documents expiring soon: {expiry['checked']} (notifications sent: {expiry['notified']})")
        _record_heartbeat(db, "scheduled_alerts", "ok")
        return 0
    except Exception as exc:
        print(f"scheduled_alerts failed: {exc}", file=sys.stderr)
        _record_heartbeat(db, "scheduled_alerts", "failed")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
