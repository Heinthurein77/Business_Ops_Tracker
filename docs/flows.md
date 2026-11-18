# Flows & state machines — Business Ops Tracker

How a request moves through the system, and what state each entity can
be in. This is the one place the flows and lifecycles are written down
(PM-m7); the code is the source of truth and the `file:line` references
below were verified against the code at the commit this doc was written
with — line numbers drift as code changes, so if a reference looks
wrong, grep for the symbol name rather than trusting the number.

Related docs: `docs/auth-model.md` (who may call what),
`docs/session-policy.md` (sessions), `docs/backup-recovery.md`
(backups), `docs/delete-policy.md` (what may be deleted).

## Flows

### CEO daily report

The CEO fills the daily form on the dashboard
(`frontend/src/pages/CeoDashboardPage.jsx`), which POSTs to
`/api/ceo/submit-report` (`src/routers/ceo.py:956`). The form is parsed
by `build_ceo_report_form` (`src/report_forms.py:223`, endpoint wrapper
at `:444`); an attachment, if any, is uploaded first by
`upload_report_attachment` (`src/services/daily_report.py:41`) so no
database session is held during the upload. The four tables (financial,
activity, health, social) are then written by one row-locked upsert,
`upsert_daily_report` (`src/services/daily_report.py:99`), which
returns 409 if two submits for the same BU/day race. After the commit,
a revenue more than 50% off the 14-day average sets
`revenue_anomaly` and notifies every HO user (`src/routers/ceo.py:1117`
and `:1123`), and a backdated resubmit notifies Chairman + HO
(`:1139-1150`); the submission itself is audit-logged as
`DAILY_REPORT_SUBMITTED`. A resubmit inside the 7-day window overwrites
the same day's row — see "Daily report" under Lifecycles.

### HO daily report

The same shape as the CEO report, against separate HO tables: the Head
Office page POSTs to `/api/ho/submit-report` (`src/routers/ho.py:337`),
parsed by `build_ho_report_form` (`src/report_forms.py:365`) and applied
by `_apply_ho_report` (`src/routers/ho.py:282`) into
`HeadOfficeDailyFinancial` (`src/models.py:324`),
`HeadOfficeActivityLog` (`:297`), `HeadOfficeHealthStatus` (`:360`)
and `HeadOfficeSocialLog` (`:384`). Unlike the CEO submit, the HO
submit sends no post-submit notifications (PM-m1) — an HO restatement
of reviewed history is silent.

### Admin overview → comparison → operations

The Chairman/HO pages (`AdminDashboardPage`, `ComparisonPage`,
`OperationsPage`) all read through one function,
`gather_admin_overview(db, parts=…)` (`src/routers/admin.py:138`),
which computes only the requested subset: the comparison page asks for
`parts={"trend", "comparison"}` (`:595`) and the operations page for
`parts={"operations"}` (`:610`). The three pages are different views
over the same daily-report rows, not three queries — so a number on the
overview and the same number on the operations grid cannot disagree
unless the underlying row changed between the two requests.

### Monthly targets

A CEO sets the month's targets once via `POST /api/ceo/monthly-target`
(`src/routers/ceo.py:568`) and HO via `POST /api/ho/monthly-target`
(`src/routers/ho.py:226`); a second set is rejected with 403, so the
target is locked for the month. The Chairman can always override: the
HO monthly-target override (`POST
/api/admin/head-office/monthly-target`, `src/routers/admin.py:857`)
and the per-BU target override (`:905`). Targets live in
`MonthlyTarget` (`src/models.py:424`) and `HeadOfficeMonthlyTarget`
(`:401`). A target for last month stays settable for
`REPORT_EDIT_WINDOW_DAYS` (default 7, `src/config.py:320`) after that
month ends (`src/utils.py` `monthly_target_window_error`).

### Notifications

Every in-app notification is built by the one helper `notify()`
(`src/utils.py:175`); the writers are the three job functions in
`src/services/ops_alerts.py` (`run_remind_missing_reports` `:30`,
`run_remind_action_items` `:66`, `run_check_document_expiry` `:103`),
the CEO submit (`src/routers/ceo.py:1123` anomaly,
`:1139` backdated resubmit), help resolution
(`src/routers/admin.py:1440`), the login-lockout alert
(`src/throttle.py:391`), the log scan (`src/jobs/log_scan.py:195`)
and the scheduled-alerts job itself
(`src/jobs/scheduled_alerts.py:42`). The bell
(`frontend/src/components/NotificationBell.jsx`) polls every 60
seconds (`:60-68`) and follows the notification's `link` in-app when
tapped (`:96`). The `link` is stored per notification — whether it is
a route the recipient may actually open is the writer's business (see
PM-3: document-expiry notifications still link CEOs to an admin-only
page).

