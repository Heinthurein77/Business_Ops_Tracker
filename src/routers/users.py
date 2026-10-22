"""
Chairman-only user management: list every account, reset a password, toggle
active/inactive, onboard a new CEO/HO account, and work the queue of
password-reset requests people file from the sign-in page.

Every temporary password issued here is random (never a shared constant),
expires (TEMP_PASSWORD_TTL_HOURS_*), forces a change at first sign-in, and is
returned once in a `Cache-Control: no-store` response — the Chairman passes it
to the user out of band.
"""
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.auth import RESET_REQUEST_TTL_HOURS, bump_token_version, hash_password, require_roles
from src.client_ip import get_client_ip
from src.config import settings
from src.database import get_db
from src.models import AuditLog, BusinessUnit, PasswordResetRequest, User
from src.schemas import CreateUserRequest, SetUserActiveRequest, UpdateUserRequest
from src.throttle import LOGIN_UNLOCK, login_lockout_remaining
from src.utils import format_local_datetime, log_audit

# One router for /api/admin/users/* and /api/admin/password-resets/* — main.py
# includes this object as-is, so both families of paths hang off /api/admin.
router = APIRouter(prefix="/api/admin", tags=["users"])
require_chairman = require_roles("Chairman")


def _new_temporary_password() -> str:
    return secrets.token_urlsafe(12)


def _apply_temporary_password(target: User, ttl_hours: int) -> str:
    """Sets a fresh random temporary password on `target` (not yet committed —
    callers finish with bump_token_version, which commits and revokes every
    session issued under the old password) and returns it in clear, once."""
    temporary_password = _new_temporary_password()
    target.hashed_password = hash_password(temporary_password)
    target.require_password_change = True
    target.temp_password_expires_at = datetime.utcnow() + timedelta(hours=ttl_hours)
    return temporary_password


def _resolve_pending_requests(db: Session, user_id: int, status: str, resolved_by: int) -> int:
    """Closes any open reset request for the user (bulk UPDATE, committed with
    the caller's own commit) so the Password Requests queue never keeps
    showing a request that has already been dealt with another way."""
    return (
        db.query(PasswordResetRequest)
        .filter(PasswordResetRequest.user_id == user_id, PasswordResetRequest.status == "Pending")
        .update(
            {"status": status, "resolved_at": datetime.utcnow(), "resolved_by": resolved_by},
            synchronize_session=False,
        )
    )


def _is_stale(reset_request: PasswordResetRequest) -> bool:
    return datetime.utcnow() - reset_request.requested_at > timedelta(hours=RESET_REQUEST_TTL_HOURS)


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


@router.get("/users")
def list_users(user: User = Depends(require_chairman), db: Session = Depends(get_db)):
    users = db.query(User).filter(User.role.in_(["CEO", "HO", "Accountant"])).order_by(User.role.desc(), User.business_unit_id).all()
    business_units = {bu.id: bu for bu in db.query(BusinessUnit).all()}

    entries = []
    for u in users:
        bu = business_units.get(u.business_unit_id)
        entries.append(
            {
                "id": u.id,
                "username": u.username,
                "full_name": u.full_name,
                "role": u.role,
                "business_unit_id": u.business_unit_id,
                "business_unit_code": bu.code if bu else None,
                "business_unit_name": bu.name if bu else None,
                "is_active": u.is_active,
                "require_password_change": u.require_password_change,
                # True while a login lockout (src/throttle.py) is active for
                # this account — drives the "Unlock login" action below.
                "login_locked": login_lockout_remaining(db, u.username) is not None,
                "last_login_display": format_local_datetime(u.last_login_at) if u.last_login_at else None,
                "created_display": format_local_datetime(u.created_at),
            }
        )
    return {"users": entries}


