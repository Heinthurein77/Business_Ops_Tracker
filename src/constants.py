"""Single source of truth for the vocabularies the codebase otherwise
repeats as free-form string literals: user roles, action-item statuses,
and in-app notification types.

A typo in one of ~90 role literals used to silently change behaviour (a
mistyped role in a filter returns an empty list instead of erroring, and
nothing fails at import). These are `str` enums / plain tuples, so the
stored values — and every API response — are byte-identical to the old
literals; only the spelling is now checked at import time.
"""
from enum import Enum


class Role(str, Enum):
    """User roles. Values are the exact strings stored in users.role
    (and enforced on Postgres by ck_users_role)."""

    CHAIRMAN = "Chairman"
    CEO = "CEO"
    HO = "HO"


# Roles that administer the whole group (Chairman + HO staff).
ADMIN_ROLES = (Role.CHAIRMAN, Role.HO)


class NotificationType(str, Enum):
    """In-app Notification.type values (see Notification.type in
    src/models.py). Every notify() call site uses one of these."""

    MISSING_REPORT = "missing_report"
    HELP_RESOLVED = "help_resolved"
    ACTION_ASSIGNED = "action_assigned"
    ACTION_DUE = "action_due"
    DOCUMENT_EXPIRY = "document_expiry"
    ALERT = "alert"


# ActionItem.status values (validated in src/routers/ops.py, filtered in
# src/services/ops_alerts.py). "done" is terminal; the other two are open.
ACTION_ITEM_STATUSES = ("pending", "in_progress", "done")
ACTION_ITEM_OPEN_STATUSES = ("pending", "in_progress")
