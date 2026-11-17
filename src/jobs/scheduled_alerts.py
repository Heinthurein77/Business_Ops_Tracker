"""Scheduled alert job: missing-report reminders + document-expiry alerts
+ action-item due/overdue reminders.

Run once daily from cron on the server, e.g.:

    # 8:30 AM every day (server time), inside the running container:
    30 8 * * * cd ~/business-ops-tracker && docker compose -f docker-compose.prod.yml exec -T app python -m src.jobs.scheduled_alerts >> ~/bot-alerts.log 2>&1

All three checks are idempotent/throttled in src/routers/ops.py:
- Missing reports: one notification per CEO per run for today's missing report.
- Document expiry: one alert per document per 7 days.
- Action items: one reminder per item per 7 days when due today or overdue.

M-05: a failed run also notifies Chairman/HO in-app (and logs at ERROR
level) — a failure that only lands in ~/bot-alerts.log is invisible to
everyone, since nothing monitors that file.
"""
import logging
import sys
from datetime import datetime

from src.database import SessionLocal
from src.models import JobHeartbeat, Notification, User
from src.utils import utcnow
from src.routers.ops import run_check_document_expiry, run_remind_action_items, run_remind_missing_reports

logger = logging.getLogger(__name__)


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


def _notify_failure(db, exc: Exception) -> None:
    """M-05: tell Chairman/HO in-app that the run failed. Best effort by
    design — when the database itself is the failure this will fail too,
    and the ERROR log line + non-zero exit are the alert path then."""
    try:
        recipients = db.query(User).filter(
            User.role.in_(["Chairman", "HO"]), User.is_active == True).all()  # noqa: E712
        for recipient in recipients:
            db.add(Notification(
                user_id=recipient.id, type="alert",
                title="Scheduled alerts failed",
                message=f"scheduled_alerts failed: {exc}",
                link="/admin/daily-ops"))
        if recipients:
            db.commit()
    except Exception as notify_exc:  # noqa: BLE001 - never mask the job result
        db.rollback()
        print(f"scheduled_alerts failure notification failed: {notify_exc}", file=sys.stderr)


def main() -> int:
    db = SessionLocal()
    try:
        reminded = run_remind_missing_reports(db)
        expiry = run_check_document_expiry(db, days=30)
        actions = run_remind_action_items(db)
        print(f"missing-report reminders sent: {reminded}")
        print(f"documents expiring soon: {expiry['checked']} (notifications sent: {expiry['notified']})")
        print(f"action items due/overdue: {actions['checked']} (reminders sent: {actions['notified']})")
        _record_heartbeat(db, "scheduled_alerts", "ok")
        return 0
    except Exception as exc:
        logger.error("scheduled_alerts failed: %s", exc, exc_info=True)
        print(f"scheduled_alerts failed: {exc}", file=sys.stderr)
        _notify_failure(db, exc)
        _record_heartbeat(db, "scheduled_alerts", "failed")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
