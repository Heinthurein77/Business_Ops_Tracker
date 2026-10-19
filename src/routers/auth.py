import re
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.auth import (
    RESET_REQUEST_TTL_HOURS,
    InvalidPasswordError,
    bump_token_version,
    create_access_token,
    get_current_user,
    get_optional_user,
    hash_password,
    is_temp_password_expired,
    resolve_redirect_path,
    serialize_user,
    verify_password,
)
from src.client_ip import get_client_ip
from src.config import settings
from src.database import get_db
from src.models import PasswordResetRequest, User
from src.schemas import ForcePasswordChangeRequest, ForgotPasswordRequest, LoginRequest, password_policy_error
from src.throttle import (
    PASSWORD_CHANGE_FAILED,
    PASSWORD_RESET_REQUESTED,
    Throttled,
    check_forgot_password_throttle,
    check_login_throttle,
    check_password_change_throttle,
    normalize_username,
)
from src.utils import log_audit

router = APIRouter(prefix="/api/auth", tags=["auth"])

# A precomputed hash of a value nobody will ever type, verified even when the
# username doesn't exist so a non-existent-user response takes the same time
# as a wrong-password one — otherwise response latency leaks which usernames
# are registered (verify_password's bcrypt call is the slow part).
_DUMMY_HASH = hash_password("no-such-account-timing-guard-8f3c2a")

_LOGIN_FAILED_DETAIL = "Invalid username or password"
_TOO_MANY_LOGINS_DETAIL = "Too many sign-in attempts. Please wait a few minutes and try again."
_TOO_MANY_PASSWORD_CHANGES_DETAIL = "Too many failed attempts. Please wait a few minutes and try again."

# Only ever this text, whatever the input — see forgot_password().
_FORGOT_PASSWORD_RESPONSE = {
    "ok": True,
    "message": (
        "If this account exists, a reset request has been sent to the Chairman. "
        "You will receive a temporary password once it is approved."
    ),
}
_RESETTABLE_USERNAME = re.compile(r"[a-z0-9_]{1,80}")


def _issue_session_cookie(response: Response, user: User) -> None:
    token = create_access_token(subject=user.username, token_version=user.token_version)
    response.set_cookie(
        key=settings.COOKIE_NAME,
        value=token,
        httponly=True,
        secure=settings.SECURE_COOKIES,
        samesite="lax",
        max_age=settings.JWT_EXPIRE_MINUTES * 60,
        path="/",
    )


def _too_many_requests(throttled: Throttled, detail: str) -> HTTPException:
    return HTTPException(status_code=429, detail=detail, headers={"Retry-After": str(throttled.retry_after)})


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return {
        "user": serialize_user(user),
        "redirect_to": resolve_redirect_path(user),
    }


