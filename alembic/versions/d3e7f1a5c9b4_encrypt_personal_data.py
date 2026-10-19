"""encrypt personal profile data and health notes at rest

Revision ID: d3e7f1a5c9b4
Revises: c2d6e0f4b8a3
Create Date: 2026-09-24 00:00:00.000000

Widens the columns that now use the EncryptedText / EncryptedDate types (see
src/crypto.py) and encrypts what is already stored. Re-runnable: rows that
already carry the "enc:v1:" prefix are skipped, so running it twice (or after a
partial failure) never double-encrypts.

Needs DATA_ENCRYPTION_KEY. Production refuses to start without one; in local
development with no key the backfill is skipped and the columns simply hold
plaintext until a key is configured and this is re-run via
`python -m src.scripts.rotate_data_key`.

Type changes are Postgres-only; SQLite (development) is dynamically typed and
already stores these values as text.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from src.crypto import ENCRYPTED_COLUMNS, decrypt_text, encrypt_text, encryption_enabled, is_encrypted


revision: str = 'd3e7f1a5c9b4'
down_revision: Union[str, Sequence[str], None] = 'c2d6e0f4b8a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BATCH = 500


def _rewrite_column(table: str, column: str, transform) -> int:
    """Applies `transform(old) -> new` to every non-NULL value that changes."""
    bind = op.get_bind()
    changed = 0
    rows = bind.execute(sa.text(f"SELECT id, {column} FROM {table} WHERE {column} IS NOT NULL")).fetchall()
    for start in range(0, len(rows), BATCH):
        for row_id, value in rows[start:start + BATCH]:
            text_value = value.isoformat() if hasattr(value, "isoformat") else str(value)
            new_value = transform(text_value)
            if new_value != text_value:
                bind.execute(sa.text(f"UPDATE {table} SET {column} = :v WHERE id = :i"), {"v": new_value, "i": row_id})
                changed += 1
    return changed


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.alter_column('ceo_profiles', 'phone_number', type_=sa.Text(), existing_nullable=False)
        op.alter_column('ceo_profiles', 'email', type_=sa.Text(), existing_nullable=True)
        op.alter_column(
            'ceo_profiles', 'date_of_birth', type_=sa.Text(), existing_nullable=False,
            postgresql_using='date_of_birth::text',
        )

    if not encryption_enabled():
        print("[encrypt migration] DATA_ENCRYPTION_KEY is not set — leaving existing values as plaintext.")
        return
    total = 0
    for table, column in ENCRYPTED_COLUMNS:
        total += _rewrite_column(table, column, lambda v: v if is_encrypted(v) else encrypt_text(v))
    print(f"[encrypt migration] encrypted {total} value(s)")


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    if not encryption_enabled():
        # Without the key the ciphertext cannot be turned back into data, and
        # narrowing the columns would destroy it — refuse rather than lose data.
        rows = bind.execute(
            sa.text("SELECT count(*) FROM ceo_profiles WHERE phone_number LIKE 'enc:v1:%'")
        ).scalar()
        if rows:
            raise RuntimeError("Encrypted data present but DATA_ENCRYPTION_KEY is not set — cannot downgrade.")
    else:
        for table, column in ENCRYPTED_COLUMNS:
            _rewrite_column(table, column, decrypt_text)
    if bind.dialect.name == "postgresql":
        op.alter_column(
            'ceo_profiles', 'date_of_birth', type_=sa.Date(), existing_nullable=False,
            postgresql_using='date_of_birth::date',
        )
        op.alter_column('ceo_profiles', 'email', type_=sa.String(length=255), existing_nullable=True)
        op.alter_column('ceo_profiles', 'phone_number', type_=sa.String(length=30), existing_nullable=False)
