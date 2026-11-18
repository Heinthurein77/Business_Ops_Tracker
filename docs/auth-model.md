# Auth model — Business Ops Tracker

One page stating the role rules the code enforces. If code and this doc
disagree, the code wins for today and the doc (plus
`tests/test_auth_matrix.py`) must be updated in the same change — the prior
review's C1 bug was exactly two routers implementing the same unwritten
rule differently.

Source of truth in code: `src/auth.py` (`require_roles`, `get_current_user`,
`bump_token_version`), `src/models.py` (`User.role`,
`ck_users_bu_assignment`), `src/routers/ops.py` (`bu_scope`).

(The Accountant role and the whole `/api/accounting/*` group were
deleted with the ERP removal, 2026-11-17. There are three roles now.)

## The three roles

| Role | Who | Scope |
|---|---|---|
| **Chairman** | The owner. Exactly one account. | Group-wide. Only role that can manage users, approve password resets, and use the HO monthly-target override. |
| **HO** (Head Office staff) | Head-office staff. | Group-wide. Sees and approves across all BUs. Cannot manage users, cannot approve password resets, cannot use the HO monthly-target override (that override would let HO bypass the once-a-month lock its own `/api/ho/monthly-target` enforces — see `src/routers/admin.py`). |
| **CEO** | One per business unit (at most one *active* CEO per BU, enforced by a partial unique index). | Pinned to exactly one BU (`business_unit_id` is NOT NULL for CEOs). Every CEO endpoint resolves the BU from the user, never from a request parameter. |

Roles are a single string column (`User.role`); there is no multi-role or
permission-flag system. A request is authenticated by the `__Host-`
session cookie (production) carrying an HS256 JWT (`sub`, `tv`,
`exp`); `tv` is the user's `token_version`, bumped on logout and on
every password change, which revokes **all** of that user's sessions at
once. There is no per-device session list (see `docs/session-policy.md`).

## Per-BU pinning

- **CEO:** `business_unit_id` always set. `ops.bu_scope` rejects any
  other BU with 403; naming no BU means their own BU. CEOs can only
  read/write their own BU's daily-ops data (action items, staff,
  customers, documents, notifications).
- **Chairman / HO:** `business_unit_id` is always NULL (enforced by the
  same check constraint, `ck_users_bu_assignment`). They are group-level
  and never carry a BU.

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
| Users (`src/routers/users.py`) | `/api/admin/users/*` (create/list/deactivate/reset — Chairman; unlock-login — Chairman, HO; totp-disable — Chairman) | Chairman (unlock: + HO) |
| Password resets (`src/routers/password_resets.py`, PM-m13) | `/api/admin/password-resets/*` (list/approve/reject) | Chairman only |
| Ops — everything else | Action items, notifications, staff, customers, documents, forecast | Any authenticated user — **BU scoping is enforced inside the handler** (`bu_scope`), not by a role gate. A CEO naming another BU gets 403; Chairman/HO pass a BU explicitly. |
| TOTP second factor | `POST /api/auth/totp/enroll`, `POST /api/auth/totp/confirm` (live session **or** short-lived setup token), `POST /api/auth/totp/disable` (session + current code), `POST /api/admin/users/{id}/totp-disable` (Chairman) | See below |

## TOTP second factor (M-01)

- The Chairman **must** have TOTP enrolled: `POST /api/auth/login` with a
  correct password but no enrolled TOTP returns 403
  `TOTP_ENROLLMENT_REQUIRED` with a 10-minute setup token and issues **no
  session**. Enrollment (`/totp/enroll` → `/totp/confirm`) accepts that
  token or a live session; only after confirming a code does login demand
  one (`TOTP_REQUIRED` when missing, generic 401 when wrong).
- HO/CEO enrollment is optional, via the same endpoints or the
  `/totp-setup` page. The per-user secret is encrypted at rest
  (`users.totp_secret`, `src/crypto.py`).
- Recovery when the device is lost: the Chairman clears an enrollment from
  User Management (`POST /api/admin/users/{id}/totp-disable`, which also
  revokes every session for the account), or an operator runs
  `python -m src.scripts.reset_totp <username>`. Clearing the Chairman's
  enrollment forces re-enrollment at the next sign-in, so the account is
  never left password-only.

## Rules for adding a route

1. Pick the narrowest group above; use `require_roles(...)` at the
   endpoint, never a frontend-only check.
2. If the route touches one BU, resolve the BU through `bu_scope` —
   never trust a raw `business_unit_id` from the request body.
3. Add the route to the matrix in `tests/test_auth_matrix.py` if it
   belongs to a role-gated group; the test fails on any route that is
   neither role-gated, session-gated, nor on the public allowlist.