@router.post("/users")
def create_user(
    payload: CreateUserRequest,
    request: Request,
    response: Response,
    user: User = Depends(require_chairman),
    db: Session = Depends(get_db),
):
    if db.query(User).filter(User.username == payload.username).first():
        raise HTTPException(status_code=400, detail=f'Username "{payload.username}" is already taken')

    business_unit_id = None
    if payload.role in ("CEO", "Accountant"):
        # CEOs and accountants both belong to exactly one business unit:
        # the CEO runs it, the accountant keeps its books — and the
        # accounting API pins a BU-assigned accountant to that BU.
        if payload.business_unit_id is None:
            raise HTTPException(status_code=400, detail=f"A business unit is required for a {payload.role} account")
        bu = db.query(BusinessUnit).filter(BusinessUnit.id == payload.business_unit_id).first()
        if not bu:
            raise HTTPException(status_code=404, detail="Business unit not found")
        business_unit_id = bu.id
    if payload.role == "CEO":
        # Every dashboard query keys its CEO lookup by business_unit_id
        # assuming exactly one CEO per BU (see admin.py's ceo_by_bu, and
        # ceo.py's own "strictly one CEO per business unit" comment) — a
        # second CEO row for the same BU makes which one "wins" in those
        # lookups undefined. Deactivating the old account first (rather
        # than deleting it) is the supported way to replace a BU's CEO.
        existing_ceo = (
            db.query(User).filter(User.role == "CEO", User.business_unit_id == bu.id, User.is_active == True).first()
        )
        if existing_ceo:
            raise HTTPException(
                status_code=400,
                detail=f'"{bu.name}" already has an active CEO account ("{existing_ceo.username}") — deactivate it first before creating a replacement.',
            )

    new_user = User(
        username=payload.username,
        hashed_password="!",  # replaced just below; never a usable hash
        full_name=payload.full_name.strip(),
        role=payload.role,
        business_unit_id=business_unit_id,
        require_password_change=True,
        # Mirrors _ensure_user's seeding convention in main.py — only a CEO
        # has a mandatory profile to complete.
        is_profile_completed=(payload.role != "CEO"),
        is_active=True,
    )
    temporary_password = _apply_temporary_password(new_user, settings.TEMP_PASSWORD_TTL_HOURS_NEW_ACCOUNT)
    db.add(new_user)
    try:
        db.commit()
    except IntegrityError:
        # Lost a race for the username, or for the one-active-CEO-per-BU
        # unique index — the pre-checks above can't close that window alone.
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="That account could not be created: the username is taken or this business unit already has an active CEO.",
        )

    log_audit(db, user, "USER_CREATED", f"username={new_user.username} role={new_user.role}", request)

    _no_store(response)
    return {
        "ok": True,
        "user_id": new_user.id,
        "temporary_password": temporary_password,
        "expires_in_hours": settings.TEMP_PASSWORD_TTL_HOURS_NEW_ACCOUNT,
    }


@router.post("/users/{user_id}/reset-password")
def reset_user_password(
    user_id: int,
    request: Request,
    response: Response,
    user: User = Depends(require_chairman),
    db: Session = Depends(get_db),
):
    if user_id == user.id:
        raise HTTPException(status_code=400, detail="You cannot reset your own password here — use Change Password")

    target = db.query(User).filter(User.id == user_id).with_for_update().first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.role == "Chairman":
        raise HTTPException(status_code=400, detail="The Chairman account cannot be reset from the app")

    temporary_password = _apply_temporary_password(target, settings.TEMP_PASSWORD_TTL_HOURS_RESET)

    # If the user had already asked for this reset, that request is now
    # answered; otherwise still leave an Approved row as the record of it.
    if not _resolve_pending_requests(db, target.id, "Approved", user.id):
        db.add(
            PasswordResetRequest(
                user_id=target.id,
                username=target.username,
                status="Approved",
                resolved_at=datetime.utcnow(),
                resolved_by=user.id,
            )
        )
    bump_token_version(db, target)

    log_audit(db, user, "USER_PASSWORD_RESET_BY_ADMIN", f"target_username={target.username}", request)

    _no_store(response)
    return {
        "ok": True,
        "temporary_password": temporary_password,
        "expires_in_hours": settings.TEMP_PASSWORD_TTL_HOURS_RESET,
    }


