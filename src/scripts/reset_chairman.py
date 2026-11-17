"""
Out-of-band Chairman recovery.

There is exactly one Chairman and nobody above him to approve a password-reset
request, so the in-app recovery flow deliberately excludes him. If his
password is lost, someone with shell access to the deployment (and therefore
to DATABASE_URL) runs:

    python -m src.scripts.reset_chairman

It gives the Chairman account a fresh random temporary password (valid for
TEMP_PASSWORD_TTL_HOURS_RESET hours, and forced to be changed at first
sign-in), signs out every existing session for that account, and writes a
CHAIRMAN_RECOVERY_SCRIPT audit row.

m-29: the temporary password is NEVER printed to stdout (stdout from
`docker compose exec` can land in captured logs) — it is written to a 0600
file next to the uploads directory, the same pattern as the bootstrap
password file (src/main.py), and only the file path is printed. A
deactivated Chairman account is NOT silently reactivated: pass
--reactivate explicitly if that is what you mean.
"""
import argparse
import os
import secrets
import sys
from datetime import datetime, timedelta
from pathlib import Path

from src.auth import bump_token_version, hash_password
from src.config import settings
from src.database import SessionLocal
from src.models import User
from src.utils import log_audit, utcnow

RECOVERY_PASSWORD_FILENAME = ".chairman_recovery_password"


def _recovery_password_path() -> Path:
    # A sibling of the uploads directory, never inside it: the uploads
    # directory is served (session-gated) in non-production, and a live
    # credential file must not sit in a served directory.
    return Path(settings.LOCAL_UPLOAD_DIR).parent / RECOVERY_PASSWORD_FILENAME


def _write_recovery_password_file(password: str, path: Path | None = None) -> Path:
    """Writes the temporary password to a 0600 file and returns its path.
    The full value never goes to the log stream or stdout."""
    target = path or _recovery_password_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, password.encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(target, 0o600)
    return target


def reset_chairman_password(db, reactivate: bool = False) -> tuple[str, str]:
    """Returns (username, temporary_password). Raises RuntimeError unless there
    is exactly one Chairman account.

    The account is NOT reactivated unless `reactivate` is True: a
    deactivated Chairman stays deactivated until someone says so
    explicitly, instead of the recovery script silently undoing the
    deactivation."""
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
    chairman.temp_password_expires_at = utcnow() + timedelta(hours=settings.TEMP_PASSWORD_TTL_HOURS_RESET)
    if reactivate:
        # A locked-out owner can't fix a deactivated account from the app
        # either — but only when the operator asked for it explicitly.
        chairman.is_active = True
    # Attributes are set first, then the atomic UPDATE token_version + 1 and the
    # commit happen together, so every session issued before this is revoked.
    bump_token_version(db, chairman)
    log_audit(db, chairman, "CHAIRMAN_RECOVERY_SCRIPT", "via=cli")
    return chairman.username, temporary_password


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Reset the Chairman's password (out-of-band recovery).")
    parser.add_argument(
        "--reactivate",
        action="store_true",
        help="Also reactivate the Chairman account if it was deactivated (off by default).",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Where to write the 0600 temporary-password file "
        "(default: a sibling of the uploads directory).",
    )
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        username, temporary_password = reset_chairman_password(db, reactivate=args.reactivate)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()

    password_file = _write_recovery_password_file(
        temporary_password, Path(args.output) if args.output else None
    )
    print(f"Chairman account:    {username}")
    print(
        f"Temporary password written to {password_file} (0600) — "
        "read it from there and delete the file after first login. "
        "It is not shown here."
    )
    print(f"Valid for {settings.TEMP_PASSWORD_TTL_HOURS_RESET} hours; you will be asked to set a new password at sign-in.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
