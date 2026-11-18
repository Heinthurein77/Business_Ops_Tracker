import logging
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.auth import (
    RESET_REQUEST_TTL_HOURS,
    InvalidPasswordError,
    assert_token_current,
    bump_token_version,
    create_access_token,
    decode_access_token,
    get_current_user,
    get_optional_user,
    hash_password,
    is_temp_password_expired,
    password_was_used,
    record_password_history,
    resolve_redirect_path,
    serialize_user,
    verify_password,
    warn_null_bu_ceo,
)
from src.client_ip import get_client_ip
from src.constants import NotificationType, Role
from src.config import settings
from src.database import get_db
from src.models import PasswordResetRequest, User
from src.schemas import (
    ForcePasswordChangeRequest,
    ForgotPasswordRequest,
    LoginRequest,
    TotpConfirmRequest,
    TotpDisableRequest,
    TotpEnrollRequest,
    PASSWORD_MAX_BYTES,
    password_policy_error,
)
from src.totp import (
    decode_totp_setup_token,
    issue_totp_setup_token,
    new_totp_secret,
    totp_provisioning_uri,
    verify_totp_code,
)
from src.throttle import (
    PASSWORD_CHANGE_FAILED,
    PASSWORD_RESET_REQUESTED,
    Throttled,
    check_forgot_password_throttle,
    check_login_throttle,
    check_password_change_throttle,
    normalize_username,
)
from src.utils import log_audit, notify, utcnow