@router.post("/users/{user_id}/active")
def set_user_active(
    user_id: int,
    payload: SetUserActiveRequest,
    request: Request,
    user: User = Depends(require_chairman),
    db: Session = Depends(get_db),
):
    if user_id == user.id and not payload.is_active:
        raise HTTPException(status_code=400, detail="You cannot deactivate your own account")

    target = db.query(User).filter(User.id == user_id).with_for_update().first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    if payload.is_active and target.role == "CEO" and target.business_unit_id is not None:
        other_ceo = (
            db.query(User)
            .filter(
                User.role == "CEO",
                User.business_unit_id == target.business_unit_id,
                User.is_active == True,
                User.id != target.id,
            )
            .first()
        )
        if other_ceo:
            raise HTTPException(
                status_code=409,
                detail=f'This business unit already has an active CEO ("{other_ceo.username}") — deactivate that account first.',
            )

    target.is_active = payload.is_active
    if not payload.is_active:
        # A deactivated account's outstanding reset request is moot.
        _resolve_pending_requests(db, target.id, "Rejected", user.id)
    try:
        # Bumping token_version on BOTH transitions: deactivation kills every
        # session it issued, and the bump on reactivation makes sure a session
        # from before an earlier deactivation can never come back to life.
        bump_token_version(db, target)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="This business unit already has an active CEO — deactivate that account first.",
        )

    log_audit(
        db,
        user,
        "USER_ACTIVATED" if payload.is_active else "USER_DEACTIVATED",
        f"target_username={target.username}",
        request,
    )

    return {"ok": True}


@router.patch("/users/{user_id}")
def update_user(
    user_id: int,
    payload: UpdateUserRequest,
    request: Request,
    user: User = Depends(require_chairman),
    db: Session = Depends(get_db),
):
    """Change an account's business-unit assignment.

    The assignment is access control, not bookkeeping: a CEO runs the BU,
    an accountant is pinned to it by the accounting API, and Chairman/HO
    are group-level (never assigned). Moving an account therefore bumps
    its token_version — every live session dies and the holder signs back
    in under the new scope, instead of an open tab silently keeping access
    to the wrong BU's books."""
    target = db.query(User).filter(User.id == user_id).with_for_update().first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    new_bu_id = payload.business_unit_id
    if target.role in ("CEO", "Accountant") and new_bu_id is None:
        raise HTTPException(
            status_code=400,
            detail=f"A {target.role} account must be assigned to a business unit",
        )
    if target.role in ("Chairman", "HO") and new_bu_id is not None:
        raise HTTPException(
            status_code=400,
            detail=f"{target.role} accounts are group-level and cannot be assigned to a business unit",
        )
    if new_bu_id is not None:
        bu = db.query(BusinessUnit).filter(BusinessUnit.id == new_bu_id).first()
        if not bu:
            raise HTTPException(status_code=404, detail="Business unit not found")

    if target.business_unit_id == new_bu_id:
        return {"ok": True, "user_id": target.id, "business_unit_id": new_bu_id}

    if target.role == "CEO" and target.is_active:
        other_ceo = (
            db.query(User)
            .filter(
                User.role == "CEO",
                User.business_unit_id == new_bu_id,
                User.is_active == True,
                User.id != target.id,
            )
            .first()
        )
        if other_ceo:
            raise HTTPException(
                status_code=409,
                detail=f'This business unit already has an active CEO ("{other_ceo.username}") — deactivate that account first.',
            )

    target.business_unit_id = new_bu_id
    try:
        # Commits the assignment and revokes every session issued under
        # the old one (same pattern as set_user_active).
        bump_token_version(db, target)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="This business unit already has an active CEO — deactivate that account first.",
        )

    log_audit(
        db,
        user,
        "USER_BU_UPDATED",
        f"target_username={target.username} business_unit_id={new_bu_id}",
        request,
    )
    return {"ok": True, "user_id": target.id, "business_unit_id": new_bu_id}


