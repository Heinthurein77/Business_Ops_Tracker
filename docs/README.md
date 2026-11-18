# Docs — Business Ops Tracker

Index of everything in `docs/`. The root `README.md` is the onboarding
entry point; the files here are the decision and operations records.
Line numbers cited in these docs were verified against the code when
written — if one looks wrong, grep for the symbol name.

## Index

- [`flows.md`](flows.md) — how each flow moves through the system (CEO
  and HO daily reports, admin overview/comparison/operations, monthly
  targets, notifications, jobs, health dashboard, help requests,
  documents, forecast) and the six lifecycles (daily report, monthly
  target, action item, document expiry, help request, password reset)
  with their states and where each is enforced in code.
- [`auth-model.md`](auth-model.md) — the authoritative role rules
  (Chairman / HO / CEO), per-BU pinning, the Chairman-only list, and
  TOTP; cross-checked by `tests/test_auth_matrix.py`.
- [`session-policy.md`](session-policy.md) — 8-hour absolute sessions,
  no idle timeout, logout revokes all devices; the accepted risk of a
  stolen cookie.
- [`security-decisions.md`](security-decisions.md) — deliberate
  security choices and accepted risks (field-encryption scope,
  length-only password policy, logging/retention, capacity, the
  Host/CSRF caveat).
- [`delete-policy.md`](delete-policy.md) — deactivate vs hard delete
  per entity; the FK `ON DELETE` policy the migration enforces.
- [`backup-recovery.md`](backup-recovery.md) — what is backed up, RPO
  1 h / RTO 4 h, the restore drill, and the user-side checklist
  (Neon PITR, R2 versioning, off-server dumps).
- [`single-instance-risk.md`](single-instance-risk.md) — the accepted
  single-point-of-failure risk per layer and the recovery for each.
- [`BOT_GoLive_Runbook_2026-10-21.md`](BOT_GoLive_Runbook_2026-10-21.md)
  — the go-live runbook: the operator's checklist for the first deploy.
- [`BOT_Incident_Runbook_2026-11-09.md`](BOT_Incident_Runbook_2026-11-09.md)
  — the incident runbook: detection, evidence preservation,
  containment per secret type, recovery, comms, rehearsal.

## Notes

- **Report drafts are browser-only (PM-m8).** "Draft" vs "submitted"
  has no server-side state: a draft lives in `localStorage` on the
  device that wrote it, and "submitted" just means a row exists for
  that BU and date. See `flows.md` ("Daily report" lifecycle).
- **Password resets live in their own router (PM-m13).** The
  approval queue is `src/routers/password_resets.py`, not
  `src/routers/users.py` — same `/api/admin/password-resets/*` paths,
  same Chairman-only gates.

## Migrations

Migrations run automatically at app startup
(`RUN_MIGRATIONS_ON_STARTUP`, default true); see "Migrations" in the
root `README.md` for the manual commands. There is exactly one head
(`621509460777` at the time of writing — verify with
`alembic heads`, never trust a hash in a doc); `tests/test_migrations_chain.py`
guards models ↔ migrations drift.

**The ERP-era migrations are historical — do not squash, do not
re-add (PM-m11).** Eight files in `alembic/versions/` carry the ERP
history the tracker was built on top of, and the chain must be
replayed as-is by a fresh clone:

| Revision | File |
|---|---|
| `bc3f5347dc23` | `erp_phase1_accounting_core.py` |
| `f973b41a7022` | `erp_phase3_procurement.py` |
| `0a1b2e81c46a` | `erp_phase4_sales_invoices.py` |
| `1762bd0f7e81` | `erp_phase5_hr_payroll.py` |
| `4c3816e1b81e` | `erp_phase6_fixed_assets_banking.py` |
| `59cd42cd0c1a` | `erp_completion_pos_quotations_returns_.py` |
| `4c107c5acdcb` | `erp_bcd_budgets_currency_intercompany_.py` |
| `a720b6b7c616` | `phase4_erp_removal_drop_tables.py` |

(There is no `erp_phase2` migration file.) The first seven build the
ERP tables; `a720b6b7c616` — the 2026-11-17 ERP removal — drops all
48 of them (journals, inventory, HR, budgets, customers, and the rest)
plus the Accountant role. A fresh database therefore replays the
whole ERP history to arrive at a tracker-only schema. Squashing the
chain would be risky (the removal migration is one-way by design —
see `ONE_WAY_REVISION` in `tests/test_bot_review_migrations_batch.py`),
so the history stays. **ERP tables must not be re-added**: the
tracker-only scope was decided 2026-11-17, and anything that needs
one of those tables is a product decision, not a migration to write.
