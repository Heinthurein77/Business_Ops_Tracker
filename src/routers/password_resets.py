"""
Chairman-only password-reset approval queue: list the requests people
file from the sign-in page, approve one (issues a random temporary
password) or reject it.

Split out of src/routers/users.py (PM-m13): user management and the
reset-approval queue are separate lifecycles sharing a prefix
(/api/admin) — they now live in separate router files, so a change to
one cannot silently touch the other. The paths are unchanged, so the
frontend (frontend/src/api/client.js: listPasswordResets /
approvePasswordReset / rejectPasswordReset) and the role matrix in
tests/test_auth_matrix.py keep working as-is.
"""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from src.auth import RESET_REQUEST_TTL_HOURS, bump_token_version, record_password_history, require_roles
from src.config import settings
from src.constants import Role
from src.database import get_db
from src.models import BusinessUnit, PasswordResetRequest, User
from src.routers.users import _apply_temporary_password, _no_store
from src.utils import format_local_datetime, log_audit, page_response, utcnow

# Own router, same paths as before the split: /api/admin/password-resets/*
router = APIRouter(prefix="/api/admin/password-resets", tags=["password-resets"])
require_chairman = require_roles(Role.CHAIRMAN)


def _is_stale(reset_request: PasswordResetRequest) -> bool:
    return utcnow() - reset_request.requested_at > timedelta(hours=RESET_REQUEST_TTL_HOURS)


@router.get("")
def list_password_resets(
    limit: int = 50,
    offset: int = 0,
    user: User = Depends(require_chairman),
    db: Session = Depends(get_db),
):
    # PM-m9: paged like report_logs (admin.py) instead of the old
    # hard .limit(50) with no parameters. Default 50 keeps the Manage
    # Users page — which passes no params — behaving exactly as before.
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    total = db.query(PasswordResetRequest).count()
    rows = (
        db.query(PasswordResetRequest)
        .order_by(PasswordResetRequest.requested_at.desc(), PasswordResetRequest.id.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    users_by_id = {u.id: u for u in db.query(User).filter(User.id.in_({r.user_id for r in rows})).all()} if rows else {}
    business_units = {bu.id: bu for bu in db.query(BusinessUnit).all()}

    entries = []
    for r in rows:
        requester = users_by_id.get(r.user_id)
        bu = business_units.get(requester.business_unit_id) if requester else None
        entries.append(
            {
                "id": r.id,
                "username": r.username,
                "full_name": requester.full_name if requester else None,
                "business_unit_name": bu.name if bu else None,
                "requested_display": format_local_datetime(r.requested_at),
                "status": r.status,
                # A Pending request older than the approval window can no
                # longer be approved (the user must ask again).
                "expired": r.status == "Pending" and _is_stale(r),
            }
        )

    fresh_since = utcnow() - timedelta(hours=RESET_REQUEST_TTL_HOURS)
    pending_count = (
        db.query(PasswordResetRequest)
        .filter(PasswordResetRequest.status == "Pending", PasswordResetRequest.requested_at >= fresh_since)
        .count()
    )
    # page_response keeps the "requests" key the frontend already reads
    # (ManageUsersPage.jsx), plus total/limit/offset for paging.
    response = page_response(entries, total, limit, offset, key="requests")
    response["pending_count"] = pending_count
    return response


def _load_pending_request(db: Session, request_id: int) -> PasswordResetRequest:
    reset_request = db.query(PasswordResetRequest).filter(PasswordResetRequest.id == request_id).with_for_update().first()
    if not reset_request:
        raise HTTPException(status_code=404, detail="Reset request not found")
    if reset_request.status != "Pending":
        raise HTTPException(status_code=409, detail=f"This request was already {reset_request.status.lower()}.")
    return reset_request


@router.post("/{request_id}/approve")
def approve_password_reset(
    request_id: int,
    request: Request,
    response: Response,
    user: User = Depends(require_chairman),
    db: Session = Depends(get_db),
):
    reset_request = _load_pending_request(db, request_id)
    if _is_stale(reset_request):
        raise HTTPException(
            status_code=409,
            detail=f"This request is more than {RESET_REQUEST_TTL_HOURS} hours old and has expired — ask the user to submit a new one.",
        )

    target = db.query(User).filter(User.id == reset_request.user_id).with_for_update().first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.role == Role.CHAIRMAN:
        raise HTTPException(status_code=400, detail="The Chairman account cannot be reset from the app")
    if not target.is_active:
        raise HTTPException(status_code=400, detail="This account is deactivated — reactivate it before issuing a password")

    old_hash = target.hashed_password
    temporary_password = _apply_temporary_password(target, settings.TEMP_PASSWORD_TTL_HOURS_RESET)
    # m-27: record both the outgoing hash and the new temporary one, so
    # the owner cannot rotate straight back to the password that was
    # just reset away.
    record_password_history(db, target, old_hash)
    record_password_history(db, target)
    reset_request.status = "Approved"
    reset_request.resolved_at = utcnow()
    reset_request.resolved_by = user.id
    # Commits the password, the request's new status and the version bump
    # together, so a crash can't leave a working temporary password behind a
    # request that still reads Pending.
    bump_token_version(db, target)

    log_audit(db, user, "PASSWORD_RESET_APPROVED", f"target_username={target.username}", request)

    _no_store(response)
    return {
        "ok": True,
        "username": target.username,
        "temporary_password": temporary_password,
        "expires_in_hours": settings.TEMP_PASSWORD_TTL_HOURS_RESET,
    }


@router.post("/{request_id}/reject")
def reject_password_reset(
    request_id: int,
    request: Request,
    user: User = Depends(require_chairman),
    db: Session = Depends(get_db),
):
    reset_request = _load_pending_request(db, request_id)
    reset_request.status = "Rejected"
    reset_request.resolved_at = utcnow()
    reset_request.resolved_by = user.id
    db.commit()

    log_audit(db, user, "PASSWORD_RESET_REJECTED", f"target_username={reset_request.username}", request)
    return {"ok": True}