### Jobs

Four cron lines in `deploy/crontab`, all under `flock` so a slow run
is skipped, not duplicated: 08:30 `scheduled_alerts` (`:35`) runs the
three `run_*` functions in `src/jobs/scheduled_alerts.py:56-58`
(missing-report reminders, document-expiry alerts, action-item
reminders — each deduped/throttled so a re-run is safe); 09:00
`log_scan` (`:58`) counts ERROR/lockout lines in the last 24 h of app
logs and runs the dead-man check on the `JobHeartbeat` table; Sunday
03:00 audit retention (`:48`, `src/scripts/audit_retention.py`) and
Sunday 03:30 the weekly `pg_dump` (`:65`, `scripts/backup.sh`). Each
job records an `ok`/`failed` heartbeat via `record_job_heartbeat`
(`src/utils.py:240`), which is what `log_scan` watches.

### Health dashboard

`GET /api/admin/health-dashboard` (`src/routers/admin.py:1718`) reads
`HealthStatus` (`src/models.py:239`) — the daily health rows CEOs
submit with their report — and renders them on
`HealthDashboardPage.jsx`. It is CEO-only: HO staff submit health
daily with their own report (`src/routers/ho.py:337`) but those rows
(`HeadOfficeHealthStatus`, `src/models.py:360`) never appear on this
dashboard (PM-m2); the dashboard's name notwithstanding, it covers
CEO health only.

### Help requests

A CEO writes `help_needed` on the daily activity form; the activity
dashboard flags it under "Needs Attention" and a Chairman/HO user
answers it with `POST /api/admin/activity-logs/{log_id}/resolve-help`
(`src/routers/admin.py:1404-1405`), which records the response on the
activity log and notifies the CEO with a deep link to
`/ceo?date=…` (`:1440`). If the CEO edits the day afterwards, the
request re-opens: the edit clears `help_resolved_at`
(`src/routers/ceo.py:902-907`). This is the best-enforced lifecycle in
the repo — see "Help request" under Lifecycles.

### Documents

Licence/permit documents are created, listed and deleted through
`src/routers/ops.py` (`list_documents` `:595`, `create_document`
`:621`, `delete_document` `:651`; rows are `LicenseDocument`,
`src/models.py:640`). Deletion is a hard delete and does not remove
the stored file from R2 (see `docs/delete-policy.md`). The expiry job
`run_check_document_expiry` (`src/services/ops_alerts.py:103`)
notifies each BU's CEOs and Chairman/HO about documents expiring
within 30 days, throttled to one alert per document per 7 days via
`expiry_notified_at` (`src/models.py:651`). Documents are surfaced
only on the Daily Ops page — CEOs have no documents UI (PM-3).

### Forecast

`GET /api/ops/forecast` (`src/routers/ops.py:545`) projects
month-end revenue from the month so far and is shown on the Daily Ops
forecast tab. With no business unit selected it sums the whole group;
the tab does not label whether the figure is one BU or all 23 (PM-m16).

## Lifecycles

### Daily report — no server-side state (PM-m8)

There is **no status column** on any daily-report table
(`DailyFinancial`, `src/models.py:155`; `HeadOfficeDailyFinancial`,
`:324`; likewise the activity/health tables). "Submitted" means a row
exists for that BU and date; "draft" exists only in the browser —
`frontend/src/utils/drafts.js` saves the in-progress form to
`localStorage`, so a draft is invisible on a second device or browser
and is lost if that browser's storage is cleared. A resubmit inside
the 7-day window (`report_date_window_error`, `src/utils.py:57-70`,
window from `REPORT_EDIT_WINDOW_DAYS`, `src/config.py:320`)
overwrites the row; the 409 race guard lives in
`upsert_daily_report` (`src/services/daily_report.py:99`, guard at
`:164`). There is
no `submitted_at` distinct from `updated_at`, no review/accept state,
and no lock after Chairman review — only the window plus the
backdated-resubmit notification (`src/routers/ceo.py:1139-1150`).
Whether a server-side submitted/draft state is wanted is an open
product decision; until it is made, nothing here should be read as
"the report is in state X" — it is only "a row exists / it does not".

