"""Operational extensions: action items, notifications, staff records,
customer metrics, documents, and forecasting.

Access model mirrors the rest of the app:
- CEO: scoped to their own business unit
- Chairman / HO ("admin"): all business units
"""
import json
import logging
import threading
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.auth import get_current_user, require_roles
from src.constants import ACTION_ITEM_STATUSES, ACTION_ITEM_TRANSITIONS, NotificationType, Role
from src.services.ops_alerts import (  # noqa: F401 - re-exported for callers/tests
    MISSING_REPORT_HOUR,
    _action_item_link,
    count_missing_report_recipients,
    run_check_document_expiry,
    run_remind_action_items,
    run_remind_missing_reports,
)
from src.database import get_db
from src.schemas import ActionItemIn, ActionItemUpdate, CustomerIn, DocumentIn, StaffIn
from src.reports.ops_reports import OPS_EXPORT_MAX_ROWS  # PM-m5: builders live there now
from src.routers.month_params import parse_month_or_422
from src.utils import _sanitize_excel_cell, clamp_days, clamp_limit, clamp_offset, log_audit, notify, page_response, today_local, utcnow
from src.models import (
    ActionItem,
    ActivityLog,
    BusinessUnit,
    CeoProfile,
    CustomerMetric,
    DailyFinancial,
    HeadOfficeActivityLog,
    IdempotencyKey,
    LicenseDocument,
    MonthlyTarget,
    Notification,
    StaffRecord,
    User,
)

router = APIRouter(prefix="/api/ops", tags=["ops"])
require_admin = require_roles(Role.CHAIRMAN, Role.HO)

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


# ---------------------------------------------------------------- m-10: Idempotency-Key

# Max key length: a client-generated UUID is 36 chars; 128 leaves room
# for prefixed keys while keeping the column bounded.
MAX_IDEMPOTENCY_KEY_LEN = 128


def _idempotency_key(request: Optional[Request]) -> Optional[str]:
    """The caller's Idempotency-Key header, or None when absent.

    A key that is only whitespace counts as absent; an over-long key is
    a 400 (it can never match a stored key, so failing fast beats
    silently creating a duplicate row)."""
    if request is None:
        return None
    key = (request.headers.get("Idempotency-Key") or "").strip()
    if not key:
        return None
    if len(key) > MAX_IDEMPOTENCY_KEY_LEN:
        raise HTTPException(status_code=400, detail="Idempotency-Key must be at most 128 characters")
    return key


def _idempotency_replay(db: Session, user, endpoint: str, key: str):
    """The stored first response for (user, endpoint, key), or None."""
    row = (
        db.query(IdempotencyKey)
        .filter(IdempotencyKey.user_id == user.id,
                IdempotencyKey.endpoint == endpoint,
                IdempotencyKey.idempotency_key == key)
        .first()
    )
    if row is None:
        return None
    return JSONResponse(status_code=row.response_status,
                        content=json.loads(row.response_body))


def _idempotency_store(db: Session, user, endpoint: str, key: str,
                       status: int, body: dict):
    """Store the first response for (user, endpoint, key).

    Returns None on success, or the winner's stored response when a
    concurrent replay won the unique-constraint race first — the
    caller then returns that replay instead of its own fresh row."""
    try:
        db.add(IdempotencyKey(
            user_id=user.id, endpoint=endpoint, idempotency_key=key,
            response_status=status, response_body=json.dumps(body),
        ))
        db.commit()
    except IntegrityError:
        db.rollback()
        return _idempotency_replay(db, user, endpoint, key)
    return None

# ---------------------------------------------------------------- helpers

def bu_scope(user: User, bu_id: Optional[int]) -> Optional[int]:
    """Resolve which BU the caller may touch. CEOs are pinned to their own:
    naming another BU is a 403, naming none means their own BU.
    Chairman/HO may name any BU (or none, for all)."""
    if user.role == Role.CEO:
        if bu_id is not None and bu_id != user.business_unit_id:
            raise HTTPException(status_code=403, detail="CEOs can only access their own business unit")
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
    total = q.count()
    limit, offset = clamp_limit(limit), clamp_offset(offset)
    items = q.order_by(ActionItem.due_date.is_(None), ActionItem.due_date, ActionItem.created_at.desc()).offset(offset).limit(limit).all()
    return page_response([serialize_action_item(db, i) for i in items], total, limit, offset)


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
        raise HTTPException(status_code=400, detail="Assigned user does not exist")
    if not assignee.is_active:
        raise HTTPException(status_code=400, detail="Assigned user is not active")
    if user.role == Role.CEO and assignee.business_unit_id != user.business_unit_id:
        raise HTTPException(status_code=400, detail="Assignee must be in your business unit")
    return assignee


