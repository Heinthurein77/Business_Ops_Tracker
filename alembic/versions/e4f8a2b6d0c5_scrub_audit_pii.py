"""scrub health values and free text out of existing audit_logs.details

Revision ID: e4f8a2b6d0c5
Revises: d3e7f1a5c9b4
Create Date: 2026-09-24 00:00:00.000000

Until now every report resubmission copied the old and new values of health
vitals (steps, heart rate, blood pressure, sleep) and previews of free-text
fields into audit_logs.details in plaintext — visible to anyone who can read
the audit table or a database dump, and impossible to erase along with the
person. New writes already store "(hidden)" instead (src/utils.py); this
rewrites the rows written before that. The display code redacts on read too,
but the plaintext would otherwise still sit in the database.

Idempotent: a row is only rewritten if it still holds a value other than the
marker. Money fields are left alone on purpose — they are the tamper trail for
financial corrections. Downgrade cannot restore what was scrubbed.
"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e4f8a2b6d0c5'
down_revision: Union[str, Sequence[str], None] = 'd3e7f1a5c9b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MARKER = "(hidden)"
JSON_TAG = "resubmit_changes_json="
# The display labels stored in the "field" key (see FIELD_LABELS in utils.py).
HIDDEN_LABELS = {
    "Steps", "Heart Rate", "Sleep Hours", "BP Systolic", "BP Diastolic",
    "Financial Notes", "Meetings Attended", "Site Visits", "Key Decisions",
    "Next Day Schedule", "Activity Notes", "Health Notes", "Social Notes",
}


def _scrub(details: str):
    """Returns the rewritten details string, or None when nothing changes."""
    head, sep, raw = details.partition(JSON_TAG)
    if not sep:
        return None
    try:
        changes = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(changes, list):
        return None
    dirty = False
    for change in changes:
        if isinstance(change, dict) and change.get("field") in HIDDEN_LABELS:
            if change.get("old") != MARKER or change.get("new") != MARKER:
                change["old"] = MARKER
                change["new"] = MARKER
                dirty = True
    if not dirty:
        return None
    return head + JSON_TAG + json.dumps(changes, ensure_ascii=False)


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, details FROM audit_logs WHERE details LIKE :pattern"),
        {"pattern": f"%{JSON_TAG}%"},
    ).fetchall()
    scrubbed = 0
    for row_id, details in rows:
        new_details = _scrub(details or "")
        if new_details is not None:
            bind.execute(
                sa.text("UPDATE audit_logs SET details = :d WHERE id = :i"), {"d": new_details, "i": row_id}
            )
            scrubbed += 1
    print(f"[audit scrub migration] redacted {scrubbed} of {len(rows)} audit row(s)")


def downgrade() -> None:
    """Downgrade schema — the scrubbed values are gone for good, by design."""
