"""Field-level encryption for personal data at rest.

Managed Postgres already encrypts the disk and backups, which protects against
someone stealing the storage — but not against a leaked database URL, a SQL
dump, a read-only replica handed to a contractor, or an injection bug: all of
those read plaintext. Encrypting the most sensitive *personal* columns inside
the application means the database itself only ever holds ciphertext for them.

Design notes
- Fernet (AES-128-CBC + HMAC-SHA256, authenticated) via `cryptography`.
- DATA_ENCRYPTION_KEY is a comma-separated list, newest first. New writes use
  the first key; every key can decrypt, which is how rotation works (prepend
  the new key, deploy, run `python -m src.scripts.rotate_data_key`, drop the
  old key). It is REQUIRED in production (src/config.py refuses to start
  without it). Losing every copy of the key makes these fields unrecoverable
  — keep a copy outside the hosting platform.
- Stored form is "enc:v1:<fernet token>". A value without that prefix is
  legacy plaintext from before encryption was enabled: it is returned as-is
  on read (so a rolling deploy and the backfill migration are safe) and is
  encrypted the next time the row is written.
- With no key configured (local development only) values pass through
  unencrypted. A row that IS encrypted but cannot be decrypted raises instead
  of returning ciphertext to a user or silently overwriting it.
- Only columns that are never filtered, sorted or aggregated in SQL may use
  these types (health values and money are aggregated or range-checked in the
  database, so they are deliberately NOT encrypted — see the audit notes).
"""
from datetime import date, datetime
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from src.config import settings

PREFIX = "enc:v1:"

# (table, column) pairs that use the encrypted types — the backfill migration
# and the key-rotation script iterate this so they can never drift from models.
ENCRYPTED_COLUMNS = [
    ("ceo_profiles", "phone_number"),
    ("ceo_profiles", "email"),
    ("ceo_profiles", "address"),
    ("ceo_profiles", "date_of_birth"),
    ("health_statuses", "other_notes"),
    ("ho_health_statuses", "other_notes"),
]


def _build() -> tuple[Optional[MultiFernet], Optional[Fernet]]:
    keys = [k.strip() for k in settings.DATA_ENCRYPTION_KEY.split(",") if k.strip()]
    if not keys:
        return None, None
    # An invalid key raises here, at import, i.e. at startup — never later on
    # the first request that happens to touch an encrypted column.
    fernets = [Fernet(k.encode()) for k in keys]
    return MultiFernet(fernets), fernets[0]


_FERNET, _PRIMARY = _build()


def encryption_enabled() -> bool:
    return _FERNET is not None


def is_encrypted(value: Optional[str]) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt_text(value: str) -> str:
    """Encrypts unconditionally (a value that merely looks encrypted is not
    trusted). Falls back to plaintext only when no key is configured."""
    if _FERNET is None:
        return value
    return PREFIX + _FERNET.encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_text(value: str) -> str:
    if not is_encrypted(value):
        return value  # legacy plaintext
    if _FERNET is None:
        raise RuntimeError(
            "Encountered encrypted data but DATA_ENCRYPTION_KEY is not configured — refusing to "
            "return or overwrite ciphertext."
        )
    try:
        return _FERNET.decrypt(value[len(PREFIX):].encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError(
            "Could not decrypt a stored value — DATA_ENCRYPTION_KEY does not match the key it was "
            "encrypted with."
        ) from exc


def is_under_primary_key(value: str) -> bool:
    """True when `value` is already encrypted under the newest key."""
    if not is_encrypted(value) or _PRIMARY is None:
        return False
    try:
        _PRIMARY.decrypt(value[len(PREFIX):].encode("ascii"))
        return True
    except InvalidToken:
        return False


def rotate_text(value: str) -> str:
    """Re-encrypts an already-encrypted value under the newest key. (Fernet
    tokens are salted, so this returns different text on every call even for
    the same key — use is_under_primary_key to skip values that don't need it.)"""
    if not is_encrypted(value) or _FERNET is None or is_under_primary_key(value):
        return value
    return PREFIX + _FERNET.rotate(value[len(PREFIX):].encode("ascii")).decode("ascii")


class EncryptedText(TypeDecorator):
    """Text column stored encrypted; transparent to application code."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return encrypt_text(str(value))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return decrypt_text(value)


class EncryptedDate(EncryptedText):
    """A date stored as an encrypted ISO-8601 string (a Date column can't hold
    ciphertext). Reads back as a `datetime.date`, so callers such as the age
    calculation are unchanged."""

    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, datetime):
            value = value.date()
        if isinstance(value, date):
            value = value.isoformat()
        return encrypt_text(str(value))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        plain = decrypt_text(value)
        return date.fromisoformat(plain[:10])
