"""
Out-of-band Chairman recovery.

There is exactly one Chairman and nobody above him to approve a password-reset
request, so the in-app recovery flow deliberately excludes him. If his
password is lost, someone with shell access to the deployment (and therefore
to DATABASE_URL) runs:

    python -m src.scripts.reset_chairman

It gives the Chairman account a fresh random temporary password (valid for
TEMP_PASSWORD_TTL_HOURS_RESET hours, and forced to be changed at first
sign-in), signs out every existing session for that account, writes a
CHAIRMAN_RECOVERY_SCRIPT audit row, and prints the password ONCE.

Uses the app's normal settings/DATABASE_URL and expects the schema to be
migrated already (the web app does that on startup).
"""
import secrets
import sys
from datetime import datetime, timedelta

from src.auth import bump_token_version, hash_password
from src.config import settings
from src.database import SessionLocal
from src.models import User
from src.utils import log_audit


def reset_chairman_password(db) -> tuple[str, str]:
    """Returns (username, temporary_password). Raises RuntimeError unless there
    is exactly one Chairman account."""
    chairmen = db.query(User).filter(User.role == "Chairman").with_for_update().all()
    if not chairmen:
        raise RuntimeError("No account with role 'Chairman' exists in this database.")
    if len(chairmen) > 1:
        names = ", ".join(sorted(c.username for c in chairmen))
        raise RuntimeError(f"Expected exactly one Chairman account but found {len(chairmen)} ({names}); refusing to guess.")

    chairman = chairmen[0]
    temporary_password = secrets.token_urlsafe(12)
    chairman.hashed_password = hash_password(temporary_password)
    chairman.require_password_change = True
    chairman.temp_password_expires_at = datetime.utcnow() + timedelta(hours=settings.TEMP_PASSWORD_TTL_HOURS_RESET)
    # A locked-out owner can't fix a deactivated account from the app either.
    chairman.is_active = True
    # Attributes are set first, then the atomic UPDATE token_version + 1 and the
    # commit happen together, so every session issued before this is revoked.
    bump_token_version(db, chairman)
    log_audit(db, chairman, "CHAIRMAN_RECOVERY_SCRIPT", "via=cli")
    return chairman.username, temporary_password


def main() -> int:
    db = SessionLocal()
    try:
        username, temporary_password = reset_chairman_password(db)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()

    print(f"Chairman account:    {username}")
    print(f"Temporary password:  {temporary_password}")
    print(f"Valid for {settings.TEMP_PASSWORD_TTL_HOURS_RESET} hours; you will be asked to set a new password at sign-in.")
    print("This password is shown once and is not stored anywhere in clear text.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
