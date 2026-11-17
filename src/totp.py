"""TOTP second factor (M-01).

A per-user TOTP secret (base32, generated with pyotp) is stored encrypted
at rest (EncryptedText on users.totp_secret — see src/crypto.py) and is
only usable once the owner proves possession by confirming a code.

Two ways to reach enrollment:
- an authenticated session (optional enrollment for HO/CEO, or a
  Chairman re-enrolling after an admin reset), or
- a short-lived setup token issued by login() when a Chairman signs in
  with a correct password but no enrolled TOTP: the token proves the
  password was just verified, and no session is issued until enrollment
  is complete.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
import pyotp

from src.config import settings

TOTP_ISSUER = "Business Ops Tracker"
TOTP_SETUP_TOKEN_MINUTES = 10
_TOTP_SETUP_PURPOSE = "totp_enroll"


def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_provisioning_uri(secret: str, username: str) -> str:
    """otpauth:// URL for the authenticator app (the frontend renders it
    as a QR code and also shows the secret for manual entry)."""
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=TOTP_ISSUER)


def verify_totp_code(secret: Optional[str], code: Optional[str]) -> bool:
    """True when `code` is the current (or immediately adjacent, for clock
    skew) 6-digit code for `secret`. Never raises on bad input — a wrong
    code is a failed check, not a server error."""
    if not secret or not code:
        return False
    code = str(code).strip().replace(" ", "")
    if not code.isdigit():
        return False
    try:
        return bool(pyotp.TOTP(secret).verify(code, valid_window=1))
    except Exception:  # noqa: BLE001 - a bad secret must fail closed
        return False


def issue_totp_setup_token(username: str) -> str:
    """Short-lived token proving the holder just passed the password check
    for `username` — the only credential the enrollment endpoints accept
    besides a live session. Same signing key/algorithm as the session
    token, but a different purpose claim, so it can never be mistaken for
    a session (get_current_user requires a `tv` claim, which this lacks)."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": username,
        "purpose": _TOTP_SETUP_PURPOSE,
        "exp": now + timedelta(minutes=TOTP_SETUP_TOKEN_MINUTES),
        "iat": now,
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_totp_setup_token(token: Optional[str]) -> Optional[str]:
    """Returns the username a valid setup token was issued for, else None."""
    if not token:
        return None
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            options={"require": ["exp", "sub", "purpose"]},
        )
    except jwt.InvalidTokenError:
        return None
    if payload.get("purpose") != _TOTP_SETUP_PURPOSE:
        return None
    subject = payload.get("sub")
    return subject if isinstance(subject, str) else None
