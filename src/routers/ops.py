"""Operational extensions: action items, notifications, staff records,
customer metrics, documents, and forecasting.

Access model mirrors the rest of the app:
- CEO: scoped to their own business unit
- Chairman / HO ("admin"): all business units
"""
import logging
import threading
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.auth import get_current_user, require_roles
from src.services.ops_alerts import (  # noqa: F401 - re-exported for callers/tests
    _action_item_link,
    run_check_document_expiry,
    run_remind_action_items,
    run_remind_missing_reports,
)
from src.database import get_db
from src.utils import _sanitize_excel_cell, log_audit, notify, today_local, utcnow, validate_period
from src.models import (
    ActionItem,
    ActivityLog,
    BusinessUnit,
    CeoProfile,
    CustomerMetric,
    DailyFinancial,
    HeadOfficeActivityLog,
    LicenseDocument,
    MonthlyTarget,
    Notification,
    StaffRecord,
    User,
)

router = APIRouter(prefix="/api/ops", tags=["ops"])
require_admin = require_roles("Chairman", "HO")

logger = logging.getLogger(__name__)

# m-08: one in-process lock for the manual reminder endpoints. The cron
# lines already have flock, but a manual trigger (or two admins clicking)
# runs in-process with no lock — overlapping runs could both pass a
# throttle/dedupe check before either commits. Non-blocking: if held,
# the caller gets a clear 409 instead of a duplicate run.
_REMINDER_LOCK = threading.Lock()


@contextmanager
def _reminder_lock_or_409():
    if not _REMINDER_LOCK.acquire(blocking=False):
        raise HTTPException(
            409, "A reminder run is already in progress — please try again shortly.")
    try:
        yield
    finally:
        _REMINDER_LOCK.release()

def _log_audit(db, user, action, details=""):
    """Local wrapper around src.utils.log_audit (R-3 audit trail).

    Two normalisations, both deliberate: (1) callers in the test suite pass
    duck-typed user objects (SimpleNamespace with id/role but no
    .username), which log_audit reads directly — the actor is re-shaped
    into the (id, username) pair log_audit needs instead of crashing the
    request; (2) an audit-write failure must never fail a business
    operation that already committed, so it is logged and swallowed (the
    D-8 direction: audit is evidence, not part of the money transaction).
    """
    from types import SimpleNamespace

    actor = user
    if user is not None and not hasattr(user, "username"):
        actor = SimpleNamespace(
            id=getattr(user, "id", None),
            username=f"user-{getattr(user, 'id', '?')}",
        )
    try:
        log_audit(db, actor, action, details)
    except Exception as exc:  # noqa: BLE001 - audit must not break the op
        logger.warning("audit write failed for %s: %s", action, exc)


# ---------------------------------------------------------------- shared helpers