@router.post("/login")
def login(payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    client_ip = get_client_ip(request)
    username = normalize_username(payload.username)

    # Throttle first, from the database, before any bcrypt work — a blocked
    # caller costs us three indexed counts, not a password hash. The answer is
    # identical for real and made-up usernames, and a throttled attempt writes
    # no audit row of its own.
    throttled = check_login_throttle(db, username, client_ip)
    if throttled:
        raise _too_many_requests(throttled, _TOO_MANY_LOGINS_DETAIL)

    user = db.query(User).filter(User.username == username).first()

    password_ok = verify_password(payload.password, user.hashed_password if user else _DUMMY_HASH)
    # An expired Chairman-issued temporary password is a wrong password: same
    # generic 401, same code path and timing as any other failure.
    if not user or not user.is_active or not password_ok or is_temp_password_expired(user):
        # Recorded under the (sanitised) attempted name even when no such
        # account exists, so the per-username throttle can count it.
        log_audit(db, user, "LOGIN_FAILED", f"username={username}", request, username=username)
        raise HTTPException(status_code=401, detail=_LOGIN_FAILED_DETAIL)

    user.last_login_at = datetime.utcnow()
    db.commit()
    log_audit(db, user, "LOGIN_SUCCESS", "", request)

    _issue_session_cookie(response, user)
    response.headers["Cache-Control"] = "no-store"
    return {
        "user": serialize_user(user),
        "redirect_to": resolve_redirect_path(user),
    }


@router.post("/logout")
def logout(request: Request, response: Response, user: User = Depends(get_optional_user), db: Session = Depends(get_db)):
    if user:
        # Bumping token_version invalidates this token immediately (rather
        # than relying on the client to actually discard the deleted
        # cookie) and also invalidates any other copy of it — e.g. the same
        # session open in a second browser, or a copy an attacker captured
        # before this logout.
        bump_token_version(db, user)
        log_audit(db, user, "LOGOUT", "", request)
    # Same attributes the cookie was set with: a __Host- cookie may only be
    # written (including expired, i.e. deleted) with Secure and Path=/, and
    # browsers ignore the deletion otherwise.
    response.delete_cookie(
        settings.COOKIE_NAME, path="/", secure=settings.SECURE_COOKIES, httponly=True, samesite="lax"
    )
    return {"ok": True}


def _pending_reset_request(db: Session, user_id: int):
    return (
        db.query(PasswordResetRequest)
        .filter(PasswordResetRequest.user_id == user_id, PasswordResetRequest.status == "Pending")
        .first()
    )


def _find_requestable_user(db: Session, username: str):
    """(user, pending_request) for an account a reset request may be opened
    for, else (None, None). Always runs
    the same account and pending-request lookups whether or not the name
    exists, so a hit and a miss cost the database the same."""
    user = None
    if _RESETTABLE_USERNAME.fullmatch(username):
        user = db.query(User).filter(User.username == username).first()
    pending = _pending_reset_request(db, user.id if user else 0)
    if not user or not user.is_active or user.role == "Chairman":
        # The Chairman has nobody above him to approve a request; his
        # recovery is the out-of-band `python -m src.scripts.reset_chairman`.
        return None, None
    return user, pending


def _open_reset_request(db: Session, user: User, pending) -> bool:
    """Adds a Pending request for `user` unless a live one already exists.
    A Pending request older than the approval window is closed first, otherwise
    it would sit in the way of the one-Pending-per-user index forever."""
    if pending is not None:
        if datetime.utcnow() - pending.requested_at <= timedelta(hours=RESET_REQUEST_TTL_HOURS):
            return False
        pending.status = "Rejected"
        pending.resolved_at = datetime.utcnow()
        db.flush()
    db.add(PasswordResetRequest(user_id=user.id, username=user.username, status="Pending"))
    return True


@router.post("/forgot-password")
def forgot_password(payload: ForgotPasswordRequest, request: Request, db: Session = Depends(get_db)):
    """Asks the Chairman to reset the caller's password. It never changes a
    password, never touches sessions, and never runs bcrypt — an anonymous
    caller can at most put an entry in the Chairman's approval queue.

    The response is byte-for-byte the same for every input: real account,
    unknown name, malformed name, Chairman, deactivated account, throttled
    caller. One audit row is written on every path (the throttle counts
    unknown names too), and the request row rides in the same commit."""
    client_ip = get_client_ip(request)
    username = normalize_username(payload.username)

    state = check_forgot_password_throttle(db, username, client_ip)
    user, pending = _find_requestable_user(db, username)

    try:
        if user is not None and not state.throttled:
            _open_reset_request(db, user, pending)
        if state.record_audit:
            log_audit(db, None, PASSWORD_RESET_REQUESTED, "", request, username=username)  # commits both rows
        else:
            db.commit()
    except IntegrityError:
        # A concurrent request for the same account won the one-Pending-per-
        # user index. Same answer; still leave the audit row.
        db.rollback()
        if state.record_audit:
            log_audit(db, None, PASSWORD_RESET_REQUESTED, "", request, username=username)

    return _FORGOT_PASSWORD_RESPONSE


@router.post("/force-password-change")
def force_password_change(
    payload: ForcePasswordChangeRequest,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Counted from PASSWORD_CHANGE_FAILED rows, so a stolen session can't be
    # used to grind through guesses at the current password.
    throttled = check_password_change_throttle(db, user.username)
    if throttled:
        raise _too_many_requests(throttled, _TOO_MANY_PASSWORD_CHANGES_DETAIL)

    if payload.new_password != payload.confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match")
    policy_error = password_policy_error(payload.new_password, username=user.username)
    if policy_error:
        raise HTTPException(status_code=400, detail=policy_error)

    if is_temp_password_expired(user):
        log_audit(db, user, PASSWORD_CHANGE_FAILED, "reason=temporary_password_expired", request)
        raise HTTPException(
            status_code=400,
            detail="Your temporary password has expired. Ask the Chairman to issue a new one.",
        )
    if not verify_password(payload.current_password, user.hashed_password):
        log_audit(db, user, PASSWORD_CHANGE_FAILED, "reason=current_password_mismatch", request)
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if payload.new_password == payload.current_password:
        raise HTTPException(status_code=400, detail="New password must be different from your current password")

    try:
        user.hashed_password = hash_password(payload.new_password)
    except InvalidPasswordError:
        raise HTTPException(status_code=400, detail="Password contains invalid characters or is too long")
    user.require_password_change = False
    user.temp_password_expires_at = None
    bump_token_version(db, user)
    log_audit(db, user, "PASSWORD_CHANGED", "", request)

    # The bump above invalidates the cookie the browser is still holding —
    # reissue it now with the new token_version so this request's own
    # session survives the password change instead of bouncing straight
    # back to the login page.
    _issue_session_cookie(response, user)
    response.headers["Cache-Control"] = "no-store"

    return {"ok": True, "redirect_to": resolve_redirect_path(user)}
