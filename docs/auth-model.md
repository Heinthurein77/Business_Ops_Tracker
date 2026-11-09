# Auth model — Business Ops Tracker

One page stating the role rules the code enforces. If code and this doc
disagree, the code wins for today and the doc (plus
`tests/test_auth_matrix.py`) must be updated in the same change — the prior
review's C1 bug was exactly two routers implementing the same unwritten
rule differently.

Source of truth in code: `src/auth.py` (`require_roles`, `get_current_user`,
`bump_token_version`), `src/models.py` (`User.role`,
`ck_users_bu_assignment`), `src/routers/accounting.py`
(`scoped_bu_id`, `check_bu_access`, `effective_bu_id`,
`require_group_access`), `src/routers/ops.py` (`bu_scope`).

## The four roles

| Role | Who | Scope |
|---|---|---|
| **Chairman** | The owner. Exactly one account. | Group-wide. Only role that can manage users, approve password resets, and use the HO monthly-target override. |
| **HO** (Head Office staff) | Head-office staff. | Group-wide. Sees and approves across all BUs. Cannot manage users, cannot approve password resets, cannot use the HO monthly-target override (that override would let HO bypass the once-a-month lock its own `/api/ho/monthly-target` enforces — see `src/routers/admin.py`). |
| **CEO** | One per business unit (at most one *active* CEO per BU, enforced by a partial unique index). | Pinned to exactly one BU (`business_unit_id` is NOT NULL for CEOs). Every CEO endpoint resolves the BU from the user, never from a request parameter. |
| **Accountant** | Per-BU bookkeeper (created by the Chairman). | Pinned to one BU — except legacy accountants (see below). |

Roles are a single string column (`User.role`); there is no multi-role or
permission-flag system. A request is authenticated by the `__Host-`
session cookie (production) carrying an HS256 JWT (`sub`, `tv`,
`exp`); `tv` is the user's `token_version`, bumped on logout and on
every password change, which revokes **all** of that user's sessions at
once. There is no per-device session list (see `docs/session-policy.md`).

## Per-BU pinning (and the legacy NULL-BU accountant)

- **CEO:** `business_unit_id` always set. `ops.bu_scope` and
  `accounting.check_bu_access` reject any other BU with 403; naming no BU
  means their own BU. CEOs can only read/write their own BU's ops,
  inventory, expenses, payroll, and accounting data.
- **Accountant, created after 2026-10-22:** creating an accountant
  REQUIRES a `business_unit_id`, and it can never be unassigned afterwards.
  The accountant is pinned across the accounting API
  (`accounting.scoped_bu_id` / `effective_bu_id`): naming another BU is a
  403, naming none means their BU. Group-level surfaces — consolidated
  reports, BU comparison, intercompany, finance summary — are 403 for a
  BU-assigned accountant (`require_group_access`); the frontend also hides
  the Group tab for them.
- **Accountant, legacy (created before per-BU assignment existed):**
  `business_unit_id` is NULL. They keep the group-wide accounting access
  they have always had rather than being locked out mid-flight. The DB
  check constraint (`ck_users_bu_assignment`) deliberately allows NULL for
  accountants for this reason. Do NOT "fix" a legacy NULL by assigning a
  BU without the Chairman's say-so — it narrows that accountant's access.
- **Chairman / HO:** `business_unit_id` is always NULL (enforced by the
  same check constraint). They are group-level and never carry a BU.

## Chairman-vs-HO split

HO can do everything the Chairman can **except** the Chairman-only list.
That is a deliberate decision, not an oversight — HO is the Chairman's
oversight staff — with these exceptions:

| Chairman-only | Why HO is excluded |
|---|---|
| `POST /api/admin/head-office/monthly-target` (the override) | Would let HO bypass its own monthly-target once-a-month lock. |
| `GET /api/admin/security-log` | Security/throttle log is owner-only. |
| User management: `GET/POST /api/admin/users`, `PATCH /api/admin/users/{id}`, `POST …/reset-password`, `POST …/active` | Only the Chairman creates/deactivates accounts and issues passwords. |
| Password resets: `GET /api/admin/password-resets`, `POST …/approve`, `POST …/reject` | Only the Chairman approves a reset (and the flow excludes the Chairman's own account — see `src/scripts/reset_chairman.py`). |

Everything else under `/api/admin/*` is Chairman + HO (`require_admin`).
User unlock (`POST /api/admin/users/{id}/unlock-login`) is Chairman + HO.

## Endpoint groups per role

| Group | Routes | Roles |
|---|---|---|
| Public (no session) | `POST /api/auth/login`, `POST /api/auth/forgot-password` | — (throttled: 5/user/15 min, see `src/throttle.py`) |
| Session | `GET /api/auth/me`, `POST /api/auth/logout`, `POST /api/auth/force-password-change` | Any authenticated user (`logout` also works with an expired session) |
| CEO | `/api/ceo/*` (dashboard, monthly-target, profile, financial-notes, submit-report) | CEO only |
| HO | `/api/ho/*` (dashboard, monthly-target, submit-report) | HO only |
| Admin — shared | `/api/admin/*` except the Chairman-only list above (overview, comparison, operations, business units, report logs, CEO profiles, health/activity dashboards, exports) | Chairman, HO |
| Admin — Chairman-only | See table above | Chairman only |
| Users (on the `/api/admin` router) | `/api/admin/users/*` (create/list/deactivate/reset — Chairman; unlock-login — Chairman, HO) and `/api/admin/password-resets/*` (Chairman) | Chairman (unlock: + HO) |
| Approvals (ops + accounting) | PO approve/reject, expense review, payroll approve/pay, staff-advance approve/reject, period close/reopen, journal approve/reject | Chairman, HO |
| Ops — leave review | `POST /api/ops/leave-requests/{id}/review` | Chairman, HO, **CEO** (a CEO reviews their own BU's leave requests) |
| Ops / modules — everything else | Inventory, expenses, staff, suppliers, projects, shipments, BOMs, production, real estate, stock transfers, action items, notifications | Any authenticated user — **BU scoping is enforced inside the handler** (`bu_scope` / `check_bu_access`), not by a role gate. A CEO or BU-assigned accountant naming another BU gets 403; Chairman/HO/legacy accountants pass a BU explicitly. |
| Accounting | `/api/accounting/*` (CoA, journals, accounts, periods, reports, provision) | Chairman, HO, Accountant — with per-BU pinning and `require_group_access` on group-level reports as above |

## Rules for adding a route

1. Pick the narrowest group above; use `require_roles(...)` at the
   endpoint, never a frontend-only check.
2. If the route touches one BU, resolve the BU through `bu_scope` /
   `effective_bu_id` / `check_bu_access` — never trust a raw
   `business_unit_id` from the request body.
3. Add the route to the matrix in `tests/test_auth_matrix.py` if it
   belongs to a role-gated group; the test fails on any route that is
   neither role-gated, session-gated, nor on the public allowlist.
