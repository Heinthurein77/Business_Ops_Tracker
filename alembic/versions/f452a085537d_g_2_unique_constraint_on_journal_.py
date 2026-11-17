"""G-2: unique constraint on journal_entries.reversed_by_id.

Revision ID: f452a085537d
Revises: 64f310f1ca3d
Create Date: 2026-11-17

Adds ``uq_journal_entries_reversed_by_id``: at most one reversal may
point at an entry. The conditional claim in ``reverse_journal`` is the
runtime guard; this is the database backstop, so a second reversal for
the same entry fails the constraint instead of leaving an orphan
reversal with no pointer to it. NULLs are distinct in a unique
constraint on both dialects, so unreversed entries are unaffected.

NOTE: this file was written by hand from the autogenerate output, not
kept verbatim. Autogenerate also reported pre-existing model/migration
drift unrelated to G-2 (ceo_profiles EncryptedText/EncryptedDate
reflection, the uq_ho_*_date / uq_ceo_profile_user unique constraints,
and a journal_lines business_unit_id index + FK) — all stripped here,
none of it touched, so this migration contains only the G-2 change.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f452a085537d'
down_revision: Union[str, Sequence[str], None] = '64f310f1ca3d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # SQLite cannot ADD a constraint to an existing table outside batch
    # mode; journal_entries carries FKs, so batch_alter_table it is.
    # No recreate="never" here (unlike the add-column migrations): a
    # new constraint needs the batch copy-and-move rebuild, which that
    # flag forbids — with it, SQLite raises NotImplementedError.
    with op.batch_alter_table("journal_entries") as batch_op:
        batch_op.create_unique_constraint(
            "uq_journal_entries_reversed_by_id", ["reversed_by_id"])


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("journal_entries") as batch_op:
        batch_op.drop_constraint(
            "uq_journal_entries_reversed_by_id", type_="unique")
