"""Scheduled alert job: missing-report reminders + document-expiry alerts
+ action-item due/overdue reminders.

Run once daily from cron on the server, e.g.:

    # 8:30 AM every day (server time), inside the running container:
    30 8 * * * cd ~/business-ops-tracker && docker compose -f docker-compose.prod.yml exec -T app python -m src.jobs.scheduled_alerts >> ~/bot-alerts.log 2>&1

All three checks are idempotent/throttled in src/services/ops_alerts.py:
- Missing reports: one notification per CEO per run for today's missing report.
- Document expiry: one alert per document per 7 days.
- Action items: one reminder per item per 7 days when due today or overdue.
"""
import sys

from src.database import SessionLocal
from src.services.ops_alerts import (
    run_check_document_expiry,
    run_remind_action_items,
    run_remind_missing_reports,
)
from src.utils import record_job_heartbeat as _record_heartbeat


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
        print(f"scheduled_alerts failed: {exc}", file=sys.stderr)
        _record_heartbeat(db, "scheduled_alerts", "failed")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
