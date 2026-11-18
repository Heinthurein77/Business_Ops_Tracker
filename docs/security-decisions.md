# Security decisions — Business Ops Tracker

Deliberate choices and accepted risks recorded during the 2026-11-09
security + reliability review (report:
`~/workspace/your_files/BOT_Security_Reliability_Review_2026-11-09.md`).
Each section names the review finding, states the decision, and says
what would make us revisit it. The go-live runbook points here; the
runbook stays the operator's checklist, this file is the reasoning.

Related decision docs: `docs/session-policy.md` (R-5/D-m5 — 8-hour
sessions, logout revokes all devices), `docs/auth-model.md` (R-1),
`docs/backup-recovery.md` (R-4), `docs/single-instance-risk.md` (D-11),
`docs/delete-policy.md` (D-m4 — deactivate vs hard delete per entity).

## R-m1 — Field-encryption scope: 7 columns only

**What is encrypted in the app:** exactly the seven columns in
`src/crypto.py` (`ENCRYPTED_COLUMNS`) — `ceo_profiles.phone_number`,
`ceo_profiles.email`, `ceo_profiles.address`, `ceo_profiles.date_of_birth`,
`health_statuses.other_notes` / `ho_health_statuses.other_notes`, and
`users.totp_secret` (the TOTP secret, M-01).
They are Fernet-encrypted under `DATA_ENCRYPTION_KEY` (`enc:v1:` prefix,
newest-key-first rotation via `src/scripts/rotate_data_key.py`).

**What is not:** everything else — all figures, usernames, and every
other piece of PII — sits in plaintext in the database. It relies on provider disk encryption
(Neon / DigitalOcean managed Postgres encrypt data at rest) and on
`DATABASE_URL` secrecy. Disk encryption protects a stolen disk, not a
stolen credential.

**If `DATABASE_URL` leaks:** whoever holds it can read the full database
in plaintext — every table, every figure — **except** the seven encrypted
columns, which still need `DATA_ENCRYPTION_KEY` to decrypt. (If the whole
environment leaks, the encryption key goes with it — the key lives in the
same env plane as `DATABASE_URL` and `JWT_SECRET_KEY`. See D-6 in the
review and the incident runbook §3D: a leaked encryption key can only be
contained by rotating it and re-encrypting; data already read cannot be
un-read.) Backups (Neon PITR, `pg_dump` dumps) contain the same plaintext
plus the same ciphertext, so they need the same protection as the live
database.

**Decision:** the 7-column scope is deliberate and stays as-is. Widening
in-app encryption to figures would break the SQL reporting the
whole app is built on (sums and filters run in the database,
not in Python). Revisit only if a compliance requirement names a specific
extra field — then it is a design change, not a config tweak.

## R-m2 — Password policy is length-only (deliberate)

