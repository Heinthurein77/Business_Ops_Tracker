# Backup & recovery — Business Ops Tracker

## What is backed up, and how

| Layer | Mechanism | Schedule | Where it lives |
|---|---|---|---|
| Database (Neon Postgres) | Neon point-in-time recovery (PITR) — restore to any point inside the retention window | Continuous (Neon-managed) | Neon dashboard — retention must be **enabled and verified** (see "User-side steps" below); the repo cannot configure it |
| Database (second copy) | `scripts/backup.sh` — `pg_dump -Fc` of the whole database | Weekly (Sunday 03:30, see `deploy/crontab`) | Separate storage from Neon (see script: `BACKUP_DIR`, default `~/bot-backups`; copy off the server — a dump on the same disk as the app is not a backup) |
| Uploads (Cloudflare R2) | R2 bucket versioning — deleted/overwritten objects stay recoverable | Continuous | R2 dashboard — versioning must be **enabled** (see below) |
| Secrets | `DATA_ENCRYPTION_KEY` copy outside the hosting platform; `JWT_SECRET_KEY` / DB / R2 credentials in the deployer's password manager | On every rotation | Off-platform (password manager). Without the encryption key, CEO phone/email/address/date-of-birth and health notes are **unrecoverable** — there is no reset path. |

The `bot_uploads_data` Docker volume is NOT a backup: in production
uploads go to R2, so the volume holds nothing.

## RPO / RTO

- **RPO (how much data we can afford to lose): 1 hour.** Neon PITR
  covers anything inside its retention window; the weekly `pg_dump` is
  the fallback if Neon itself is unavailable, so worst case we lose up
  to a week of data from the dump path — which is why PITR (not the
  dump) is the primary recovery path, and why the dump is a second copy,
  not the plan.
- **RTO (how fast we must be back): 4 hours** for a database restore to
  a scratch DB + re-pointing the app; 1 working day if the restore has
  to be rebuilt from the weekly dump (manual steps, see below).

These are recorded in the go-live runbook (§2A) as go-live gates: the
first successful restore drill must be recorded before real business
data goes in.

## Restore drill (quarterly, and once before go-live)

1. In the Neon dashboard, restore to a **scratch database** (new branch
   or new database — never restore over the live database).
2. Point a local checkout at the scratch DB and run the migration-chain
   check:
   ```bash
   DATABASE_URL='<scratch connection string>' alembic heads
   DATABASE_URL='<scratch connection string>' alembic upgrade head
   # expect: single head a720b6b7c616, upgrade is a no-op
   ```
3. Start the app against the scratch DB (test env) and spot-check: a
   CEO can log in, the CEO dashboard loads, and one
   attachment URL resolves from R2.
4. Record the drill (date, restorer, time taken, result) in the runbook.
   A drill that is not recorded did not happen.

## Restoring for real (database destroyed or corrupted)

1. **Stop the app** so nothing writes during the restore:
   `docker compose -f docker-compose.prod.yml down` (keeps volumes).
2. **Preserve evidence first** (incident runbook): export `audit_logs`
   before any retention run touches them — if the DB is still readable,
   `pg_dump` the live DB to a separate file even if it is corrupt.
3. Restore via Neon PITR to a new database at the last known-good time.
4. Run the migration-chain check above against the restored DB.
5. Update `DATABASE_URL` (and `MIGRATION_DATABASE_URL`, if the roles
   are in use) to the restored database and `up -d --build`.
6. Verify §2D of the runbook (login, CEO dashboard, one attachment
   resolves).
7. If Neon PITR is unavailable, restore the latest weekly dump instead:
   ```bash
   pg_restore -d '<new empty database URL>' --clean --if-exists ~/bot-backups/bot-<latest>.dump
   ```
   then steps 4–6. Data after the dump's timestamp is lost — that is
   the documented worst case; tell the Chairman before doing it.

## Uploads (R2) recovery

- Deleted or overwritten file: restore the previous version from the
  R2 dashboard (versioning on) — the app stores the object key, so
  re-uploading the old version under the same key restores the link.
- Whole bucket lost: re-upload from the weekly server copy only if one
  exists — there is no automated R2-to-disk backup today; the weekly
  `pg_dump` does **not** contain file contents. Accepting this is a
  deliberate scope limit for ~25 users (single bucket, see
  `docs/single-instance-risk.md`).

## Retention

- `pg_dump` files: keep 4 weekly dumps (script prunes older ones),
  then one monthly dump kept for 12 months (copy manually out of
  `BACKUP_DIR` and rename `bot-monthly-YYYYMM.dump`).
- Neon PITR window: whatever the Neon plan retains (verify in the
  dashboard; extend if the plan allows — the drill above is the check
  that it actually works).
- R2 versions: keep until the bucket lifecycle says otherwise; do not
  turn versioning off to save space without a Chairman decision.

## User-side steps (NOT done by this change — pending checklist)

- [ ] Enable Neon PITR / confirm the retention window in the Neon
      dashboard, and record the window here.
- [ ] Enable R2 bucket versioning in the Cloudflare dashboard.
- [ ] Choose the off-server home for weekly dumps (`BACKUP_DIR`) and
      copy the first dump there.
- [ ] Verify the off-platform `DATA_ENCRYPTION_KEY` copy by decrypting
      a canary value with it (see incident runbook).
- [ ] Run and record the first restore drill (go-live gate).
