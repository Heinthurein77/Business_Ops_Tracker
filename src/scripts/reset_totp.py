"""
Out-of-band TOTP reset (M-01 recovery path).

If a user loses the device holding their authenticator app, they cannot
sign in — and nobody in the app can clear it for them without a session.
Someone with shell access to the deployment (and therefore to
DATABASE_URL) runs:

    python -m src.scripts.reset_totp <username>

It wipes the account's TOTP enrollment (secret, enabled flag, enrollment
time), signs out every existing session for that account, and writes a
TOTP_RESET_SCRIPT audit row. The account's password is unchanged; a
Chairman must enroll a new authenticator at the next sign-in before any
session is issued.
"""
import sys

from src.auth import bump_token_version
from src.database import SessionLocal
from src.models import User
from src.utils import log_audit


def reset_user_totp(db, username: str) -> str:
    """Clears the TOTP enrollment for `username`. Returns the username.
    Raises RuntimeError when no such account exists."""
    target = db.query(User).filter(User.username == username).with_for_update().first()
    if not target:
        raise RuntimeError(f"No account with username {username!r} exists in this database.")
    was_enabled = bool(target.totp_enabled)
    target.totp_secret = None
    target.totp_enabled = False
    target.totp_enrolled_at = None
    # Revokes every session, so a stale session cannot keep the account
    # usable without its second factor.
    bump_token_version(db, target)
    log_audit(db, target, "TOTP_RESET_SCRIPT", f"via=cli was_enabled={was_enabled}")
    return target.username


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("Usage: python -m src.scripts.reset_totp <username>", file=sys.stderr)
        return 2
    db = SessionLocal()
    try:
        username = reset_user_totp(db, args[0])
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()
    print(f"TOTP enrollment cleared for {username}. They will sign in with their password only,")
    print("and a Chairman must enroll a new authenticator before any session is issued.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
