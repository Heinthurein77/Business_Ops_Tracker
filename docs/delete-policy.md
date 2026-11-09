# Delete policy (D-m4)

There is no `ON DELETE` policy anywhere in `src/models.py` — every foreign
key uses the database default (NO ACTION / RESTRICT). Delete behaviour is
therefore defined per endpoint below, not by the schema. The rule of thumb:

- **Anything with history is deactivated, never hard-deleted.** History
  (POs, invoices, payments, payroll runs, stock movements, journals) must
  stay reconcilable, so those entities have no delete endpoint at all.
- **Hard delete is only for rows with no history of their own**, and where
  a referenced row would fail at the database with `IntegrityError` → 500,
  the endpoint pre-checks references and returns a clean `400 ("in use")`
  instead.

## Per-entity policy

| Entity (endpoint) | Behaviour |
|---|---|
| Inventory item (`DELETE /api/ops/inventory/{id}`) | Deactivate (`is_active=False`). Stock movements and serials keep pointing at the item, so it is never removed. |
| Supplier (`DELETE /api/ops/suppliers/{id}`) | Deactivate. POs keep their `supplier_id`; group-shared suppliers (BU `NULL`) are Chairman/HO-only. |
| Sales customer (`DELETE /api/ops/sales-customers/{id}`) | Deactivate. Invoices keep their `customer_id`. |
| Employee (`DELETE /api/ops/employees/{id}`) | Deactivate. Payroll lines, leave requests and advances keep their `employee_id`. |
| Action item (`DELETE /api/ops/action-items/{id}`) | Hard delete. No other table references action items, so nothing can dangle. |
| License document (`DELETE /api/ops/documents/{id}`) | Hard delete. No other table references documents; the stored file itself is not removed from R2 by this endpoint. |
| Journal entry (`DELETE /api/accounting/journal/{id}`) | Hard delete, **draft entries only** — posted entries return `400` ("reverse instead"). Lines delete with the entry (ORM `cascade="all, delete-orphan"`). Pre-checked references return `400 "in use"`: another entry's `reversal_of_id` / `reversed_by_id`, or a staff advance's `journal_entry_id`. |
| Budget (`DELETE /api/accounting/budgets/{id}`) | Hard delete. No other table references budgets. |

`audit_logs.user_id` is nullable, so audit rows survive user deactivation
or deletion — the audit trail is never cascade-deleted with a user.

## If a new delete endpoint is added

1. Decide deactivate vs hard delete from the table above (history → deactivate).
2. Grep `src/models.py` for `ForeignKey("<table>.id")`; if any row can
   reference the row being deleted and has no ORM cascade, pre-check it
   and return `400 "<entity> is in use …"` — never let the database
   `IntegrityError` surface as a 500.
3. Add the row to the table above.