def _escape_like(value: str) -> str:
    """Escape LIKE wildcards (Sm-1): `_` and `%` in a stored attachment
    key must match literally, never as a single-/multi-char wildcard."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ---------------------------------------------------------------- helpers

def bu_scope(user: User, bu_id: Optional[int]) -> Optional[int]:
    """Resolve which BU the caller may touch. CEOs are pinned to their own:
    naming another BU is a 403, naming none means their own BU.
    Chairman/HO may name any BU (or none, for all)."""
    if user.role == "CEO":
        if bu_id is not None and bu_id != user.business_unit_id:
            raise HTTPException(403, "CEOs can only access their own business unit")
        return user.business_unit_id
    return bu_id


def serialize_action_item(db: Session, item: ActionItem) -> dict:
    assignee = db.query(User).filter(User.id == item.assigned_to_id).first() if item.assigned_to_id else None
    return {
        "id": item.id,
        "business_unit_id": item.business_unit_id,
        "title": item.title,
        "description": item.description,
        "assigned_to_id": item.assigned_to_id,
        "assigned_to_name": assignee.full_name or assignee.username if assignee else None,
        "due_date": item.due_date.isoformat() if item.due_date else None,
        "status": item.status,
        "created_by_id": item.created_by_id,
        "completed_at": item.completed_at.isoformat() if item.completed_at else None,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


# ---------------------------------------------------------------- action items

class ActionItemIn(BaseModel):
    business_unit_id: Optional[int] = None
    title: str = Field(min_length=1, max_length=200)
    description: Optional[str] = None
    assigned_to_id: Optional[int] = None
    due_date: Optional[date] = None


@router.get("/action-items")
def list_action_items(
    business_unit_id: Optional[int] = None,
    status: Optional[str] = None,
    mine: bool = False,
    limit: int = 200,
    offset: int = 0,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    bu_id = bu_scope(user, business_unit_id)
    q = db.query(ActionItem)
    if bu_id is not None:
        q = q.filter(ActionItem.business_unit_id == bu_id)
    if status:
        q = q.filter(ActionItem.status == status)
    if mine:
        q = q.filter(ActionItem.assigned_to_id == user.id)
    items = q.order_by(ActionItem.due_date.is_(None), ActionItem.due_date, ActionItem.created_at.desc()).offset(offset).limit(limit).all()
    return {"items": [serialize_action_item(db, i) for i in items]}


def _validate_assignee(db: Session, user: User, assigned_to_id: Optional[int]) -> Optional[User]:
    """m-34: an assignee must exist, be active, and — for a CEO creator —
    belong to the caller's own business unit (a Chairman/HO creator
    manages all BUs, so any active user is assignable by them). Returns
    the assignee (None when unassigned) so callers can reuse it for the
    notification link. Raises 400 BEFORE anything is created — the old
    code only failed on the second commit (FK violation → 500) after the
    item itself was already committed."""
    if assigned_to_id is None:
        return None
    assignee = db.query(User).filter(User.id == assigned_to_id).first()
    if assignee is None:
        raise HTTPException(400, "Assigned user does not exist")
    if not assignee.is_active:
        raise HTTPException(400, "Assigned user is not active")
    if user.role == "CEO" and assignee.business_unit_id != user.business_unit_id:
        raise HTTPException(400, "Assignee must be in your business unit")
    return assignee


@router.post("/action-items")
def create_action_item(payload: ActionItemIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bu_id = bu_scope(user, payload.business_unit_id)
    if bu_id is None:
        raise HTTPException(400, "business_unit_id is required")
    assignee = _validate_assignee(db, user, payload.assigned_to_id)
    item = ActionItem(
        business_unit_id=bu_id,
        title=payload.title.strip(),
        description=payload.description,
        assigned_to_id=payload.assigned_to_id,
        due_date=payload.due_date,
        status="pending",
        created_by_id=user.id,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    if item.assigned_to_id and item.assigned_to_id != user.id:
        notify(db, item.assigned_to_id, "action_assigned",
               "New action item assigned", item.title, link=_action_item_link(assignee))
        db.commit()
    _log_audit(db, user, "CREATE_ACTION_ITEM", f"id={item.id}")
    return serialize_action_item(db, item)


class ActionItemUpdate(BaseModel):
    status: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    assigned_to_id: Optional[int] = None
    due_date: Optional[date] = None


@router.patch("/action-items/{item_id}")
def update_action_item(item_id: int, payload: ActionItemUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = db.query(ActionItem).filter(ActionItem.id == item_id).first()
    if not item:
        raise HTTPException(404, "Action item not found")
    bu_scope(user, item.business_unit_id)
    if payload.status is not None:
        if payload.status not in ("pending", "in_progress", "done"):
            raise HTTPException(400, "Invalid status")
        item.status = payload.status
        item.completed_at = utcnow() if payload.status == "done" else None
    if payload.title is not None:
        item.title = payload.title.strip()
    if payload.description is not None:
        item.description = payload.description
    if payload.assigned_to_id is not None:
        _validate_assignee(db, user, payload.assigned_to_id)
        item.assigned_to_id = payload.assigned_to_id
    if payload.due_date is not None:
        item.due_date = payload.due_date
    db.commit()
    db.refresh(item)
    _log_audit(db, user, "UPDATE_ACTION_ITEM", f"item_id={item_id}")
    return serialize_action_item(db, item)


@router.delete("/action-items/{item_id}")
def delete_action_item(item_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = db.query(ActionItem).filter(ActionItem.id == item_id).first()
    if not item:
        raise HTTPException(404, "Action item not found")
    bu_scope(user, item.business_unit_id)
    db.delete(item)
    db.commit()
    _log_audit(db, user, "DELETE_ACTION_ITEM", f"item_id={item_id}")
    return {"ok": True}


# ---------------------------------------------------------------- notifications

@router.get("/notifications")
def list_notifications(unread_only: bool = False, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(Notification).filter(Notification.user_id == user.id)
    if unread_only:
        q = q.filter(Notification.is_read == False)  # noqa: E712
    rows = q.order_by(Notification.created_at.desc()).limit(100).all()
    unread = db.query(Notification).filter(Notification.user_id == user.id, Notification.is_read == False).count()  # noqa: E712
    return {
        "unread_count": unread,
        "items": [
            {
                "id": n.id, "type": n.type, "title": n.title, "message": n.message,
                "link": n.link, "is_read": n.is_read,
                "created_at": n.created_at.isoformat() if n.created_at else None,
            }
            for n in rows
        ],
    }


@router.post("/notifications/{notification_id}/read")
def mark_notification_read(notification_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    n = db.query(Notification).filter(Notification.id == notification_id, Notification.user_id == user.id).first()
    if not n:
        raise HTTPException(404, "Notification not found")
    n.is_read = True
    db.commit()
    return {"ok": True}


@router.post("/notifications/read-all")
def mark_all_read(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.query(Notification).filter(Notification.user_id == user.id, Notification.is_read == False).update({"is_read": True})  # noqa: E712
    db.commit()
    return {"ok": True}


@router.post("/notifications/remind-missing")
def remind_missing(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Notify CEOs whose BU has not submitted today's DailyFinancial."""
    with _reminder_lock_or_409():
        _log_audit(db, user, "REMIND_MISSING", "")
        return {"notified": run_remind_missing_reports(db)}


