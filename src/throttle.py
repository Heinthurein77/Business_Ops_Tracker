"""
Database-backed throttling for the unauthenticated auth endpoints.

State lives in the audit_logs table (rows that are written anyway), so limits
survive restarts and hold across multiple app instances, and nothing here can
grow without bound the way an in-process dict of timestamps did. Every count
filters on (action, username | ip_address, timestamp) — the composite indexes
created by migration a7d4e1c9b2f6 — so a check costs an index range scan, not
a table scan.

Each function takes an explicit `db` (and `now` where useful) and returns
plain data, so it is easy to unit-test against a throwaway database.

Lock-out safety: a login block on a *username* never applies to an IP that has
already signed in as that user within the last 30 days, so someone hammering an
account from elsewhere cannot lock its real owner out of their usual network.

Lock-out lifetime (M1): when the per-username failure limit is reached, the
lockout *triggers* once — a LOGIN_LOCKOUT audit row records the moment and
the duration, and Chairman/HO users get an in-app notification. The expiry is
fixed at trigger time: attempts made while the lock is active never extend
it and never count toward the next one (only failures recorded after the
expiry — or after an admin's LOGIN_UNLOCK row — count toward a new lockout).
That bounds the spray-to-keep-locked denial of service to one fixed window
per trigger, and the lock is always visible and clearable in User Management.
"""
import ipaddress
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Query, Session

from src.models import AuditLog, Notification, User

MAX_USERNAME_LEN = 80

# --- Login ---
LOGIN_FAILED = "LOGIN_FAILED"
LOGIN_SUCCESS = "LOGIN_SUCCESS"
LOGIN_LOCKOUT = "LOGIN_LOCKOUT"
LOGIN_UNLOCK = "LOGIN_UNLOCK"
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_IP_LIMIT = 20
LOGIN_USER_LIMIT = 5
LOGIN_TRUSTED_IP_DAYS = 30
LOGIN_BREAKER_WINDOW_SECONDS = 60
LOGIN_BREAKER_LIMIT = 300
LOGIN_MAX_RETRY_SECONDS = 15 * 60

# --- Forgot password ---
PASSWORD_RESET_REQUESTED = "PASSWORD_RESET_REQUESTED"
FORGOT_IP_LIMIT = 5
FORGOT_IP_WINDOW_SECONDS = 3600
FORGOT_USER_LIMIT = 3
FORGOT_USER_WINDOW_SECONDS = 24 * 3600
# Past this many requests from one IP in the window, stop even writing the
# audit row: the request is already being ignored, and an unauthenticated
# caller must not be able to grow the audit table at line rate.
FORGOT_IP_AUDIT_CAP = 50
# Global flood guard, for when no per-IP rule can apply (see ip_is_throttle_key):
# past this many requests in a minute, from anyone, stop creating requests and
# writing rows.
FORGOT_BREAKER_WINDOW_SECONDS = 60
FORGOT_BREAKER_LIMIT = 200

# --- Failed password changes (authenticated, guessing the current password) ---
PASSWORD_CHANGE_FAILED = "PASSWORD_CHANGE_FAILED"
PASSWORD_CHANGE_LIMIT = 5
PASSWORD_CHANGE_WINDOW_SECONDS = 15 * 60

_CONTROL_CHARS = re.compile("[\x00-\x1f\x7f-\x9f\u2028\u2029]")


logger = logging.getLogger("business_ops_tracker.throttle")

_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_warned_private_ip = False


def ip_is_throttle_key(ip: Optional[str]) -> bool:
    """Whether `ip` identifies a single real client. A private / loopback /
    link-local / unspecified / carrier-grade-NAT address means the app is
    looking at the platform's own proxy (CLIENT_IP_HEADER or
    TRUSTED_PROXY_HOPS misconfigured), where one address stands for EVERY user.
    Keying limits on it would let an attacker's 20 failures lock everybody
    out, so per-IP rules (and the trusted-IP bypass) are skipped for such
    addresses — the per-username rules and the global breaker still apply."""
    global _warned_private_ip
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    shared = (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_unspecified
        or (isinstance(addr, ipaddress.IPv4Address) and addr in _CGNAT)
    )
    if shared and not _warned_private_ip:
        _warned_private_ip = True
        logger.warning(
            "Client IP %s looks like a private address - per-IP throttling is disabled for such "
            "addresses. Check CLIENT_IP_HEADER / TRUSTED_PROXY_HOPS (and run uvicorn with --no-proxy-headers).",
            ip,
        )
    return not shared