logger = logging.getLogger(__name__)

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
        "Ask the Chairman — they will share a temporary password with you once approved."
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
def me(request: Request, user: User = Depends(get_current_user)):
    # SM-1: the session-expiry warning needs the real expiry, and the
    # cookie is httponly so the frontend cannot read it itself. Decode the
    # cookie's `exp` claim here (signature-verified, same as auth) and
    # report it; the frontend shows "session expires in 15 min" from
    # 7 h 45 m after login. None when the cookie cannot be decoded — the
    # session itself was already validated by get_current_user.
    expires_at = None
    payload = decode_access_token(request.cookies.get(settings.COOKIE_NAME) or "")
    if payload:
        expires_at = datetime.fromtimestamp(payload["exp"], tz=timezone.utc).isoformat()
    return {
        "user": serialize_user(user),
        "redirect_to": resolve_redirect_path(user),
        "session_expires_at": expires_at,
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

    # bcrypt silently ignores everything past byte 72: a longer password
    # whose first 72 bytes match the stored hash would authenticate, so
    # reject it here before any verification (creation already caps at
    # 72 bytes, but the login path itself did not enforce the cap).
    if len(payload.password.encode("utf-8")) > PASSWORD_MAX_BYTES:
        log_audit(db, user, "LOGIN_FAILED", f"username={username}", request, username=username)
        raise HTTPException(status_code=401, detail=_LOGIN_FAILED_DETAIL)

    password_ok = verify_password(payload.password, user.hashed_password if user else _DUMMY_HASH)
    # PM-20: an expired Chairman-issued temporary password gets its own
    # message instead of the generic "wrong password" — the user can
    # otherwise retry forever assuming a typo. This deliberately reveals
    # that the account exists and its temp password lapsed (accepted
    # trade-off, per the PM review); it fires only when the password
    # itself was correct.
    if user and user.is_active and password_ok and is_temp_password_expired(user):
        log_audit(db, user, "LOGIN_FAILED", f"username={username}", request, username=username)
        raise HTTPException(
            status_code=401,
            detail="Your temporary password has expired — ask the Chairman for a new one",
        )
    # An expired Chairman-issued temporary password is a wrong password: same
    # generic 401, same code path and timing as any other failure.
    if not user or not user.is_active or not password_ok or is_temp_password_expired(user):
        # Recorded under the (sanitised) attempted name even when no such
        # account exists, so the per-username throttle can count it.
        log_audit(db, user, "LOGIN_FAILED", f"username={username}", request, username=username)
        raise HTTPException(status_code=401, detail=_LOGIN_FAILED_DETAIL)

    # M-01: second factor, checked before any session is issued. The
    # Chairman may not sign in at all without an enrolled TOTP: a correct
    # password alone earns only a short-lived setup token, and the caller
    # must complete enrollment (below) before a session exists.
    if user.role == Role.CHAIRMAN and not user.totp_enabled:
        return JSONResponse(
            status_code=403,
            content={
                "detail": "TOTP_ENROLLMENT_REQUIRED",
                "code": "TOTP_ENROLLMENT_REQUIRED",
                "totp_setup_token": issue_totp_setup_token(user.username),
            },
        )
    if user.totp_enabled:
        if not payload.totp_code:
            # B-m6: a plain HTTPException can only carry `detail`, so the
            # machine code rides in a JSONResponse body instead — same
            # status, same `detail` value, plus a stable `code` field.
            return JSONResponse(
                status_code=401,
                content={"detail": "TOTP_REQUIRED", "code": "TOTP_REQUIRED"},
            )
        if not verify_totp_code(user.totp_secret, payload.totp_code):
            log_audit(db, user, "LOGIN_FAILED", f"username={username}", request, username=username)
            raise HTTPException(status_code=401, detail=_LOGIN_FAILED_DETAIL)

    user.last_login_at = utcnow()
    db.commit()
    # Sm-7: login is a session-establishment point — flag a CEO whose row
    # has no BU (a data inconsistency that would also skip the upload
    # owner check), the same warning get_current_user emits per request.
    warn_null_bu_ceo(user)
    log_audit(db, user, "LOGIN_SUCCESS", "", request)
    if user.role == Role.CHAIRMAN:
        # m-17: the bootstrap password file has served its purpose once
        # the Chairman signs in — delete it so the credential stops
        # existing on disk. Local import: main.py imports this router, so
        # a top-level import would be circular. Best effort only: login
        # must never fail over cleanup.
        try:
            from src.main import _delete_bootstrap_password_file

            _delete_bootstrap_password_file()
        except Exception:  # noqa: BLE001 - cleanup must not break login
            logger.warning("Could not delete the bootstrap password file after Chairman login")

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
    if not user or not user.is_active or user.role == Role.CHAIRMAN:
        # The Chairman has nobody above him to approve a request; his
        # recovery is the out-of-band `python -m src.scripts.reset_chairman`.
        return None, None
    return user, pending


def _open_reset_request(db: Session, user: User, pending) -> bool:
    """Adds a Pending request for `user` unless a live one already exists.
    A Pending request older than the approval window is closed first, otherwise
    it would sit in the way of the one-Pending-per-user index forever."""
    if pending is not None:
        if utcnow() - pending.requested_at <= timedelta(hours=RESET_REQUEST_TTL_HOURS):
            return False
        pending.status = "Rejected"
        pending.resolved_at = utcnow()
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
        created = False
        if user is not None and not state.throttled:
            created = _open_reset_request(db, user, pending)
        if created:
            # PM-7: the Chairman only learned of a reset request by
            # opening Manage Users. Notify every active Chairman now
            # (link /admin/users), reusing the existing bell.
            for chairman in db.query(User).filter(User.role == Role.CHAIRMAN, User.is_active == True).all():  # noqa: E712
                notify(
                    db, chairman.id, NotificationType.ALERT,
                    f"Password reset requested: {user.username}",
                    f"{user.username} asked for a password reset — approve or reject it in User Management.",
                    link="/admin/users",
                )
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

    # SM-1: re-check the session is still current before a commit that
    # revokes every session (the bump below) — a logout landing mid-request
    # must not be papered over by issuing a fresh cookie afterwards.
    assert_token_current(db, user)

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
    # m-27: rotating back to a recent password is a reuse, not a change.
    if password_was_used(db, user, payload.new_password):
        raise HTTPException(
            status_code=400,
            detail="That password was used recently — choose a different one",
        )

    try:
        old_hash = user.hashed_password
        user.hashed_password = hash_password(payload.new_password)
    except InvalidPasswordError:
        raise HTTPException(status_code=400, detail="Password contains invalid characters or is too long")
    # m-27: record both the outgoing hash and the new one — otherwise a
    # rotation back to the immediately previous password would not be
    # caught (the old password was set before history existed).
    record_password_history(db, user, old_hash)
    record_password_history(db, user)
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


# ---------------------------------------------------------------------------
# TOTP second factor (M-01)
# ---------------------------------------------------------------------------

def _resolve_totp_user(db: Session, session_user, setup_token):
    """The account a TOTP enrollment/confirm call acts on: the live session
    when there is one, else the username carried by a valid setup token
    (issued by login() after a correct password for a Chairman with no
    enrolled TOTP). Neither credential alone is a session — enrollment
    only stores a secret until a code proves possession."""
    if session_user is not None:
        return session_user
    username = decode_totp_setup_token(setup_token)
    if username is None:
        return None
    return db.query(User).filter(User.username == username).first()


@router.post("/totp/enroll")
def totp_enroll(
    payload: TotpEnrollRequest,
    request: Request,
    user: User = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """Starts (or restarts) TOTP enrollment: generates a fresh secret,
    stores it encrypted, and returns the otpauth URL + secret for the
    authenticator app. The secret is NOT enabled until /totp/confirm
    proves possession with a code."""
    target = _resolve_totp_user(db, user, payload.setup_token)
    if target is None or not target.is_active:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if target.totp_enabled:
        raise HTTPException(
            status_code=400,
            detail="Two-factor authentication is already enabled for this account",
        )
    secret = new_totp_secret()
    target.totp_secret = secret
    target.totp_enabled = False
    target.totp_enrolled_at = None
    db.commit()
    log_audit(db, target, "TOTP_ENROLL_STARTED", "", request)
    return {"otpauth_url": totp_provisioning_uri(secret, target.username), "secret": secret}


@router.post("/totp/confirm")
def totp_confirm(
    payload: TotpConfirmRequest,
    request: Request,
    user: User = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """Completes enrollment: the code proves the owner holds the
    authenticator the secret was just shown to. Only after this does
    login() start demanding a code."""
    target = _resolve_totp_user(db, user, payload.setup_token)
    if target is None or not target.is_active:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if target.totp_enabled:
        raise HTTPException(
            status_code=400,
            detail="Two-factor authentication is already enabled for this account",
        )
    if not target.totp_secret:
        raise HTTPException(
            status_code=400,
            detail="Start two-factor setup first — no enrollment is in progress",
        )
    if not verify_totp_code(target.totp_secret, payload.code):
        log_audit(db, target, "TOTP_CONFIRM_FAILED", "", request)
        raise HTTPException(
            status_code=400,
            detail="That code is not valid — check your authenticator app and try again",
        )
    target.totp_enabled = True
    target.totp_enrolled_at = utcnow()
    db.commit()
    log_audit(db, target, "TOTP_ENABLED", "", request)
    return {"ok": True}


@router.post("/totp/disable")
def totp_disable_self(
    payload: TotpDisableRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Removes the caller's own TOTP enrollment (proving possession with
    a current code). Every session is revoked: a fresh sign-in is
    required, and a Chairman must enroll again before any new session is
    issued."""
    if not user.totp_enabled:
        raise HTTPException(
            status_code=400,
            detail="Two-factor authentication is not enabled for this account",
        )
    if not verify_totp_code(user.totp_secret, payload.code):
        raise HTTPException(status_code=400, detail="That code is not valid")
    user.totp_secret = None
    user.totp_enabled = False
    user.totp_enrolled_at = None
    bump_token_version(db, user)
    log_audit(db, user, "TOTP_DISABLED", "", request)
    return {"ok": True}
