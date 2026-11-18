"""Ops alert jobs: missing-report reminders, action-item due/overdue
reminders, and document-expiry alerts.

m-21: moved here verbatim from src/routers/ops.py so the cron job
(src/jobs/scheduled_alerts.py) and the manual trigger endpoints both
import from a module with no HTTP layer (no FastAPI, no schemas, no R2).
Behaviour is unchanged; the router re-exports these names so existing
imports (including the test suite) keep working.
"""
from datetime import datetime, time, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from src.constants import ACTION_ITEM_OPEN_STATUSES, NotificationType, Role
from src.models import ActionItem, BusinessUnit, DailyFinancial, LicenseDocument, Notification, User
from src.utils import APP_TZ, clamp_days, notify, today_local, utcnow


def _action_item_link(assignee: Optional[User]) -> str:
    """M-13: an action-item link must be reachable by the assignee. A CEO
    sees their items on /ceo (CeoOpsSection on the CEO dashboard);
    Chairman/HO use /admin/daily-ops. The old '/admin' link bounced a
    CEO to their home page — /admin is Chairman/HO-only."""
    if assignee is not None and assignee.role == Role.CEO:
        return "/ceo"
    return "/admin/daily-ops"


def run_remind_missing_reports(db: Session) -> int:
    """Job-callable: notify CEOs whose BU hasn't submitted today.

    m-08: deduped per CEO per day — unlike the 7-day throttles on
    document-expiry and action-item reminders, this used to send one
    notification per run, so a manual re-run (or a cron + manual overlap)
    duplicated reminders. "Today" is the Yangon business day, so the
    cutoff is Yangon midnight expressed in UTC (created_at is naive UTC).
    """
    today = today_local()
    day_start_utc = datetime.combine(today, time.min).replace(tzinfo=APP_TZ).astimezone(timezone.utc).replace(tzinfo=None)
    submitted_bu_ids = {
        row[0] for row in db.query(DailyFinancial.business_unit_id).filter(DailyFinancial.report_date == today).all()
    }
    ceos = db.query(User).filter(User.role == Role.CEO, User.is_active == True).all()  # noqa: E712
    sent = 0
    for ceo in ceos:
        if ceo.business_unit_id and ceo.business_unit_id not in submitted_bu_ids:
            already = db.query(Notification).filter(
                Notification.user_id == ceo.id,
                Notification.type == NotificationType.MISSING_REPORT,
                Notification.created_at >= day_start_utc,
            ).first()
            if already is not None:
                continue
            notify(
                db, ceo.id, NotificationType.MISSING_REPORT,
                "Report not submitted today",
                "Please submit today's business performance report.",
                link="/ceo",
            )
            sent += 1
    db.commit()
    return sent


def run_remind_action_items(db: Session) -> dict:
    """Job-callable: notify assignees of open action items due today or
    overdue. Throttled per item to one reminder per 7 days (same pattern
    as run_check_document_expiry's expiry_notified_at). Unassigned items
    and items assigned to inactive users are skipped — there is nobody
    to notify."""
    today = today_local()
    items = db.query(ActionItem).filter(
        ActionItem.status.in_(ACTION_ITEM_OPEN_STATUSES),
        ActionItem.due_date.is_not(None),
        ActionItem.due_date <= today,
        ActionItem.assigned_to_id.is_not(None),
    ).all()
    sent = 0
    for item in items:
        if item.reminder_sent_at and item.reminder_sent_at > utcnow() - timedelta(days=7):
            continue
        assignee = db.query(User).filter(User.id == item.assigned_to_id, User.is_active == True).first()  # noqa: E712
        if assignee is None:
            continue
        bu = db.query(BusinessUnit).filter(BusinessUnit.id == item.business_unit_id).first()
        bu_name = bu.name if bu else f"BU {item.business_unit_id}"
        overdue_days = (today - item.due_date).days
        if overdue_days > 0:
            title = f"Action item overdue: {item.title}"
            message = (f"'{item.title}' ({bu_name}) was due {item.due_date.isoformat()} "
                       f"({overdue_days} day{'s' if overdue_days != 1 else ''} overdue).")
        else:
            title = f"Action item due today: {item.title}"
            message = f"'{item.title}' ({bu_name}) is due today ({item.due_date.isoformat()})."
        notify(db, assignee.id, NotificationType.ACTION_DUE, title, message, link=_action_item_link(assignee))
        item.reminder_sent_at = utcnow()
        sent += 1
    db.commit()
    return {"checked": len(items), "notified": sent}


def run_check_document_expiry(db: Session, days: int = 30) -> dict:
    """Job-callable: notify CEOs + Chairman/HO about documents expiring soon."""
    # m-01: the cron job calls this directly, so clamp here too — a huge
    # days used to overflow timedelta() and 500 the 08:30 run.
    days = clamp_days(days)
    today = today_local()
    horizon = today + timedelta(days=days)
    docs = db.query(LicenseDocument).filter(
        LicenseDocument.expiry_date >= today, LicenseDocument.expiry_date <= horizon).all()
    admins = db.query(User).filter(User.role.in_([Role.CHAIRMAN, Role.HO]), User.is_active == True).all()  # noqa: E712
    sent = 0
    for d in docs:
        if d.expiry_notified_at and d.expiry_notified_at > utcnow() - timedelta(days=7):
            continue
        bu = db.query(BusinessUnit).filter(BusinessUnit.id == d.business_unit_id).first()
        bu_name = bu.name if bu else f"BU {d.business_unit_id}"
        days_left = (d.expiry_date - today).days
        title = f"Document expiring: {d.title} ({bu_name})"
        message = f"{d.document_type} expires in {days_left} days ({d.expiry_date.isoformat()})."
        ceos = db.query(User).filter(User.role == Role.CEO, User.business_unit_id == d.business_unit_id, User.is_active == True).all()  # noqa: E712
        seen = set()
        for u in admins + ceos:
            if u.id in seen:
                continue
            seen.add(u.id)
            notify(db, u.id, NotificationType.DOCUMENT_EXPIRY, title, message, link="/admin/daily-ops")
            sent += 1
        d.expiry_notified_at = utcnow()
    db.commit()
    return {"checked": len(docs), "notified": sent}