@router.post("/users/{user_id}/unlock-login")
def unlock_user_login(
    user_id: int,
    request: Request,
    user: User = Depends(require_roles("Chairman", "HO")),
    db: Session = Depends(get_db),
):
    """Clears an active login lockout for one account (see src/throttle.py).

    A lockout normally expires on its own at the fixed time recorded when it
    triggered; this is the manual override for when the lock is keeping the
    real account holder out (e.g. they are on a new network, where the
    trusted-IP bypass can't recognise them). The LOGIN_UNLOCK marker row is
    also the throttle's cycle boundary: failures recorded before it stop
    counting toward a new lockout, so the account starts with a clean slate
    rather than re-locking on the next single mistake. Chairman/HO only —
    every other user-admin action on this router is Chairman-only, but
    unlocking is a routine support task HO staff handle day to day; it grants
    no access by itself (the account still needs its password)."""
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    was_locked = login_lockout_remaining(db, target.username) is not None
    if was_locked:
        db.add(
            AuditLog(
                user_id=target.id,
                username=target.username,
                action=LOGIN_UNLOCK,
                details=f"unlocked_by={user.username}",
                ip_address=get_client_ip(request),
            )
        )
        db.commit()
        log_audit(db, user, "USER_LOGIN_UNLOCKED", f"target_username={target.username}", request)
    return {"ok": True, "was_locked": was_locked}


# --- Password-reset requests (filed anonymously from the sign-in page; see
# forgot_password in routers/auth.py). Nothing happens to an account until the
# Chairman approves here. ---


@router.get("/password-resets")
def list_password_resets(user: User = Depends(require_chairman), db: Session = Depends(get_db)):
    rows = (
        db.query(PasswordResetRequest)
        .order_by(PasswordResetRequest.requested_at.desc(), PasswordResetRequest.id.desc())
        .limit(50)
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

    fresh_since = datetime.utcnow() - timedelta(hours=RESET_REQUEST_TTL_HOURS)
    pending_count = (
        db.query(PasswordResetRequest)
        .filter(PasswordResetRequest.status == "Pending", PasswordResetRequest.requested_at >= fresh_since)
        .count()
    )
    return {"requests": entries, "pending_count": pending_count}


def _load_pending_request(db: Session, request_id: int) -> PasswordResetRequest:
    reset_request = db.query(PasswordResetRequest).filter(PasswordResetRequest.id == request_id).with_for_update().first()
    if not reset_request:
        raise HTTPException(status_code=404, detail="Reset request not found")
    if reset_request.status != "Pending":
        raise HTTPException(status_code=409, detail=f"This request was already {reset_request.status.lower()}.")
    return reset_request


@router.post("/password-resets/{request_id}/approve")
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
    if target.role == "Chairman":
        raise HTTPException(status_code=400, detail="The Chairman account cannot be reset from the app")
    if not target.is_active:
        raise HTTPException(status_code=400, detail="This account is deactivated — reactivate it before issuing a password")

    temporary_password = _apply_temporary_password(target, settings.TEMP_PASSWORD_TTL_HOURS_RESET)
    reset_request.status = "Approved"
    reset_request.resolved_at = datetime.utcnow()
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


@router.post("/password-resets/{request_id}/reject")
def reject_password_reset(
    request_id: int,
    request: Request,
    user: User = Depends(require_chairman),
    db: Session = Depends(get_db),
):
    reset_request = _load_pending_request(db, request_id)
    reset_request.status = "Rejected"
    reset_request.resolved_at = datetime.utcnow()
    reset_request.resolved_by = user.id
    db.commit()

    log_audit(db, user, "PASSWORD_RESET_REJECTED", f"target_username={reset_request.username}", request)
    return {"ok": True}
