"""First-boot seeding: business units, users, and the bootstrap Chairman password.

PM-m12 (PM review 2026-11-18): this used to live in src/main.py, mixing
domain seeding with the app entrypoint. main.py is now assembly only and
re-exports the public names (seed_business_units, seed_users,
BUSINESS_UNIT_NAMES, _delete_bootstrap_password_file) so existing callers
— tests, src/routers/auth.py — keep importing them from src.main.
"""

import logging
import os
import secrets
from pathlib import Path

from sqlalchemy.exc import IntegrityError
from sqlalchemy.engine import make_url

from src.auth import hash_password
from src.config import settings
from src.constants import Role
from src.models import BusinessUnit, User

logger = logging.getLogger("business_ops_tracker.seed")


# File a generated (random) bootstrap Chairman password is written to
# instead of the log stream (D-13). It lives NEXT TO the uploads
# directory — a sibling of LOCAL_UPLOAD_DIR, never inside it — with 0600
# perms, and the server operator fetches it with:
#   docker compose exec app cat /app/.bootstrap_chairman_password
# then deletes the file after the first login. m-17: the uploads directory
# is served (session-gated) in non-production, so a live credential file
# must not sit in it; the file is also deleted automatically after the
# first successful Chairman login (see routers/auth.py login()).
BOOTSTRAP_PASSWORD_FILENAME = ".bootstrap_chairman_password"


def _bootstrap_password_path() -> Path:
    return Path(settings.LOCAL_UPLOAD_DIR).parent / BOOTSTRAP_PASSWORD_FILENAME


def _delete_bootstrap_password_file() -> bool:
    """Deletes the bootstrap password file after the first successful
    Chairman login. Best effort: returns False (never raises) when there
    is nothing to delete or the delete fails, so login can never fail
    over cleanup."""
    try:
        _bootstrap_password_path().unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _write_bootstrap_password_file(password: str) -> Path:
    """Writes the generated bootstrap password to the 0600 file above and
    returns its path. The full value never goes to the log stream."""
    path = _bootstrap_password_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, password.encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(path, 0o600)
    return path


# Real business unit names, keyed by BU number — falls back to "Business
# Unit N" for any number not listed here (e.g. while the remaining ones are
# still being confirmed).
BUSINESS_UNIT_NAMES = {
    1: "ABC Convenience Store",
    2: "ABC Logistics (Logistics)",
    3: "ABC Logistics (Flour)",
    4: "ABC Logistics (Project)",
    5: "ABC Telecom",
    6: "ABC Content Solution",
    7: "ABC Beauty (Principal)",
    8: "ABC Beauty (Distribution)",
    9: "ABC Medical",
    10: "Hope Land Real Estate",
    11: "Myanmar Herbal Medicine",
    12: "MIB Coffee",
    13: "MIB Production",
    14: "MIB FMCG Distribution",
    15: "MIB FMCG Principal (PM)",
    16: "MIB FMCG Principal (HM)",
    17: "MIB FMCG Principal (Shiny)",
}


def seed_business_units(db) -> None:
    existing_codes = {bu.code for bu in db.query(BusinessUnit).all()}
    for i in range(1, settings.TOTAL_BUSINESS_UNITS + 1):
        code = f"BU{i}"
        if code in existing_codes:
            continue
        db.add(BusinessUnit(code=code, name=BUSINESS_UNIT_NAMES.get(i, f"Business Unit {i}")))
    try:
        db.commit()
    except IntegrityError:
        # D-m3: same race as the chairman seed below — two instances in a
        # rolling deploy can both pass the existence check above and race
        # the unique `code` constraint. The loser rolls back and moves on
        # instead of crashing startup; the winner already seeded the BU.
        db.rollback()


def _ensure_user(db, username, password, role, require_password_change, business_unit_id=None, full_name=None) -> None:
    user = db.query(User).filter(User.username == username).first()
    if user:
        return
    db.add(
        User(
            username=username,
            hashed_password=hash_password(password),
            role=role,
            business_unit_id=business_unit_id,
            require_password_change=require_password_change,
            full_name=full_name,
            # CEOs land in the mandatory Profile Setup flow after their first
            # password rotation; Chairman/HO have no profile, so the column
            # default (True) is left as-is for them.
            is_profile_completed=(role != Role.CEO),
        )
    )


