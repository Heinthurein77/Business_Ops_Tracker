# Business Ops Tracker

Daily financial / activity / health reporting for 23 business units,
plus Head Office oversight and Chairman admin. **Tracker-only** — the
ERP functions (accounting, journals, inventory, HR) were removed on
2026-11-17 and must not be re-added.

Stack: FastAPI + SQLAlchemy + Alembic (Postgres in production,
SQLite locally), React 19 + Vite frontend. Timezone is Asia/Yangon
throughout.

## Run locally

Prerequisites: Python 3.11+ and Node 22.

```sh
python -m venv .venv && .venv/bin/pip install -r requirements.txt
cd frontend && npm ci && cd ..

# Backend on :8123 (the Vite proxy expects it there). First boot
# migrates the DB, then seeds the dev accounts — their RANDOM passwords
# are written once to .dev_seed_passwords (0600, repo root).
ENVIRONMENT=development ALLOW_DEV_SEED=true \
  ALLOWED_ORIGINS=http://localhost:5173 \
  DATABASE_URL=sqlite:///./local_dev.db \
  .venv/bin/python -m uvicorn src.main:app --host 127.0.0.1 --port 8123

# Frontend on :5173 (proxies /api to :8123).
cd frontend && npm run dev
```

`ALLOWED_ORIGINS=http://localhost:5173` is required for browser
logins: the Vite proxy rewrites `Host` to the backend while the
browser `Origin` stays `localhost:5173`, and the backend's CSRF check
rejects the login POST without it. Never point a dev backend at a
shared or production database — `ALLOW_DEV_SEED` is for local SQLite
only (see `src/main.py: seed_users`).

## Tests

```sh
# Full backend suite (parallel; ~1 min with -n auto).
ENVIRONMENT=test DATABASE_URL=sqlite:// \
  .venv/bin/python -m pytest tests/ -q -n auto

# One file, and the frontend production build (CI also runs both,
# plus a pyflakes lint gate over src/ + tests/).
ENVIRONMENT=test DATABASE_URL=sqlite:// \
  .venv/bin/python -m pytest tests/test_http_smoke.py -q
cd frontend && npm run build
```

Playwright smoke tests: see `frontend/e2e/README.md` (scratch DB in
`/tmp`, never `local_dev.db`). CI runs them too
(`.github/workflows/tests.yml`, `e2e` job).

## Migrations

Migrations run automatically at app startup (`RUN_MIGRATIONS_ON_STARTUP`,
default true). To check or apply manually:

```sh
ENVIRONMENT=test DATABASE_URL=sqlite:////tmp/mig.db \
  MIGRATION_DATABASE_URL=sqlite:////tmp/mig.db \
  .venv/bin/python -m alembic upgrade head
.venv/bin/python -m alembic current   # single head: 621509460777
```

To create one: edit `src/models.py`, then
`.venv/bin/python -m alembic revision --autogenerate -m "<what changed>"`
and **read the generated file before committing it** — autogenerate
can flag the FK `ON DELETE` policy from `c2d6e0f4b8a3` (already
applied in Postgres); discard ondelete-only diffs. Keep exactly one
head; `tests/test_migrations_chain.py` guards models ↔ migrations
drift (tables, columns, and FK `ondelete`).

The chain still replays 8 ERP-era migrations (`erp_phase1..6`,
`erp_completion_*`, …) that build tables the ERP-removal migration
(`a720b6b7c616`) drops afterwards — they are history, not live schema,
and must not be re-added (PM-m11). Squashing them is deliberately not
done: editing applied history is riskier than replaying it.

## Deploy paths (read before deploying)

**No canonical deploy target is chosen yet — this is open question 1
(PM review 2026-11-18, C-1), and it blocks the Neon string, domain,
secrets, and drill work.** Two fully-written candidate paths exist;
both can now boot (migrations need only the database URL since the
C-1 fix), but do not deploy from either until the user picks one —
then archive or delete the losing spec.

| Path | Spec | Notes |
|---|---|---|
| VPS + Neon + Caddy | `docker-compose.prod.yml`, `scripts/deploy.sh` | Go-live runbook §2 documents this path only. Needs a Neon project + PITR, a domain, `.env` (0600) on the server. |
| DigitalOcean App Platform | `.do/app.yaml` | `deploy_on_push` is off (PM-12) — releases deploy explicitly from a tag. DB is dev-tier (`production: false` — flip before real data). `ALLOWED_HOSTS`/`ALLOWED_ORIGINS` still carry `replace-with-your-domain` placeholders: set the real domain before the first deploy. |

**Layout (PM-m6):** scripts live in three places on purpose — `scripts/`
holds the host-side shell entry points (`deploy.sh`, `backup.sh`),
`src/scripts/` holds Python CLIs that run inside the app container
(`audit_retention.py`, `reset_chairman.py`, `reset_totp.py`,
`rotate_data_key.py`), and `deploy/` holds the non-script deploy
inputs (`crontab`, `db_roles.sql`). `deploy.sh` stays in `scripts/`
(documented here, not moved). The go-live runbook
(`docs/BOT_GoLive_Runbook_2026-10-21.md`) is the execution script;
`CHANGELOG.md` records what changed and what is still pending
(the `v1.0.0` tag belongs on the go-live commit, not before).

Release identification (PM-11): the image bakes the commit in
(Dockerfile `ARG GIT_SHA`, compose `args:`) and `/healthz` reports
`version` + `commit` — runbook §2D compares that against the deployed
commit ("push ≠ live").

## Auth in five lines

- Roles: **Chairman** (one, seeded at first boot), **HO** (group),
  **CEO** (exactly one active account per business unit).
- Every account must rotate its password on first login; dev-seed
  passwords are random per boot (see `.dev_seed_passwords` above).
- The Chairman must enroll TOTP before the first session is issued.
- Sessions are HttpOnly cookies, 8 hours; a password change or reset
  revokes every live session for that account.
- Only the Chairman creates accounts (Manage Users, or
  `POST /api/admin/users`); a CEO completes a mandatory profile on
  first login.

## Where things live

- `src/` — API (`routers/`, `services/`, `jobs/`), `src/models.py`
  (the schema; FK `ON DELETE` policy lives here, matching migration
  `c2d6e0f4b8a3`), `src/main.py` (startup/assembly only; first-boot
  seeding lives in `src/seed.py`).
- `frontend/src/` — pages, `api/client.js`, `context/AuthContext.jsx`.
- `tests/` — pytest suite (HTTP tests go through the shared `client`
  fixture in root `conftest.py`).
- `docs/` — `README.md` (index), `flows.md` (the five flows + five
  lifecycles), `auth-model.md`, `delete-policy.md`,
  `backup-recovery.md`, `session-policy.md`,
  `single-instance-risk.md`, `security-decisions.md`, plus the
  incident runbook (`BOT_Incident_Runbook_2026-11-09.md`) and the
  go-live runbook (`BOT_GoLive_Runbook_2026-10-21.md`).
- `.github/workflows/` — `tests.yml` (pytest `-n auto` + frontend
  build + pyflakes + Playwright e2e) and `postgres-check.yml`.