@router.post("/action-items")
def create_action_item(payload: ActionItemIn, request: Request = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # m-10: a retried request with the same Idempotency-Key replays the
    # first response instead of creating a second action item.
    endpoint = "POST /api/ops/action-items"
    key = _idempotency_key(request)
    if key is not None:
        replay = _idempotency_replay(db, user, endpoint, key)
        if replay is not None:
            return replay
    bu_id = bu_scope(user, payload.business_unit_id)
    if bu_id is None:
        raise HTTPException(status_code=400, detail="business_unit_id is required")
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
        notify(db, item.assigned_to_id, NotificationType.ACTION_ASSIGNED,
               "New action item assigned", item.title, link=_action_item_link(assignee))
        db.commit()
    _log_audit(db, user, "CREATE_ACTION_ITEM", f"id={item.id}")
    body = serialize_action_item(db, item)
    if key is not None:
        replay = _idempotency_store(db, user, endpoint, key, 200, body)
        if replay is not None:
            return replay
    return body


@router.patch("/action-items/{item_id}")
def update_action_item(item_id: int, payload: ActionItemUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = db.query(ActionItem).filter(ActionItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Action item not found")
    bu_scope(user, item.business_unit_id)
    old_status = item.status
    old_assignee_id = item.assigned_to_id
    was_done = old_status == "done"
    if payload.status is not None:
        if payload.status not in ACTION_ITEM_STATUSES:
            raise HTTPException(status_code=400, detail="Invalid status")
        # PM-m3: transitions are the ACTION_ITEM_TRANSITIONS table in
        # src/constants.py — any->any used to be allowed, so done ->
        # in_progress silently "un-did" a completion.
        if payload.status != old_status and payload.status not in ACTION_ITEM_TRANSITIONS.get(old_status, ()):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status transition from {old_status} to {payload.status}",
            )
        item.status = payload.status
        item.completed_at = utcnow() if payload.status == "done" else None
    if payload.title is not None:
        item.title = payload.title.strip()
    if payload.description is not None:
        item.description = payload.description
    new_assignee = None
    if payload.assigned_to_id is not None:
        new_assignee = _validate_assignee(db, user, payload.assigned_to_id)
        item.assigned_to_id = payload.assigned_to_id
    if payload.due_date is not None:
        item.due_date = payload.due_date
    db.commit()
    db.refresh(item)
    # PM-m3: a reassignment notifies the NEW assignee (the old assignee
    # is not pinged — they simply stop seeing it in "mine").
    if new_assignee is not None and item.assigned_to_id != old_assignee_id and item.assigned_to_id != user.id:
        notify(db, item.assigned_to_id, NotificationType.ACTION_ASSIGNED,
               "Action item assigned to you", item.title, link=_action_item_link(new_assignee))
        db.commit()
    if was_done and item.status == "pending":
        # PM-m3: reopening a done item keeps a trace — the old code just
        # cleared completed_at with no record the item was ever done.
        _log_audit(db, user, "REOPEN_ACTION_ITEM",
                   f"item_id={item_id} was done, reopened to pending")
    else:
        _log_audit(db, user, "UPDATE_ACTION_ITEM", f"item_id={item_id}")
    return serialize_action_item(db, item)


@router.delete("/action-items/{item_id}")
def delete_action_item(item_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = db.query(ActionItem).filter(ActionItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Action item not found")
    bu_scope(user, item.business_unit_id)
    old_assignee_id = item.assigned_to_id
    title = item.title
    db.delete(item)
    db.commit()
    # PM-m3: deletion used to be silent for the assignee — tell them the
    # item they were working on is gone.
    if old_assignee_id and old_assignee_id != user.id:
        assignee = db.query(User).filter(User.id == old_assignee_id).first()
        if assignee is not None:
            notify(db, old_assignee_id, NotificationType.ALERT,
                   "Action item deleted", f"'{title}' was deleted.", link=_action_item_link(assignee))
            db.commit()
    _log_audit(db, user, "DELETE_ACTION_ITEM", f"item_id={item_id}")
    return {"ok": True}


# ---------------------------------------------------------------- notifications

@router.get("/notifications")
def list_notifications(unread_only: bool = False, limit: int = 100, offset: int = 0,
                       user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # PM-21: was capped at the latest 100 with no pagination and no
    # archive page — older alerts became unreachable. Paginated now
    # (same page_response envelope shape as the other list endpoints,
    # plus unread_count for the bell).
    q = db.query(Notification).filter(Notification.user_id == user.id)
    if unread_only:
        q = q.filter(Notification.is_read == False)  # noqa: E712
    total = q.count()
    limit, offset = clamp_limit(limit), clamp_offset(offset)
    rows = q.order_by(Notification.created_at.desc()).offset(offset).limit(limit).all()
    unread = db.query(Notification).filter(Notification.user_id == user.id, Notification.is_read == False).count()  # noqa: E712
    return {
        "unread_count": unread,
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [
            {
                "id": n.id, "type": n.type, "title": n.title, "message": n.message,
                "link": n.link, "is_read": n.is_read,
                "created_at": n.created_at.isoformat() if n.created_at else None,
            }
            for n in rows
        ],
    }


@router.get("/notifications/missing-count")
def missing_report_count(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """PM-2: how many CEOs a manual 'Remind missing reports' would ping
    right now, and the hour the scheduled run uses — shown on the button
    before anything is sent."""
    return {
        "count": count_missing_report_recipients(db),
        "missing_report_hour": MISSING_REPORT_HOUR,
    }


@router.post("/notifications/{notification_id}/read")
def mark_notification_read(notification_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    n = db.query(Notification).filter(Notification.id == notification_id, Notification.user_id == user.id).first()
    if not n:
        raise HTTPException(status_code=404, detail="Notification not found")
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
        raise HTTPException(status_code=400, detail="Invalid attachment key")
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
            raise HTTPException(status_code=404, detail="Attachment not found")
        if user.role not in (Role.CHAIRMAN, Role.HO) and profile_owner_id != user.id:
            raise HTTPException(status_code=403, detail="Not your profile photo")
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
                if user.role not in (Role.CHAIRMAN, Role.HO):
                    raise HTTPException(status_code=403, detail="Only Chairman/HO can view head-office attachments")
            else:
                raise HTTPException(status_code=404, detail="Attachment not found")
        # m-26: fail closed — the old `and user.business_unit_id`
        # condition skipped the 403 entirely for a NULL-BU CEO. A CEO
        # whose BU is unset now matches nothing, instead of everything.
        if owner_bu_id is not None and user.role == Role.CEO:
            if owner_bu_id != user.business_unit_id:
                raise HTTPException(status_code=403, detail="Attachment belongs to another business unit")
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
        raise HTTPException(status_code=503, detail="File storage is not available")
    return RedirectResponse(url=url, status_code=302, headers=no_store)


# ---------------------------------------------------------------- staff

@router.post("/staff")
def submit_staff(payload: StaffIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bu_id = bu_scope(user, payload.business_unit_id)
    if bu_id is None:
        raise HTTPException(status_code=400, detail="business_unit_id is required")
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
    since = today_local() - timedelta(days=clamp_days(days))
    q = db.query(StaffRecord).filter(StaffRecord.report_date >= since)
    if bu_id is not None:
        q = q.filter(StaffRecord.business_unit_id == bu_id)
    total = q.count()
    limit, offset = clamp_limit(limit), clamp_offset(offset)
    rows = q.order_by(StaffRecord.report_date.desc()).offset(offset).limit(limit).all()
    return page_response([
        {"id": r.id, "business_unit_id": r.business_unit_id, "report_date": r.report_date.isoformat(),
         "total_staff": r.total_staff, "present": r.present, "absent": r.absent,
         "new_hires": r.new_hires, "resignations": r.resignations, "notes": r.notes}
        for r in rows
    ], total, limit, offset)


# ---------------------------------------------------------------- customers

@router.post("/customers")
def submit_customers(payload: CustomerIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bu_id = bu_scope(user, payload.business_unit_id)
    if bu_id is None:
        raise HTTPException(status_code=400, detail="business_unit_id is required")
    if payload.satisfaction_score is not None and not (1.0 <= payload.satisfaction_score <= 5.0):
        raise HTTPException(status_code=400, detail="satisfaction_score must be between 1.0 and 5.0")
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
    since = today_local() - timedelta(days=clamp_days(days))
    q = db.query(CustomerMetric).filter(CustomerMetric.report_date >= since)
    if bu_id is not None:
        q = q.filter(CustomerMetric.business_unit_id == bu_id)
    total = q.count()
    limit, offset = clamp_limit(limit), clamp_offset(offset)
    rows = q.order_by(CustomerMetric.report_date.desc()).offset(offset).limit(limit).all()
    return page_response([
        {"id": r.id, "business_unit_id": r.business_unit_id, "report_date": r.report_date.isoformat(),
         "footfall": r.footfall, "transactions": r.transactions, "complaints": r.complaints,
         "satisfaction_score": float(r.satisfaction_score) if r.satisfaction_score is not None else None,
         "notes": r.notes}
        for r in rows
    ], total, limit, offset)


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
        "business_unit_id": bu_id,
        # PM-16: label the forecast cards with the selected BU name, or
        # the group total when all are selected (was unlabeled, so a
        # Chairman could not tell one BU from all 23).
        "business_unit_name": (
            next((bu.name for bu in db.query(BusinessUnit).all() if bu.id == bu_id), None)
            if bu_id is not None else "All Business Units (group total)"
        ),
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
    limit, offset = clamp_limit(limit), clamp_offset(offset)
    rows = q.order_by(LicenseDocument.expiry_date.asc()).offset(offset).limit(limit).all()
    items = [serialize_document(db, d) for d in rows]
    # B-m2: "count" renamed to "total" (+ limit/offset) — the shared envelope.
    return page_response(items, total, limit, offset)


@router.post("/documents")
def create_document(payload: DocumentIn, request: Request = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # m-10: same Idempotency-Key replay as create_action_item.
    endpoint = "POST /api/ops/documents"
    key = _idempotency_key(request)
    if key is not None:
        replay = _idempotency_replay(db, user, endpoint, key)
        if replay is not None:
            return replay
    bu_id = bu_scope(user, payload.business_unit_id)
    if bu_id is None:
        raise HTTPException(status_code=400, detail="business_unit_id is required")
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
    body = serialize_document(db, d)
    if key is not None:
        replay = _idempotency_store(db, user, endpoint, key, 200, body)
        if replay is not None:
            return replay
    return body


@router.delete("/documents/{document_id}")
def delete_document(document_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    d = db.query(LicenseDocument).filter(LicenseDocument.id == document_id).first()
    if not d:
        raise HTTPException(status_code=404, detail="Document not found")
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
        # m-01: clamp before the audit line so the logged value is the one used.
        days = clamp_days(days)
        _log_audit(db, user, "CHECK_DOCUMENT_EXPIRY", f"days={days}")
        return run_check_document_expiry(db, days)


# ---------------------------------------------------------------- monthly chairman PDF report

@router.get("/monthly-report")
def monthly_report(month: Optional[str] = None, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """One-page-per-BU monthly summary PDF for the Chairman (admin roles only).

    `month` format YYYY-MM; defaults to the current month. PM-m5: the
    PDF is built by src/reports/ops_reports.py; this router only
    validates, audits and streams it.
    """
    from io import BytesIO

    from fastapi.responses import StreamingResponse

    from src.reports.ops_reports import build_monthly_report_pdf

    # Validate the month first (422 on a bad month, before any audit row),
    # matching the old inline order.
    parse_month_or_422(month)
    # M-04: the Chairman PDF export — audit who pulled it, for which month.
    _log_audit(db, user, "MONTHLY_REPORT_EXPORT", f"month={month}")
    data, filename = build_monthly_report_pdf(db, month)
    return StreamingResponse(BytesIO(data), media_type="application/pdf",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ---------------------------------------------------------------- Excel export

@router.get("/export-excel")
def export_excel(tab: str = "actions", business_unit_id: Optional[int] = None,
                 from_date: Optional[date] = None, to_date: Optional[date] = None,
                 limit: int = OPS_EXPORT_MAX_ROWS,
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Download one Daily Ops tab as an .xlsx workbook.

    PM-m5: the workbook is built by src/reports/ops_reports.py; this
    router only scopes, audits and streams it. Row-capped at `limit`
    (max 5000); the actions tab defaults to the last 90 days unless
    from_date/to_date are given. Staff and customers were already capped
    at 500 and stay that way.
    """
    from io import BytesIO

    from fastapi.responses import StreamingResponse

    from src.reports.ops_reports import build_ops_excel

    bu_id = bu_scope(user, business_unit_id)
    # M-04: exports carry PII/health/financial data — audit who pulled
    # which tab (never the rows themselves).
    _log_audit(db, user, "OPS_EXCEL_EXPORT", f"tab={tab} bu_id={bu_id}")
    data, filename = build_ops_excel(db, bu_id, tab=tab, from_date=from_date, to_date=to_date, limit=limit)
    return StreamingResponse(
        BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