def seed_users(db) -> dict:
    # Seeded on first boot only (_ensure_user is a no-op once the account
    # exists) with a random password per account, so every seeded account —
    # including the admin-tier ones — must rotate it on first login.
    #
    # In production this can't use a hardcoded, source-visible password:
    # anyone who's read this file could log in as e.g. ceo_bu14 the moment
    # a fresh prod database comes up, before its real owner ever has. So a
    # real (ENVIRONMENT=production) deploy only bootstraps the one Chairman
    # account — with the password from BOOTSTRAP_CHAIRMAN_PASSWORD when that
    # secret is configured (never logged), otherwise a random one written to
    # the 0600 file /app/.bootstrap_chairman_password (see D-13, m-17: a
    # sibling of the uploads directory, never inside it) — and stops there;
    # every other account is created afterward through Manage Users
    # (Chairman-only), which already hands out its own one-time temporary
    # password per account instead of a fixed, publicly-known string.
    #
    # Returns the {username: password} pairs it generated this run (empty
    # when nothing was seeded), so a dev/test caller can log in with them;
    # the lifespan caller ignores the return value.
    if settings.ENVIRONMENT.lower() == "production":
        if db.query(User).filter(User.username == "chairman").first() is None:
            configured_password = settings.BOOTSTRAP_CHAIRMAN_PASSWORD
            bootstrap_password = configured_password or secrets.token_urlsafe(18)
            _ensure_user(db, "chairman", bootstrap_password, "Chairman", True, full_name="Chairman")
            try:
                db.commit()
            except IntegrityError:
                # Two instances/workers can both pass the "does chairman
                # exist?" check above during a rolling deploy and race to
                # insert it — the username's unique constraint makes the
                # loser's commit fail. That's fine: it means the winner
                # already seeded the account, just not with this process's
                # password, so roll back and move on instead of crashing
                # this instance's startup.
                db.rollback()
            else:
                if configured_password:
                    # Deliberately does not echo the password.
                    logger.info(
                        "First boot: seeded the 'chairman' account from the configured "
                        "BOOTSTRAP_CHAIRMAN_PASSWORD secret; a password change is required at first login."
                    )
                else:
                    # D-13: the generated password is NOT printed to the log
                    # stream — anyone with log access would hold the Chairman
                    # credential. Only the file path is logged (m-17: not
                    # even a prefix hint — a prefix narrows a brute-force
                    # search); the full value is in the 0600 file,
                    # retrievable by the server operator (see
                    # _write_bootstrap_password_file). The file is deleted
                    # automatically after the first successful Chairman login.
                    password_file = _write_bootstrap_password_file(bootstrap_password)
                    logger.warning(
                        "First boot: seeded the 'chairman' account with a random temporary "
                        "password — the full value is NOT shown here; it was written to %s. "
                        "Delete that file after first login.",
                        password_file,
                    )
        return {}

    # Defence in depth for the dev seeding below (audit finding L4):
    # config.py already refuses to boot on an unrecognised ENVIRONMENT,
    # but this branch must be unreachable outside development/test on its
    # own merits — if a future environment name ever slips past the config
    # check, fail loudly here rather than seed dev accounts.
    if settings.ENVIRONMENT not in ("development", "test"):
        raise RuntimeError(
            f"Refusing to seed dev accounts with ENVIRONMENT={settings.ENVIRONMENT!r}: "
            "the dev seed branch is for development/test only."
        )

    # M-02: the publicly-known-password branch is deleted. Dev accounts are
    # seeded ONLY when ALLOW_DEV_SEED=true is set explicitly AND the
    # database is local (sqlite or localhost) — one wrong ENVIRONMENT value
    # on a fresh production database can no longer create 25 accounts with
    # passwords readable in the public source. Each account gets a random
    # password, written once to a 0600 file (same pattern as the bootstrap
    # file) and returned to the caller; the values are never logged.
    if not settings.ALLOW_DEV_SEED:
        logger.warning(
            "Dev seed accounts are disabled (ALLOW_DEV_SEED is not true) — "
            "no dev accounts seeded. Create accounts through Manage Users."
        )
        return {}
    if not _is_local_database_url(settings.DATABASE_URL):
        raise RuntimeError(
            "Refusing to seed dev accounts: ALLOW_DEV_SEED is set but DATABASE_URL "
            "is not local (sqlite or localhost) — dev seeding is for local "
            "databases only."
        )

    generated: dict[str, str] = {}

    def _seed_dev_account(username, role, business_unit_id=None, full_name=None):
        # _ensure_user is a no-op when the account already exists, so only
        # newly created accounts get a generated password recorded here.
        if db.query(User).filter(User.username == username).first() is not None:
            return
        password = secrets.token_urlsafe(18)
        _ensure_user(db, username, password, role, True,
                     business_unit_id=business_unit_id, full_name=full_name)
        generated[username] = password

    _seed_dev_account("chairman", Role.CHAIRMAN, full_name="Chairman")
    _seed_dev_account("ho_staff", Role.HO, full_name="Head Office Staff")

    business_units = {bu.code: bu.id for bu in db.query(BusinessUnit).all()}
    for i in range(1, settings.TOTAL_BUSINESS_UNITS + 1):
        code = f"BU{i}"
        _seed_dev_account(
            f"ceo_bu{i}",
            Role.CEO,
            business_unit_id=business_units.get(code),
            full_name=f"CEO - Business Unit {i}",
        )
    db.commit()
    if generated:
        seed_file = _write_dev_seed_password_file(generated)
        logger.warning(
            "Dev seed accounts created with random passwords (rotate them at first "
            "login) — passwords written once to %s (0600); they are NOT shown here. "
            "NEVER run this mode against a shared database.",
            seed_file,
        )
    return generated


def _is_local_database_url(url: str) -> bool:
    """True for sqlite URLs and for Postgres on localhost — the only
    databases dev seeding may ever touch (M-02)."""
    if url.startswith("sqlite:"):
        return True
    try:
        host = (make_url(url).host or "").lower()
    except Exception:  # noqa: BLE001 - an unparseable URL is not local
        return False
    return host in {"localhost", "127.0.0.1", "::1"}


def _write_dev_seed_password_file(passwords: dict[str, str]) -> Path:
    """Writes the generated dev-seed passwords to a 0600 file next to the
    uploads directory (same pattern as the bootstrap file) and returns its
    path. The values are never logged."""
    path = Path(settings.LOCAL_UPLOAD_DIR).parent / ".dev_seed_passwords"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(
            fd,
            "".join(f"{username}: {password}\n" for username, password in sorted(passwords.items())).encode("utf-8"),
        )
    finally:
        os.close(fd)
    os.chmod(path, 0o600)
    return path
