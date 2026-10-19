"""
Re-encrypt personal data under the newest DATA_ENCRYPTION_KEY.

Rotation procedure:
  1. Generate a new key:
       python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  2. Set DATA_ENCRYPTION_KEY="<new key>,<old key>" (newest first) and redeploy.
     The app can now read everything (any listed key decrypts) and writes new
     data under the new key.
  3. Run this script:  python -m src.scripts.rotate_data_key
     It rewrites every encrypted value under the new key. It also encrypts any
     value that is still plaintext (for example rows written before encryption
     was switched on).
  4. Once it reports nothing left to change, remove the old key from
     DATA_ENCRYPTION_KEY and redeploy.

Safe to re-run: a value already encrypted under the newest key is left as it is.
Reads and writes the raw column text (bypassing the ORM's transparent
encryption) so it never decrypts anything it doesn't have to.
"""
import sys

import sqlalchemy as sa

from src.crypto import ENCRYPTED_COLUMNS, encrypt_text, encryption_enabled, is_encrypted, rotate_text
from src.database import engine

BATCH = 500


def rotate_all() -> dict:
    changed = {}
    with engine.begin() as conn:
        for table, column in ENCRYPTED_COLUMNS:
            rows = conn.execute(sa.text(f"SELECT id, {column} FROM {table} WHERE {column} IS NOT NULL")).fetchall()
            count = 0
            for start in range(0, len(rows), BATCH):
                for row_id, value in rows[start:start + BATCH]:
                    text_value = value.isoformat() if hasattr(value, "isoformat") else str(value)
                    # Already under the newest key -> rotate_text hands the value back
                    # unchanged; plaintext (legacy) gets encrypted; anything under an
                    # older key is re-encrypted under the newest.
                    new_value = rotate_text(text_value) if is_encrypted(text_value) else encrypt_text(text_value)
                    if new_value != text_value:
                        conn.execute(
                            sa.text(f"UPDATE {table} SET {column} = :v WHERE id = :i"), {"v": new_value, "i": row_id}
                        )
                        count += 1
            changed[f"{table}.{column}"] = count
    return changed


def main() -> int:
    if not encryption_enabled():
        print("DATA_ENCRYPTION_KEY is not set — nothing to rotate.", file=sys.stderr)
        return 1
    for name, count in rotate_all().items():
        print(f"{name}: {count} value(s) re-encrypted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
