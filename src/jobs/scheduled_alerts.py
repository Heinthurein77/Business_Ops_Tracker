"""Scheduled alert job: missing-report reminders + document-expiry alerts.

Run once daily from cron on the server, e.g.:

    # 8:30 AM every day (server time), inside the running container:
    30 8 * * * cd ~/business-ops-tracker && docker compose -f docker-compose.prod.yml exec -T app python -m src.jobs.scheduled_alerts >> ~/bot-alerts.log 2>&1

Both checks are idempotent/throttled in src/routers/ops.py:
- Missing reports: one notification per CEO per run for today's missing report.
- Document expiry: one alert per document per 7 days.
"""
import sys

from src.database import SessionLocal
from src.routers.ops import run_check_document_expiry, run_remind_missing_reports


def main() -> int:
    db = SessionLocal()
    try:
        reminded = run_remind_missing_reports(db)
        expiry = run_check_document_expiry(db, days=30)
        print(f"missing-report reminders sent: {reminded}")
        print(f"documents expiring soon: {expiry['checked']} (notifications sent: {expiry['notified']})")
        return 0
    except Exception as exc:
        print(f"scheduled_alerts failed: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
