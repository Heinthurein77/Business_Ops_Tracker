# Delete policy (D-m4)

There is no `ON DELETE` policy anywhere in `src/models.py` — every foreign
key uses the database default (NO ACTION / RESTRICT). Delete behaviour is
therefore defined per endpoint below, not by the schema. The rule of thumb:

- **Anything with history is deactivated, never hard-deleted.** Reports,
  targets, notes, action items and audit rows must stay reconcilable, so
  those entities have no delete endpoint at all. Users and business units
  have no delete endpoint either — a user is deactivated (Chairman sets
  `is_active=False`), a business unit is never removed.
- **Hard delete is only for rows with no history of their own**, and where
  a referenced row would fail at the database with `IntegrityError` → 500,
  the endpoint pre-checks references and returns a clean `400 ("in use")`
  instead.

(The ERP entities this policy used to cover — inventory items, suppliers,
sales customers, employees, journal entries, budgets — were deleted with
the ERP removal, 2026-11-17, along with their delete endpoints.)

## Per-entity policy

| Entity (endpoint) | Behaviour |
|---|---|
| Action item (`DELETE /api/ops/action-items/{id}`) | Hard delete. No other table references action items, so nothing can dangle. |
| License document (`DELETE /api/ops/documents/{id}`) | Hard delete. No other table references documents; the stored file itself is not removed from R2 by this endpoint. |

`audit_logs.user_id` is nullable, so audit rows survive user deactivation
— the audit trail is never cascade-deleted with a user.

## If a new delete endpoint is added

1. Decide deactivate vs hard delete from the table above (history → deactivate).
2. Grep `src/models.py` for `ForeignKey("<table>.id")`; if any row can
   reference the row being deleted and has no ORM cascade, pre-check it
   and return `400 "<entity> is in use …"` — never let the database
   `IntegrityError` surface as a 500.
3. Add the row to the table above.