**The policy** (`src/schemas.py`, `password_policy_error`): 12–72 bytes
(UTF-8; bcrypt's 72-byte cap), a small startswith denylist of guessable
passwords, at least 5 distinct characters, must not contain the username,
must differ from the current password on change. There is **no**
complexity rule (no required character classes), **no** breached-password
(HaveIBeenPwned-style) check, and **no** expiry / forced rotation except
the first-login forced change.

**Decision:** acceptable as-is, deliberately. The system has ~25 known
users, not a public audience, and the compensating controls are: login
throttling (5 attempts/user/15 min, IP breaker, lockout alerts), bcrypt
hashing, and the first-login forced change. A 12-character all-lowercase
passphrase passes the policy — that is known and accepted for this user
base; the throttle, not complexity, is what stops guessing. Revisit if
the user base grows beyond the current group, if accounts are ever
exposed to the public internet without the proxy, or if a breach check
becomes cheap to add at set-time (it would be an additive check in
`password_policy_error`, not a policy rewrite).

## A-m1 — Logging: unstructured stdout, passwords printed by design, retention bounds

**What logging is:** a single `StreamHandler`, `INFO` level,
`%(asctime)s %(levelname)s %(name)s: %(message)s` (`src/main.py`).
No JSON-structured logging, no request-id. That is a known limitation,
not a bug — the review only says "consider" structured logging, and this
fix does not restructure it.

**Retention / access, by deploy target:**

- **VPS (compose path):** container logs are bounded by the compose
  logging config (D-m9) — `json-file` driver, `max-size: "10m"`,
  `max-file: "3"`, i.e. ~30 MB per container, oldest file dropped first.
  Read them with `docker compose -f docker-compose.prod.yml logs app`
  (the incident runbook §2 saves `--since 72h` as evidence — note that
  72 h of logs may already have rotated away if the volume is high, so
  save evidence promptly). Server access is whoever holds the VPS SSH
  key; there is no separate log reader role.
- **DigitalOcean App Platform:** runtime logs are short-lived on the
  platform (days, and cleared on redeploy — verify the current window in
  the DO dashboard). Do not rely on DO to keep logs: export anything
  needed for an investigation before redeploying, and forward logs
  off-platform if retention beyond a few days is ever required.

**Known, accepted exposure — passwords in logs/stdout:**

- `src/scripts/reset_chairman.py` prints the fresh temporary Chairman
  password to stdout **by design** (single-use, forced change at
  sign-in, time-limited by `TEMP_PASSWORD_TTL_HOURS_RESET`). Anyone with
  log access in the retention window above holds that password until it
  is used/expired — accepted because the script is run by the server
  operator, the password is single-use, and the window is bounded by
  the retention above.
- The first-boot random Chairman password is **not** printed to logs:
  since the D-13 fix it goes to `/app/.bootstrap_chairman_password`
  (0600) and only a first-4-characters hint appears in the log. Delete
  that file after first login (runbook §2B).

SQL parameters are never logged (`hide_parameters=True` on the engine),
so phone numbers/addresses/vitals/password hashes cannot leak into logs
through SQL error messages; the 500 path returns only an `error_id` to
the client while the traceback goes to the server log.

## A-m3 — Data retention and erasure

**Retention policy** (implemented by `src/scripts/audit_retention.py`,
scheduled weekly Sunday 03:00 via `deploy/crontab`):

- Sign-in / sign-out / failed-sign-in audit rows older than **90 days**
  are deleted (high-volume, only useful for recent investigation and for
  throttle windows, which look back hours/days).
- The IP address on every remaining audit row older than **30 days** is
  cleared.
- Report-submission, target, account-management and password rows are
  **kept** — they are the tamper-evidence trail for financial edits and
  account changes.

**Failure mode (documented, not silent):** the script needs the
schema-owner role — it connects with `MIGRATION_DATABASE_URL`. Under the
least-privilege setup the runtime role (`bot_app`) deliberately cannot
`DELETE` from `audit_logs`, so running the script with the wrong URL
fails with "permission denied" instead of silently doing nothing. If
`~/bot-audit-retention.log` shows that error, `MIGRATION_DATABASE_URL`
was not set when the container was started — re-run `up -d` with it set.
Never run `audit_retention` during an incident (it deletes evidence) —
the incident runbook §2 says to pause the cron job first.

**Erasure procedure for a departed CEO / user (decision):**

1. **Deactivate, never hard-delete.** The Chairman deactivates the
   account (Manage Users → deactivate). Deactivation bumps
   `token_version`, which revokes every session immediately, and writes
   a `USER_DEACTIVATED` audit row. There is no delete endpoint and no
   hard delete of the `users` row: reports, documents, notes and
   audit rows all reference the user, and deleting the row would break
   report history. (Same rule as `docs/delete-policy.md`: anything
   with history is deactivated, never hard-deleted.)
2. **Scrub the personal data, keep the financial evidence.** The seven
   encrypted columns (R-m1) are overwritten in place through the app
   layer (so the values stay valid ciphertext): text fields
   (`phone_number`, `email`, `address`, `other_notes`) become
   `"[erased]"`, `date_of_birth` becomes the sentinel `1900-01-01`.
   `profile_pic_url` and `business_details` are cleared. This needs a
   small app-layer script (no such script exists yet — it is a follow-up
   to write on first use, not something to improvise in raw SQL: the
   columns are `NOT NULL`, so they cannot be NULLed, and raw SQL cannot
   produce valid Fernet ciphertext). Until that script exists, a
   deactivated account's PII stays encrypted at rest and unreachable —
   the account cannot log in.
3. **Kept as evidence:** `audit_logs` rows (including `username`) and all
   reports the user submitted/approved. They are business
   records, not personal data held for its own sake, and deleting them
   would destroy the audit trail.

## A-m4 — Capacity: know your first bottleneck

**The first bottleneck is the database connection pool, not CPU.**
Pool is `pool_size=5, max_overflow=5, pool_timeout=10s`
(`src/database.py`) — 10 concurrent connections for the whole app — and
PDF/Excel exports run synchronously in-request, holding a pooled
connection while they generate. Under concurrent exports/dashboard loads
the pool exhausts first: requests wait up to 10 s for a connection, then
fail with 500s, before the 1 vCPU / 1 GB instance saturates. Mitigations
already in place: `statement_timeout=15s`, `lock_timeout=5s`,
`idle_in_transaction_session_timeout=60s`, `pool_pre_ping=True`,
`pool_recycle=1800`.

**How exhaustion is visible:** there is no dedicated pool-timeout
counter — pool-timeout 500s surface as `ERROR` lines in the app log,
and the daily log scan (`src/jobs/log_scan.py`, 09:00 cron) counts
`ERROR` / `lockout` lines and notifies the Chairman.
If the daily scan starts reporting sustained `ERROR` lines at busy
times, suspect pool exhaustion before CPU. No code change for the
current ~25-user load.

**Second failure mode: disk-full.** Unbounded logs used to be one half
of it; that half is now bounded by the D-m9 compose log rotation
(10 MB × 3 files). Uploads go to R2 in production (the local volume is
a dev-only fallback), so a burst of 10 MB uploads fills the bucket/bill,
not the VPS disk — that is a quota question (D-m7, not fixed here), not
a disk question.

**Single worker:** one uvicorn worker serves everything (Dockerfile CMD
has no `--workers`, deliberately — the in-process export budget in
`src/edge.py` must move out of process memory first, D-11). A second
worker is a design change, not a flag flip; see
`docs/single-instance-risk.md` for the accepted single-instance risk and
the recovery for each layer.

## P3-8 — The proxy must not let a forged Host through (CSRF caveat)

The edge middleware's CSRF check (`src/edge.py`, `_origin_ok`) compares
the request `Origin` against `ALLOWED_ORIGINS`, and when the Origin is
not in that list it falls back to comparing it against the `Host`
header — explicitly never using `X-Forwarded-Host`. `Host` is
client-controlled unless something in front overwrites it, so the check
is only sound when the deploy pins the host.

**The control on the compose path is the Host allowlist, not the proxy:**
Caddy forwards the `Host` header it received — it does not rewrite it —
so the enforcement point is `ALLOWED_HOSTS` (set from `DOMAIN` in
`docker-compose.prod.yml`, with `localhost`/`127.0.0.1` always included
for the local health check). A `Host` that is not the public domain is
rejected with 400 before the Origin check runs. Never set
`ALLOWED_HOSTS` to `*`, and never rely on the proxy to rewrite `Host`
unless the Caddyfile is changed to do so explicitly. The same applies on
the DO path (`.do/app.yaml` comments: pin `ALLOWED_HOSTS` /
`ALLOWED_ORIGINS` when serving under a fixed domain).