### Monthly target — unset → set (locked) → admin override

`MonthlyTarget` (`src/models.py:424`) and `HeadOfficeMonthlyTarget`
(`:401`) have no status column either; the lifecycle is enforced by
the endpoints. Unset: no row for the BU/month. Set: the CEO/HO
`POST …/monthly-target` (`src/routers/ceo.py:568`,
`src/routers/ho.py:226`) creates the row, and a second set is rejected
with 403 — the target is locked for that month. Override: the
Chairman-only endpoints (`src/routers/admin.py:857` for HO,
`:905` per BU) always upsert, so the Chairman can correct a locked
target at any time. A target for last month stays settable for
`REPORT_EDIT_WINDOW_DAYS` after the month ends.

### Action item — pending / in_progress / done

Statuses are `ACTION_ITEM_STATUSES = ("pending", "in_progress",
"done")` (`src/constants.py:41-42`); rows are `ActionItem`
(`src/models.py:556`). The value is validated on update
(`src/routers/ops.py:277-300`) but **any transition is allowed** —
there is no transition table. Setting `done` stamps `completed_at`
(`:287`); moving back to `pending` or `in_progress` clears it, with
no record of the prior completion and no `completed_by` field (PM-m3:
reopening a done item should keep an audit note, and reassignment /
deletion currently notify nobody). Creation notifies the assignee
(`src/routers/ops.py:264-266`); the reminder job
(`src/services/ops_alerts.py:66-72`) only reminds items that have an
assignee and a due date today or overdue, at most once per item per 7
days.

### Document expiry — no status field, computed

`LicenseDocument` (`src/models.py:640`) has no status column:
"expiring" is computed as `days_left` from `expiry_date`
(`src/services/ops_alerts.py:119`), and the only stored lifecycle
marker is `expiry_notified_at` (`src/models.py:651`), the throttle
that limits expiry alerts to one per document per 7 days
(`src/services/ops_alerts.py:115` and `:130`). Deletion is a hard
delete with no archive state. A document therefore has exactly two
states — present, or deleted — and "expiring soon" is a property the
job computes on each run, not something stored on the row.

### Help request — open → resolved → re-opened on edit

Open means `help_needed` is set and `help_resolved_at` is NULL on the
activity log (`ActivityLog`, `src/models.py:195`, fields at `:213` and
`:219`; the CEO flag is read at `src/routers/ceo.py:374-376`). Resolved means a
Chairman/HO user answered via `resolve_help_request`
(`src/routers/admin.py:1405`), which requires response text and
refuses an already-resolved request, then notifies the CEO
(`:1440`). Re-opened: editing the day after resolution clears
`help_resolved_at` (`src/routers/ceo.py:902-907`), so the CEO's new
text becomes a fresh open request. Every transition is guarded in
code; this lifecycle is fully enforced, not just documented.

### Password reset — Pending → Approved / Rejected

Rows are `PasswordResetRequest` (`src/models.py:510`): the status
column is constrained by `ck_pwreset_status` to `Pending`,
`Approved`, `Rejected` (`:522`), and a partial unique index allows
at most one Pending request per user. The flow starts anonymously at
`POST /api/auth/forgot-password` (`src/routers/auth.py:266`):
`_open_reset_request` (`:251-262`) adds a Pending row unless a live
one already exists — a Pending request older than the approval window
(`RESET_REQUEST_TTL_HOURS = 72`, `src/auth.py:23`) is auto-rejected
first so it stops blocking a re-file. The Chairman works the queue
in `src/routers/password_resets.py`: list (`:36`, paginated),
approve (`:100`) and reject (`:149`). Approval is row-locked
(`_load_pending_request`, `:90`): a second resolve attempt gets 409,
a stale (>72 h) request cannot be approved, and approving issues a
random temporary password, forces a change at first sign-in, and
revokes the account's sessions. The Chairman's own account is
excluded from the whole flow — its recovery is the out-of-band
`python -m src/scripts/reset_chairman` (`src/routers/auth.py:244`).
The request itself never notifies the Chairman (PM-7): it waits in
the queue until the Chairman opens Manage Users.
