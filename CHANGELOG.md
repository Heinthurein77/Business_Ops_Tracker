# Changelog — Business Ops Tracker

All notable changes to this project are recorded here. Dates are in
Asia/Yangon. The tracker is **tracker-only** (ERP functions were removed
2026-11-17 and must not be re-added).

## Unreleased

### Fixed — PM review 2026-11-18, workstream A (deploy & delivery)

- **C-1 — either deploy path can now boot.** `alembic/env.py` no longer
  imports the full app `Settings()`; migrations need only the database
  URL (`src/config.migration_database_url()`), so the compose `migrate`
  service and the DO PRE_DEPLOY job run with no JWT / encryption /
  allowlist secrets. `src/config.settings` is a lazy singleton and
  `src/crypto` tolerates a migrate-only environment (see `alembic/env.py`
  for the batch-migration naming-convention fallback). The canonical
  deploy target (VPS + Neon vs DigitalOcean) is still undecided — see
  README "Deploy paths".
- **PM-9** — go-live runbook no longer tells the deployer to set
  `R2_PUBLIC_BASE_URL` (production refuses to start with it set);
  attachments are served only via the session-gated endpoint.
- **PM-10** — stale migration-head citations removed from the go-live
  runbook and `docs/backup-recovery.md`; the rule is now "exactly one
  head, matching `git log`".
- **PM-11** — versioning started: this changelog; the commit SHA is
  baked into the image (Dockerfile `ARG GIT_SHA`) and exposed on
  `/healthz` (`version` + `commit`).
- **PM-12** — DigitalOcean `deploy_on_push` is off; releases deploy
  explicitly from a tag instead of on every merge to main.
- **PM-13** — `scripts/backup.sh` now verifies the dump is restorable
  (`pg_restore -l`); the off-server-copy check is documented in
  `docs/backup-recovery.md`. The restore drill itself is still a
  user-side go-live gate (not done).
- **PM-14** — `deploy/crontab` sets `MAILTO` (replace with the alert
  address before install); a cheap external uptime check pinging
  `/healthz` is documented in the go-live runbook.
- **PM-15** — `scripts/deploy.sh` runs a pre-flight block (compose
  config validation, required vars, DB reachable) before stopping
  anything, plus an optional pre-deploy `pg_dump` snapshot
  (`PRE_DEPLOY_SNAPSHOT=1`).
- **PM-16** — `.github/CODEOWNERS` added. Branch protection on `main`
  is a user-side GitHub setting (5 minutes), not done here.
- **PM-m6** — README documents the canonical deploy-path choice point
  and the `scripts/` vs `src/scripts/` vs `deploy/` split; `deploy.sh`
  stays in `scripts/` (documented, not moved).
- **PM-m8 / PM-m30** — `.env.example` now lists all eight missing vars
  (incl. `REPORT_EDIT_WINDOW_DAYS`, the edge rate/upload budgets,
  `ALLOW_DEV_SEED`) and the DB CA-cert fetch instruction; the header no
  longer says "Render / DigitalOcean".
- **PM-m12** — seeding + `BUSINESS_UNIT_NAMES` moved from `src/main.py`
  to `src/seed.py`; `main.py` is assembly only (names re-exported).
- **PM-m25** — runbook corrections: pool is (5+5), no "Dev login
  `Chairman123!`" (dev seeds are random), bootstrap path is
  `/app/.bootstrap_chairman_password`, `docker compose exec app`
  (not `web`); same `exec app` fix in the `src/main.py` comment that
  moved to `src/seed.py`.
- **PM-m26** — CI validates the deploy artifacts (`docker compose
  config -q` + `.do/app.yaml` parse); `pytest` moved into
  `requirements-dev.txt`.
- **PM-m27** — `deploy/db_roles.sql` is parameterised with `psql`
  variables and idempotent (existence-guarded roles). Running it
  against a real DB is still user-side.
- **PM-m28** — JWT-rotation rehearsal stays a go-live gate in the docs;
  dual-key support is a later improvement, not built here.
- **PM-m29** — `docs/backup-recovery.md` citation fixed ("§2D of the
  incident runbook" → the go-live runbook).
- **PM-m30** — see PM-m8 above.

### Pending (not done in this pass)

- **`v1.0.0` git tag — belongs on the go-live commit.** Do not tag
  before then; the release that goes live gets the tag.
- **Deploy target decision (user)** — VPS + Neon vs DigitalOcean App
  Platform. Blocks the Neon string, domain, secrets, and drill work.
- **Telegram alerting for cron/job failures (user decision)** — not
  built; `MAILTO` + an external `/healthz` check cover the minimum.
- **Branch protection on `main` (user, GitHub, ~5 min)** — require PR +
  green CI.
- **Restore drill + JWT-rotation rehearsal (user-side go-live gates)** —
  recorded in the runbook once run, not yet run.
- **Running `deploy/db_roles.sql` against the real DB (user-side)** —
  needs the Neon/DO database from the target decision.