def strip_control_chars(value: str, replacement: str = "") -> str:
    """Removes (or replaces) control characters, newlines and Unicode line
    separators, so caller-supplied text can't forge log lines or smuggle NUL
    bytes into a database driver."""
    return _CONTROL_CHARS.sub(replacement, value)


def normalize_username(raw: Optional[str]) -> str:
    """The canonical form of an attempted username: control characters
    removed, trimmed, lower-cased, truncated to the column width. Used for the
    account lookup AND for what is recorded/counted, so the two always agree."""
    return strip_control_chars(raw or "").strip().lower()[:MAX_USERNAME_LEN]


@dataclass(frozen=True)
class Throttled:
    retry_after: int  # seconds


def _utcnow() -> datetime:
    # audit_logs.timestamp is a naive UTC datetime (models.py: default=utcnow).
    return datetime.utcnow()


def _rows(db: Session, action: str, since: datetime, username: Optional[str] = None, ip: Optional[str] = None) -> Query:
    query = db.query(AuditLog).filter(AuditLog.action == action, AuditLog.timestamp >= since)
    if username is not None:
        query = query.filter(AuditLog.username == username)
    if ip is not None:
        query = query.filter(AuditLog.ip_address == ip)
    return query


def _count(db: Session, action: str, since: datetime, username: Optional[str] = None, ip: Optional[str] = None) -> int:
    return _rows(db, action, since, username, ip).with_entities(func.count(AuditLog.id)).scalar() or 0


def _seconds_until_below_limit(
    db: Session, action: str, window_seconds: int, limit: int, count: int, now: datetime,
    username: Optional[str] = None, ip: Optional[str] = None,
) -> int:
    """How long until the rolling window holds fewer than `limit` matching rows,
    i.e. until enough of the oldest ones age out. One extra query, paid only on
    the (already rejected) blocked path."""
    since = now - timedelta(seconds=window_seconds)
    oldest_to_expire = (
        _rows(db, action, since, username, ip)
        .with_entities(AuditLog.timestamp)
        .order_by(AuditLog.timestamp.asc())
        .offset(max(count - limit, 0))
        .limit(1)
        .scalar()
    )
    if oldest_to_expire is None:
        return window_seconds
    remaining = (oldest_to_expire + timedelta(seconds=window_seconds) - now).total_seconds()
    return max(1, int(remaining) + 1)


def _has_recent_success(db: Session, username: str, ip: Optional[str], now: datetime) -> bool:
    if ip is None:
        return False
    since = now - timedelta(days=LOGIN_TRUSTED_IP_DAYS)
    return _rows(db, LOGIN_SUCCESS, since, username, ip).with_entities(AuditLog.id).first() is not None


_LOCKOUT_DURATION_RE = re.compile(r"duration_seconds=(\d+)")


def _lock_state(db: Session, username: str, now: datetime) -> tuple[Optional[int], Optional[datetime]]:
    """(seconds remaining, epoch) for the username's current lock cycle.

    The cycle is defined by the newest LOGIN_LOCKOUT / LOGIN_UNLOCK marker
    row for the username. A lockout's expiry is FIXED at trigger time
    (trigger timestamp + the duration recorded on the row) — nothing that
    happens during the lock moves it. `epoch` is the instant from which
    failures count toward a NEW lockout: the lockout's expiry, or the
    unlock time, so failures from before/during a lock never re-trigger one.
    """
    event = (
        db.query(AuditLog)
        .filter(AuditLog.action.in_((LOGIN_LOCKOUT, LOGIN_UNLOCK)), AuditLog.username == username)
        .order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
        .first()
    )
    if event is None:
        return None, None
    if event.action == LOGIN_UNLOCK:
        return None, event.timestamp
    match = _LOCKOUT_DURATION_RE.search(event.details or "")
    duration = int(match.group(1)) if match else LOGIN_MAX_RETRY_SECONDS
    expiry = event.timestamp + timedelta(seconds=duration)
    if now < expiry:
        return max(1, int((expiry - now).total_seconds()) + 1), expiry
    return None, expiry