@router.post("/notifications/remind-actions")
def remind_actions(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Notify assignees of action items due today or overdue."""
    with _reminder_lock_or_409():
        _log_audit(db, user, "REMIND_ACTIONS", "")
        return run_remind_action_items(db)


@router.get("/uploads/view")
def view_upload(key: str = Query(...), user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    """D-5: session-gated attachment viewing. Stored URL columns are only a
    key reference — the browser never follows them directly. This endpoint
    checks the caller may see the owning record (own BU for a CEO,
    own photo for a CEO, anything for Chairman/HO) and then 302-redirects
    to a fresh 15-minute presigned URL (or to the session-gated local
    /uploads route when R2 is not configured)."""
    import re

    from fastapi.responses import RedirectResponse

    from src.r2_storage import is_configured as r2_is_configured
    from src.r2_storage import presigned_url_for_key

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", key or "") or ".." in key or "/" not in key:
        raise HTTPException(400, "Invalid attachment key")
    owner_bu_id = None
    is_profile_photo = key.startswith("profile-photos/")
    # m-26: ownership is matched on EXACT key equality, not LIKE. Stored
    # values are full URLs (presigned R2, public-base, or the
    # /uploads/<key> local fallback) — extract_r2_key recovers the key
    # from any of those shapes, and only an exact match counts, so a
    # short/partial key can no longer match an arbitrary row's URL
    # substring (and .first() with no order_by made the pick arbitrary).
    # Only the key columns are fetched, not full rows.
    from src.r2_storage import extract_r2_key

    def _key_matches(stored_url):
        if not stored_url:
            return False
        return stored_url == key or extract_r2_key(stored_url) == key

    if is_profile_photo:
        profile_owner_id = None
        for user_id, stored_url in db.query(
                CeoProfile.user_id, CeoProfile.profile_pic_url).filter(
                CeoProfile.profile_pic_url.isnot(None)).all():
            if _key_matches(stored_url):
                profile_owner_id = user_id
                break
        if profile_owner_id is None:
            raise HTTPException(404, "Attachment not found")
        if user.role not in ("Chairman", "HO") and profile_owner_id != user.id:
            raise HTTPException(403, "Not your profile photo")
    else:
        for bu_id, stored_url in db.query(
                ActivityLog.business_unit_id, ActivityLog.document_url).filter(
                ActivityLog.document_url.isnot(None)).all():
            if _key_matches(stored_url):
                owner_bu_id = bu_id
                break
        if owner_bu_id is None:
            ho_found = any(
                _key_matches(stored_url)
                for (stored_url,) in db.query(HeadOfficeActivityLog.document_url).filter(
                    HeadOfficeActivityLog.document_url.isnot(None)).all()
            )
            if ho_found:
                if user.role not in ("Chairman", "HO"):
                    raise HTTPException(403, "Only Chairman/HO can view head-office attachments")
            else:
                raise HTTPException(404, "Attachment not found")
        # m-26: fail closed — the old `and user.business_unit_id`
        # condition skipped the 403 entirely for a NULL-BU CEO. A CEO
        # whose BU is unset now matches nothing, instead of everything.
        if owner_bu_id is not None and user.role == "CEO":
            if owner_bu_id != user.business_unit_id:
                raise HTTPException(403, "Attachment belongs to another business unit")
    # M-04: record who viewed which attachment (key only, never contents).
    _log_audit(db, user, "VIEW_UPLOAD", f"key={key}")
    # Sm-9: the 302 target is a 15-minute bearer URL — Cache-Control:
    # no-store so browsers never cache the resolved presigned URL (a
    # cached copy would keep working as a bearer link past its purpose,
    # or be shared from history).
    no_store = {"Cache-Control": "no-store"}
    if not r2_is_configured():
        return RedirectResponse(url=f"/uploads/{key}", status_code=302, headers=no_store)
    url = presigned_url_for_key(key)
    if not url:
        raise HTTPException(503, "File storage is not available")
    return RedirectResponse(url=url, status_code=302, headers=no_store)


# ---------------------------------------------------------------- staff

class StaffIn(BaseModel):
    business_unit_id: Optional[int] = None
    report_date: date
    total_staff: int = Field(default=0, ge=0, le=1000000)
    present: int = Field(default=0, ge=0, le=1000000)
    absent: int = Field(default=0, ge=0, le=1000000)
    new_hires: int = Field(default=0, ge=0, le=1000000)
    resignations: int = Field(default=0, ge=0, le=1000000)
    notes: Optional[str] = None


@router.post("/staff")
def submit_staff(payload: StaffIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bu_id = bu_scope(user, payload.business_unit_id)
    if bu_id is None:
        raise HTTPException(400, "business_unit_id is required")
    row = db.query(StaffRecord).filter(StaffRecord.business_unit_id == bu_id, StaffRecord.report_date == payload.report_date).first()
    if row is None:
        row = StaffRecord(business_unit_id=bu_id, report_date=payload.report_date)
        db.add(row)
    row.total_staff = payload.total_staff
    row.present = payload.present
    row.absent = payload.absent
    row.new_hires = payload.new_hires
    row.resignations = payload.resignations
    row.notes = payload.notes
    row.submitted_by_id = user.id
    db.commit()
    _log_audit(db, user, "SUBMIT_STAFF", f"id={row.id}")
    return {"ok": True, "id": row.id}


@router.get("/staff")
def list_staff(business_unit_id: Optional[int] = None, days: int = 30,
               limit: int = 200, offset: int = 0,
               user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bu_id = bu_scope(user, business_unit_id)
    since = today_local() - timedelta(days=days)
    q = db.query(StaffRecord).filter(StaffRecord.report_date >= since)
    if bu_id is not None:
        q = q.filter(StaffRecord.business_unit_id == bu_id)
    rows = q.order_by(StaffRecord.report_date.desc()).offset(offset).limit(limit).all()
    return {"items": [
        {"id": r.id, "business_unit_id": r.business_unit_id, "report_date": r.report_date.isoformat(),
         "total_staff": r.total_staff, "present": r.present, "absent": r.absent,
         "new_hires": r.new_hires, "resignations": r.resignations, "notes": r.notes}
        for r in rows
    ]}


# ---------------------------------------------------------------- customers

class CustomerIn(BaseModel):
    business_unit_id: Optional[int] = None
    report_date: date
    footfall: Optional[int] = Field(default=None, ge=0, le=10**9)
    transactions: Optional[int] = Field(default=None, ge=0, le=10**9)
    complaints: int = Field(default=0, ge=0, le=10**9)
    satisfaction_score: Optional[float] = None
    notes: Optional[str] = None


@router.post("/customers")
def submit_customers(payload: CustomerIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bu_id = bu_scope(user, payload.business_unit_id)
    if bu_id is None:
        raise HTTPException(400, "business_unit_id is required")
    if payload.satisfaction_score is not None and not (1.0 <= payload.satisfaction_score <= 5.0):
        raise HTTPException(400, "satisfaction_score must be between 1.0 and 5.0")
    row = db.query(CustomerMetric).filter(CustomerMetric.business_unit_id == bu_id, CustomerMetric.report_date == payload.report_date).first()
    if row is None:
        row = CustomerMetric(business_unit_id=bu_id, report_date=payload.report_date)
        db.add(row)
    row.footfall = payload.footfall
    row.transactions = payload.transactions
    row.complaints = payload.complaints
    row.satisfaction_score = Decimal(str(payload.satisfaction_score)) if payload.satisfaction_score is not None else None
    row.notes = payload.notes
    row.submitted_by_id = user.id
    db.commit()
    _log_audit(db, user, "SUBMIT_CUSTOMERS", f"id={row.id}")
    return {"ok": True, "id": row.id}


@router.get("/customers")
def list_customers(business_unit_id: Optional[int] = None, days: int = 30,
                   limit: int = 200, offset: int = 0,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bu_id = bu_scope(user, business_unit_id)
    since = today_local() - timedelta(days=days)
    q = db.query(CustomerMetric).filter(CustomerMetric.report_date >= since)
    if bu_id is not None:
        q = q.filter(CustomerMetric.business_unit_id == bu_id)
    rows = q.order_by(CustomerMetric.report_date.desc()).offset(offset).limit(limit).all()
    return {"items": [
        {"id": r.id, "business_unit_id": r.business_unit_id, "report_date": r.report_date.isoformat(),
         "footfall": r.footfall, "transactions": r.transactions, "complaints": r.complaints,
         "satisfaction_score": float(r.satisfaction_score) if r.satisfaction_score is not None else None,
         "notes": r.notes}
        for r in rows
    ]}


# ---------------------------------------------------------------- forecast

@router.get("/forecast")
def forecast(business_unit_id: Optional[int] = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Month-end projection from MTD daily revenue/cash-flow run rate."""
    bu_id = bu_scope(user, business_unit_id)
    today = today_local()
    month_start = today.replace(day=1)
    q = db.query(DailyFinancial).filter(DailyFinancial.report_date >= month_start, DailyFinancial.report_date <= today)
    if bu_id is not None:
        q = q.filter(DailyFinancial.business_unit_id == bu_id)
    rows = q.all()
    import calendar
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    days_elapsed = today.day
    mtd_revenue = sum(float(r.revenue or 0) for r in rows)
    mtd_cash_flow = sum(float(r.cash_flow or 0) for r in rows)
    reporting_days = len({r.report_date for r in rows})
    scale = (days_in_month / days_elapsed) if days_elapsed else 1.0
    return {
        "month": today.strftime("%B %Y"),
        "days_elapsed": days_elapsed,
        "days_in_month": days_in_month,
        "reporting_days": reporting_days,
        "mtd_revenue": round(mtd_revenue, 2),
        "mtd_cash_flow": round(mtd_cash_flow, 2),
        "projected_revenue": round(mtd_revenue * scale, 2),
        "projected_cash_flow": round(mtd_cash_flow * scale, 2),
        "daily_avg_revenue": round(mtd_revenue / days_elapsed, 2) if days_elapsed else 0,
        "daily_avg_cash_flow": round(mtd_cash_flow / days_elapsed, 2) if days_elapsed else 0,
    }


# ---------------------------------------------------------------- licenses & documents

def serialize_document(db: Session, d: LicenseDocument) -> dict:
    bu = db.query(BusinessUnit).filter(BusinessUnit.id == d.business_unit_id).first()
    days_left = (d.expiry_date - today_local()).days if d.expiry_date else None
    return {
        "id": d.id,
        "business_unit_id": d.business_unit_id,
        "bu_name": bu.name if bu else None,
        "title": d.title,
        "document_type": d.document_type,
        "issue_date": d.issue_date.isoformat() if d.issue_date else None,
        "expiry_date": d.expiry_date.isoformat() if d.expiry_date else None,
        "days_left": days_left,
        "notes": d.notes,
        "created_at": d.created_at.isoformat() if d.created_at else None,
    }


class DocumentIn(BaseModel):
    business_unit_id: Optional[int] = None
    title: str = Field(min_length=1, max_length=255)
    document_type: str = "license"
    issue_date: Optional[date] = None
    expiry_date: date
    notes: Optional[str] = None


@router.get("/documents")
def list_documents(business_unit_id: Optional[int] = None, expiring_soon: bool = False,
                   limit: int = 200, offset: int = 0,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bu_id = bu_scope(user, business_unit_id)
    q = db.query(LicenseDocument)
    if bu_id is not None:
        q = q.filter(LicenseDocument.business_unit_id == bu_id)
    if expiring_soon:
        # m-11: filter in SQL before pagination. The old code fetched the
        # page and filtered in Python, so `count` was the post-filter page
        # length and a page could come back short while matching rows sat
        # on later pages. Same threshold as before (expiry within 30 days,
        # including already-expired rows), now enforced by the query — and
        # `count` is the total matching count, not the page length.
        horizon = today_local() + timedelta(days=30)
        q = q.filter(LicenseDocument.expiry_date.isnot(None),
                     LicenseDocument.expiry_date <= horizon)
    total = q.count()
    rows = q.order_by(LicenseDocument.expiry_date.asc()).offset(offset).limit(limit).all()
    items = [serialize_document(db, d) for d in rows]
    return {"items": items, "count": total}


@router.post("/documents")
def create_document(payload: DocumentIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bu_id = bu_scope(user, payload.business_unit_id)
    if bu_id is None:
        raise HTTPException(400, "business_unit_id is required")
    d = LicenseDocument(
        business_unit_id=bu_id, title=payload.title,
        document_type=payload.document_type or "license",
        issue_date=payload.issue_date, expiry_date=payload.expiry_date,
        notes=payload.notes, created_by_id=user.id,
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    _log_audit(db, user, "CREATE_DOCUMENT", f"id={d.id}")
    return serialize_document(db, d)


@router.delete("/documents/{document_id}")
def delete_document(document_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    d = db.query(LicenseDocument).filter(LicenseDocument.id == document_id).first()
    if not d:
        raise HTTPException(404, "Document not found")
    bu_scope(user, d.business_unit_id)
    db.delete(d)
    db.commit()
    _log_audit(db, user, "DELETE_DOCUMENT", f"document_id={document_id}")
    return {"ok": True}


@router.post("/documents/check-expiry")
def check_document_expiry(days: int = 30, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Notify each BU's CEO + all Chairman/HO about documents expiring within
    `days`. Throttled per document to one alert per 7 days."""
    with _reminder_lock_or_409():
        _log_audit(db, user, "CHECK_DOCUMENT_EXPIRY", f"days={days}")
        return run_check_document_expiry(db, days)


# ---------------------------------------------------------------- monthly chairman PDF report

@router.get("/monthly-report")
def monthly_report(month: Optional[str] = None, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """One-page-per-BU monthly summary PDF for the Chairman (admin roles only).

    `month` format YYYY-MM; defaults to the current month.
    """
    from io import BytesIO

    from fastapi.responses import StreamingResponse
    from sqlalchemy import func

    today = today_local()
    month = validate_period(month)
    if month:
        y, m = (int(x) for x in month.split("-"))
        month_start = date(y, m, 1)
    else:
        month_start = date(today.year, today.month, 1)
    import calendar as _cal
    month_end = date(month_start.year, month_start.month, _cal.monthrange(month_start.year, month_start.month)[1])
    days_in_month = (month_end - month_start).days + 1

    # M-04: the Chairman PDF export — audit who pulled it, for which month.
    _log_audit(db, user, "MONTHLY_REPORT_EXPORT", f"month={month_start.isoformat()}")

    fin = (db.query(DailyFinancial.business_unit_id,
                    func.coalesce(func.sum(DailyFinancial.revenue), 0),
                    func.coalesce(func.sum(DailyFinancial.cash_flow), 0),
                    func.count(func.distinct(DailyFinancial.report_date)))
           .filter(DailyFinancial.report_date >= month_start, DailyFinancial.report_date <= month_end)
           .group_by(DailyFinancial.business_unit_id).all())
    fin_map = {row[0]: (float(row[1]), float(row[2]), int(row[3])) for row in fin}
    targets = {t.business_unit_id: t for t in db.query(MonthlyTarget).filter(MonthlyTarget.target_month == month_start).all()}
    bus = db.query(BusinessUnit).order_by(BusinessUnit.name).all()

    rows = []
    for bu in bus:
        rev, cf, rep_days = fin_map.get(bu.id, (0.0, 0.0, 0))
        t = targets.get(bu.id)
        trev = float(t.monthly_revenue_target) if t and t.monthly_revenue_target else 0.0
        ach = (rev / trev * 100) if trev else None
        rows.append({"name": bu.name, "revenue": rev, "cash_flow": cf,
                     "reports": rep_days, "target": trev, "ach": ach})

    totals = {
        "revenue": sum(r["revenue"] for r in rows),
        "cash_flow": sum(r["cash_flow"] for r in rows),
        "target": sum(r["target"] for r in rows),
    }
    totals["ach"] = (totals["revenue"] / totals["target"] * 100) if totals["target"] else None

    # Alert data
    expiring_docs = [d for d in db.query(LicenseDocument).all()
                     if d.expiry_date and 0 <= (d.expiry_date - today).days <= 30]

    from fpdf import FPDF, XPos, YPos

    def t(text):
        """Strip characters the core latin-1 PDF font can't render."""
        return (text or "").encode("latin-1", errors="replace").decode("latin-1")

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.add_page()
    W = pdf.w - 20

    pdf.set_font("helvetica", "B", 18)
    pdf.cell(W, 10, "Business Operations - Monthly Report", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("helvetica", "", 11)
    pdf.set_text_color(100, 100, 100)
    label = month_start.strftime("%B %Y")
    pdf.cell(W, 6, f"{label}  |  Generated {today.isoformat()}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(4)

    def money(v):
        return f"Ks {v:,.0f}"

    pdf.set_font("helvetica", "B", 13)
    pdf.cell(W, 8, "Portfolio Summary", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("helvetica", "", 11)
    summary = [
        ("Total revenue", money(totals["revenue"])),
        ("Total cash flow", money(totals["cash_flow"])),
        ("Revenue target", money(totals["target"]) if totals["target"] else "-"),
        ("Overall achievement", f"{totals['ach']:.1f}%" if totals["ach"] is not None else "-"),
        ("Business units", str(len(rows))),
    ]
    for k, v in summary:
        pdf.cell(60, 6.5, k)
        pdf.cell(0, 6.5, v, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(3)

    pdf.set_font("helvetica", "B", 13)
    pdf.cell(W, 8, "Business Unit Performance", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    headers = [("BU", 52), ("Revenue", 30), ("Cash Flow", 30), ("Target", 30), ("Ach. %", 18), ("Reports", 20)]
    pdf.set_font("helvetica", "B", 10)
    for h, w in headers:
        pdf.cell(w, 7, h, border=1)
    pdf.ln()
    pdf.set_font("helvetica", "", 10)
    for r in sorted(rows, key=lambda x: -(x["ach"] or 0)):
        vals = [t(r["name"][:26]), f"{r['revenue']:,.0f}", f"{r['cash_flow']:,.0f}",
                f"{r['target']:,.0f}" if r["target"] else "-",
                f"{r['ach']:.0f}%" if r["ach"] is not None else "-",
                f"{r['reports']}/{days_in_month}"]
        for (_, w), v in zip(headers, vals):
            pdf.cell(w, 6.5, v, border=1)
        pdf.ln()
    pdf.ln(3)

    pdf.set_font("helvetica", "B", 13)
    pdf.cell(W, 8, "Alerts", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("helvetica", "", 11)
    alerts = []
    no_report = [r["name"] for r in rows if r["reports"] == 0]
    if no_report:
        alerts.append(f"No reports this month: {', '.join(no_report)}")
    for d in expiring_docs:
        alerts.append(f"Document expiring soon: {d.title} - {d.expiry_date.isoformat()}")
    if not alerts:
        alerts.append("No alerts.")
    for a in alerts:
        pdf.multi_cell(W, 6, t(f"- {a}"))

    buf = BytesIO(pdf.output())
    filename = f"monthly_report_{month_start.strftime('%Y-%m')}.pdf"
    return StreamingResponse(buf, media_type="application/pdf",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ---------------------------------------------------------------- Excel export

# PM-4: ops exports are bounded. Unbounded tabs used to load every row
# into one workbook; now each tab is capped and the date-bearing tabs
# default to the last 90 days (pass from_date/to_date for more).
OPS_EXPORT_MAX_ROWS = 5000
OPS_EXPORT_DEFAULT_DAYS = 90


@router.get("/export-excel")
def export_excel(tab: str = "actions", business_unit_id: Optional[int] = None,
                 from_date: Optional[date] = None, to_date: Optional[date] = None,
                 limit: int = OPS_EXPORT_MAX_ROWS,
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Download one Daily Ops tab as an .xlsx workbook.

    Row-capped at `limit` (max 5000); the actions tab defaults
    to the last 90 days unless from_date/to_date are given. Staff and
    customers were already capped at 500 and stay that way.
    """
    from io import BytesIO

    from fastapi.responses import StreamingResponse
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    bu_id = bu_scope(user, business_unit_id)
    # M-04: exports carry PII/health/financial data — audit who pulled
    # which tab (never the rows themselves).
    _log_audit(db, user, "OPS_EXCEL_EXPORT", f"tab={tab} bu_id={bu_id}")
    bu_names = {bu.id: bu.name for bu in db.query(BusinessUnit).all()}
    header_fill = PatternFill(start_color="1F2937", end_color="1F2937", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF")
    limit = max(1, min(limit, OPS_EXPORT_MAX_ROWS))

    def date_bounds(default_days=None):
        """(start, end) datetimes for a created_at filter, or (None, None).

        default_days set → from_date defaults to today minus that many
        days; to_date always defaults to open-ended.
        """
        frm = from_date
        if frm is None and default_days is not None:
            frm = today_local() - timedelta(days=default_days)
        start = datetime.combine(frm, datetime.min.time()) if frm else None
        end = datetime.combine(to_date, datetime.max.time()) if to_date else None
        return start, end

    def apply_date_filter(q, column, default_days=None):
        start, end = date_bounds(default_days)
        if start is not None:
            q = q.filter(column >= start)
        if end is not None:
            q = q.filter(column <= end)
        return q

    def build(title, headers, rows):
        wb = Workbook()
        ws = wb.active
        ws.title = title[:31]
        ws.append(headers)
        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = header_font
        for r in rows:
            ws.append([_sanitize_excel_cell(v) if isinstance(v, str) else v for v in r])
        for col in ws.columns:
            width = max(len(str(c.value)) if c.value is not None else 0 for c in col) + 3
            ws.column_dimensions[col[0].column_letter].width = min(max(width, 12), 42)
        buf = BytesIO()
        wb.save(buf)
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="ops_{tab}.xlsx"'},
        )

    def scoped(q, model):
        return q.filter(model.business_unit_id == bu_id).all() if bu_id is not None else q.all()

    def user_names(ids):
        """PM-3: one IN query for many user names (was: one query per row)."""
        ids = [i for i in set(ids) if i]
        if not ids:
            return {}
        return {u.id: (u.full_name or "") for u in
                db.query(User).filter(User.id.in_(ids)).all()}

    if tab == "actions":
        q = apply_date_filter(
            db.query(ActionItem).order_by(ActionItem.created_at.desc()),
            ActionItem.created_at, OPS_EXPORT_DEFAULT_DAYS).limit(limit)
        rows = scoped(q, ActionItem)
        names = user_names([i.assigned_to_id for i in rows])
        return build("Action Items",
                     ["Title", "BU", "Status", "Due Date", "Assigned To", "Created"],
                     [[i.title, bu_names.get(i.business_unit_id, ""), i.status,
                       i.due_date.isoformat() if i.due_date else "",
                       names.get(i.assigned_to_id, "") if i.assigned_to_id else "",
                       i.created_at.strftime("%Y-%m-%d %H:%M") if i.created_at else ""] for i in rows])

    if tab == "documents":
        q = apply_date_filter(
            db.query(LicenseDocument).order_by(LicenseDocument.expiry_date.asc()),
            LicenseDocument.expiry_date).limit(limit)
        rows = scoped(q, LicenseDocument)
        return build("Documents",
                     ["Title", "Type", "BU", "Issue Date", "Expiry Date", "Days Left", "Notes"],
                     [[d.title, d.document_type, bu_names.get(d.business_unit_id, ""),
                       d.issue_date.isoformat() if d.issue_date else "",
                       d.expiry_date.isoformat() if d.expiry_date else "",
                       (d.expiry_date - today_local()).days if d.expiry_date else "",
                       d.notes or ""] for d in rows])

    if tab == "staff":
        q = db.query(StaffRecord).order_by(StaffRecord.report_date.desc()).limit(500)
        rows = scoped(q, StaffRecord)
        return build("Staff",
                     ["Date", "BU", "Total", "Present", "Absent", "New Hires", "Resignations"],
                     [[s.report_date.isoformat() if s.report_date else "", bu_names.get(s.business_unit_id, ""),
                       s.total_staff, s.present, s.absent, s.new_hires, s.resignations] for s in rows])

    if tab == "customers":
        q = db.query(CustomerMetric).order_by(CustomerMetric.report_date.desc()).limit(500)
        rows = scoped(q, CustomerMetric)
        return build("Customers",
                     ["Date", "BU", "Footfall", "Transactions", "Complaints", "Satisfaction (1-5)"],
                     [[c.report_date.isoformat() if c.report_date else "", bu_names.get(c.business_unit_id, ""),
                       c.footfall, c.transactions, c.complaints,
                       float(c.satisfaction_score) if c.satisfaction_score is not None else ""] for c in rows])

    raise HTTPException(400, "tab must be actions/documents/staff/customers")


