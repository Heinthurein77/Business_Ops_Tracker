from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, Request
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from src.config import settings
from src.database import get_db
from src.models import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# A password-reset request stays approvable for this long; after that it is
# stale and the user has to ask again.
RESET_REQUEST_TTL_HOURS = 72


class NotAuthenticatedException(Exception):
    """Raised when no valid session cookie is present."""


class InsufficientRoleException(Exception):
    """Raised when an authenticated user's role cannot access a route."""

    def __init__(self, redirect_to: str = "/login"):
        self.redirect_to = redirect_to


class PasswordChangeRequiredException(Exception):
    """Raised when a user with a pending mandatory password rotation calls any
    API route other than the handful needed to complete that rotation."""


class ProfileSetupRequiredException(Exception):
    """Raised when a CEO with an incomplete mandatory profile calls any API
    route other than the handful needed to complete it. Checked after the
    password-rotation gate, so a CEO always finishes changing their password
    first and only then is steered into profile setup."""


# Routes an account mid-rotation is still allowed to call.
_PASSWORD_CHANGE_EXEMPT_PATHS = {
    "/api/auth/me",
    "/api/auth/logout",
    "/api/auth/force-password-change",
}

# Routes a CEO with an incomplete profile is still allowed to call.
_PROFILE_SETUP_EXEMPT_PATHS = {
    "/api/auth/me",
    "/api/auth/logout",
    "/api/ceo/profile",
}


def role_home_path(role: str) -> str:
    if role == "CEO":
        return "/ceo"
    if role in ("Chairman", "HO"):
        return "/admin"
    return "/login"


def resolve_redirect_path(user: User) -> str:
    """Single source of truth for "where should this user land right now" —
    used by /login, /auth/me, and /force-password-change so all three always
    agree. Order matters: a pending password rotation always comes first,
    then an incomplete CEO profile, then the user's normal role home."""
    if user.require_password_change:
        return "/force-password-change"
    if user.role == "CEO" and not user.is_profile_completed:
        return "/profile-setup"
    return role_home_path(user.role)


def bump_token_version(db: Session, user: User) -> None:
    """Increments token_version as a single atomic `UPDATE ... SET
    token_version = token_version + 1` (computed server-side, not read then
    written back), so a concurrent bump on the same account — e.g. logout
    racing a password reset, or a Chairman resetting their own password via
    the admin endpoint that also targets `user.id` — can't be lost the way
    a plain `user.token_version += 1; db.commit()` could: two concurrent
    requests would both read the same starting value in Python and the
    second commit would clobber the first, undercounting distinct
    invalidation events by one. Deliberately not a SELECT ... FOR UPDATE
    re-read instead — SessionLocal is created with autoflush=False (see
    database.py), so re-reading `user` mid-request would silently discard
    whatever other uncommitted attribute changes a caller already set on it
    before calling this (e.g. force_password_change's own hashed_password
    update). Commits the whole session, then refreshes `user` so its
    in-memory token_version — used right after by callers that reissue the
    cookie — reflects the real post-increment value."""
    db.query(User).filter(User.id == user.id).update(
        {User.token_version: User.token_version + 1}, synchronize_session=False
    )
    db.commit()
    db.refresh(user)


class InvalidPasswordError(ValueError):
    """The password can't be hashed at all (NUL byte, absurd length). Routers
    turn this into a clean 4xx instead of letting passlib's ValueError become
    a 500."""


def hash_password(password: str) -> str:
    try:
        return pwd_context.hash(password)
    except ValueError as exc:  # passlib PasswordValueError / PasswordSizeError
        raise InvalidPasswordError("Password contains invalid characters or is too long") from exc


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except ValueError:
        # A password passlib refuses to process (NUL byte, oversize) can never
        # match a stored hash — a failed check, not a server error.
        return False


def is_temp_password_expired(user: User, now: Optional[datetime] = None) -> bool:
    """True when the account's password is a Chairman-issued temporary one
    whose lifetime has run out. Timestamps are naive UTC (see models.py)."""
    if user.temp_password_expires_at is None:
        return False
    return user.temp_password_expires_at < (now or datetime.utcnow())


def create_access_token(subject: str, token_version: int = 0, expires_minutes: Optional[int] = None) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=expires_minutes or settings.JWT_EXPIRE_MINUTES)
    payload = {"sub": subject, "tv": token_version, "exp": expire, "iat": now}
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    """Verifies signature and expiry against the single pinned algorithm, and
    requires exp, sub and tv to be present — a token without a `tv` claim
    (minted before token versioning, or forged by someone who somehow holds
    the key) is rejected outright rather than assumed to be version 0."""
    try:
        return jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            options={"require": ["exp", "sub", "tv"]},
        )
    except jwt.InvalidTokenError:
        return None


def _user_from_token(request: Request, db: Session) -> Optional[User]:
    """The one place a session cookie becomes a User: valid token, account
    exists and is active, and the token's `tv` equals the user's current
    token_version. Tokens issued before a logout or password change carry the
    previous version and are rejected here even though they haven't expired
    yet — see the column's comment in models.py."""
    token = request.cookies.get(settings.COOKIE_NAME)
    if not token:
        return None
    payload = decode_access_token(token)
    if not payload:
        return None
    subject, token_version = payload.get("sub"), payload.get("tv")
    if not isinstance(subject, str) or not isinstance(token_version, int) or isinstance(token_version, bool):
        return None
    user = db.query(User).filter(User.username == subject).first()
    if not user or not user.is_active:
        return None
    if token_version != user.token_version:
        return None
    return user


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = _user_from_token(request, db)
    if user is None:
        raise NotAuthenticatedException()

    if user.require_password_change:
        if request.url.path not in _PASSWORD_CHANGE_EXEMPT_PATHS:
            raise PasswordChangeRequiredException()
        # Still mid-rotation: don't also evaluate the profile-setup gate
        # below yet, or a CEO who needs both could never reach the
        # force-password-change endpoint to clear the first one.
        return user

    if (
        user.role == "CEO"
        and not user.is_profile_completed
        and request.url.path not in _PROFILE_SETUP_EXEMPT_PATHS
    ):
        raise ProfileSetupRequiredException()

    return user


def get_optional_user(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    """Same validity checks as get_current_user (active + token_version),
    just without raising when there's no session — for routes like logout
    that must work whether or not the caller is still validly signed in."""
    return _user_from_token(request, db)


def require_roles(*roles: str):
    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise InsufficientRoleException(redirect_to=role_home_path(user.role))
        return user

    return dependency


def serialize_user(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "full_name": user.full_name,
        "role": user.role,
        "business_unit_id": user.business_unit_id,
        "require_password_change": user.require_password_change,
        "profile_completed": user.is_profile_completed,
    }