def login_lockout_remaining(db: Session, username: str, now: Optional[datetime] = None) -> Optional[int]:
    """Seconds left on the username's active login lockout, or None when the
    account is not locked. Used by the admin user list (the `login_locked`
    flag) and the unlock endpoint."""
    remaining, _epoch = _lock_state(db, username, now or _utcnow())
    return remaining


def _trigger_lockout(db: Session, username: str, now: datetime, n_user: int, duration: int) -> None:
    """Persists a lockout trigger: the LOGIN_LOCKOUT marker row (which fixes
    the expiry) plus one in-app notification per active Chairman/HO user, so
    the attack is visible the moment it happens. Runs exactly once per
    lockout — attempts during the lock return early in check_login_throttle
    and never reach this. Commits: the caller answers 429 immediately after,
    with no later commit of its own."""
    target = db.query(User).filter(User.username == username).first()
    db.add(
        AuditLog(
            user_id=target.id if target else None,
            username=username,
            action=LOGIN_LOCKOUT,
            details=f"duration_seconds={duration} failures={n_user}",
            timestamp=now,
        )
    )
    minutes = max(1, round(duration / 60))
    for admin in db.query(User).filter(User.role.in_(("Chairman", "HO")), User.is_active == True).all():  # noqa: E712
        db.add(
            Notification(
                user_id=admin.id,
                type="login_lockout",
                title=f"Login locked: {username}",
                message=(
                    f"{n_user} failed sign-in attempts in {LOGIN_WINDOW_SECONDS // 60} minutes locked "
                    f"the account '{username}' for about {minutes} minute{'s' if minutes != 1 else ''}. "
                    "Attempts during the lock cannot extend it. If this was not the account holder, "
                    "investigate — you can clear the lock early from User Management."
                ),
                link="/admin/users",
            )
        )
    db.commit()
    logger.warning("Login lockout triggered for username=%s failures=%d duration=%ds", username, n_user, duration)


def user_backoff_seconds(n_user: int) -> int:
    """Exponential backoff floor for a username block: 1 min at the 5th
    failure, doubling per further failure, capped at 15 min."""
    exponent = max(n_user - LOGIN_USER_LIMIT, 0)
    return min(2**exponent * 60, LOGIN_MAX_RETRY_SECONDS)


def check_login_throttle(db: Session, username: str, ip: Optional[str], now: Optional[datetime] = None) -> Optional[Throttled]:
    """Call BEFORE any password hashing. Returns None to proceed or a
    Throttled(retry_after) to answer 429. Callers must not record a
    LOGIN_FAILED row for a throttled attempt (the breaker in particular exists
    to stop write amplification). Side effect: the check that first observes
    the per-username limit being reached persists the lockout trigger (see
    _trigger_lockout) — one write per lockout, never per attempt."""
    now = now or _utcnow()

    # Global circuit breaker: a flood far beyond anything legitimate (a
    # distributed guessing run, or a bug) — refuse cheaply, write nothing.
    breaker_since = now - timedelta(seconds=LOGIN_BREAKER_WINDOW_SECONDS)
    if _count(db, LOGIN_FAILED, breaker_since) > LOGIN_BREAKER_LIMIT:
        return Throttled(LOGIN_BREAKER_WINDOW_SECONDS)

    since = now - timedelta(seconds=LOGIN_WINDOW_SECONDS)

    # None from here on means "no usable per-client address" (see ip_is_throttle_key).
    ip = ip if ip_is_throttle_key(ip) else None

    if ip is not None:
        n_ip = _count(db, LOGIN_FAILED, since, ip=ip)
        if n_ip >= LOGIN_IP_LIMIT:
            wait = _seconds_until_below_limit(db, LOGIN_FAILED, LOGIN_WINDOW_SECONDS, LOGIN_IP_LIMIT, n_ip, now, ip=ip)
            return Throttled(min(wait, LOGIN_MAX_RETRY_SECONDS))

    # Username rule, on the fixed-expiry lockout cycle (see _lock_state):
    # during an active lock the caller is refused with the time left on the
    # ORIGINAL trigger — attempts now cannot extend it — and the failure
    # count for a new lock starts from the cycle epoch, so nothing recorded
    # before or during this lock can re-trigger the next one.
    trusted = _has_recent_success(db, username, ip, now)
    remaining, epoch = _lock_state(db, username, now)
    if remaining is not None:
        return None if trusted else Throttled(remaining)
    if trusted:
        return None

    count_since = since if epoch is None or epoch < since else epoch
    n_user = _count(db, LOGIN_FAILED, count_since, username=username)
    if n_user >= LOGIN_USER_LIMIT:
        wait = _seconds_until_below_limit(db, LOGIN_FAILED, LOGIN_WINDOW_SECONDS, LOGIN_USER_LIMIT, n_user, now, username=username)
        # The exponential figure is a floor and the age-out wait is what the
        # block used to last; whichever is longer becomes the FIXED duration
        # recorded on the trigger row (never more than the cap).
        duration = min(max(user_backoff_seconds(n_user), wait), LOGIN_MAX_RETRY_SECONDS)
        _trigger_lockout(db, username, now, n_user, duration)
        return Throttled(duration)

    return None


@dataclass(frozen=True)
class ForgotPasswordState:
    throttled: bool      # do not create a reset request
    record_audit: bool   # False once an IP is far past the limit


def check_forgot_password_throttle(db: Session, username: str, ip: Optional[str], now: Optional[datetime] = None) -> ForgotPasswordState:
    """5 requests per IP per hour and 3 per username per day, counted from the
    PASSWORD_RESET_REQUESTED rows written for every call (known or unknown
    name), so the limit can't be used to tell real usernames from fake ones."""
    now = now or _utcnow()
    ip = ip if ip_is_throttle_key(ip) else None
    n_ip = 0
    if ip is not None:
        n_ip = _count(db, PASSWORD_RESET_REQUESTED, now - timedelta(seconds=FORGOT_IP_WINDOW_SECONDS), ip=ip)
    n_user = _count(db, PASSWORD_RESET_REQUESTED, now - timedelta(seconds=FORGOT_USER_WINDOW_SECONDS), username=username)
    flooded = _count(db, PASSWORD_RESET_REQUESTED, now - timedelta(seconds=FORGOT_BREAKER_WINDOW_SECONDS)) > FORGOT_BREAKER_LIMIT
    return ForgotPasswordState(
        throttled=flooded or n_ip >= FORGOT_IP_LIMIT or n_user >= FORGOT_USER_LIMIT,
        record_audit=not flooded and n_ip < FORGOT_IP_AUDIT_CAP,
    )


def check_password_change_throttle(db: Session, username: str, now: Optional[datetime] = None) -> Optional[Throttled]:
    """Failed force-password-change attempts (wrong current password): 5 per
    15 minutes per account. Stops a stolen session from being used to guess the
    current password offline-speed."""
    now = now or _utcnow()
    since = now - timedelta(seconds=PASSWORD_CHANGE_WINDOW_SECONDS)
    n = _count(db, PASSWORD_CHANGE_FAILED, since, username=username)
    if n >= PASSWORD_CHANGE_LIMIT:
        wait = _seconds_until_below_limit(
            db, PASSWORD_CHANGE_FAILED, PASSWORD_CHANGE_WINDOW_SECONDS, PASSWORD_CHANGE_LIMIT, n, now, username=username
        )
        return Throttled(min(wait, PASSWORD_CHANGE_WINDOW_SECONDS))
    return None
