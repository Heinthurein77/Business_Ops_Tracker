# Single-instance risk — Business Ops Tracker

Accepted risk (D-11, BOT security review 2026-11-09). Every layer of
the production system is a single point of failure; this doc states
that explicitly and records the recovery for each layer, so an outage
is a known procedure instead of a surprise.

## The single points of failure

| Layer | As deployed | Why it is single |
|---|---|---|
| App | One `bot_app` container, one uvicorn worker (`Dockerfile` CMD has no `--workers`) | A crash, a bad deploy, or one long-running request blocks the whole tracker |
| Database | One Neon Postgres database, no replica/standby in either deploy path (the DO database is explicitly a dev-tier single node, `production: false`) | Neon down = whole app down, including the CEO daily-report form |
| Uploads | One Cloudflare R2 bucket, no versioning configured in the repo (versioning is a dashboard step — see `docs/backup-recovery.md`) | Bucket loss = attachment loss, except what versioning retains |
| Proxy (compose path) | One Caddy container (tls profile only) | Caddy down = no public access (the app itself keeps running on 127.0.0.1:8123) |
| Scheduler | Cron on the one VPS | The VPS down = no alerts, no retention, no log scan |

This is accepted for ~25 users: the cost and complexity of a second
instance, a read replica, or multi-region storage outweighs the risk
while the group is this size. The in-process export budget
(`src/edge.py`) is also why a second uvicorn worker must NOT be added
casually — the budget lives in process memory, so two workers would
split (and effectively double) it; moving it to the DB comes first.

## Recovery per layer

### App container crashes / bad deploy
1. `docker compose -f docker-compose.prod.yml logs app --tail=100` —
   read the actual error before doing anything else.
2. Restart: `docker compose -f docker-compose.prod.yml up -d app`
   (the `migrate` service is a no-op once the DB is at head).
3. If the deploy itself is bad, redeploy the previous image: `git log`
   for the last known-good commit, `git checkout <sha>`,
   `docker compose -f docker-compose.prod.yml up -d --build`, then
   `git checkout main` once verified. If the bad deploy already ran a
   migration, see "rollback" in the incident runbook ([BOT_Incident_Runbook_2026-11-09.md](BOT_Incident_Runbook_2026-11-09.md) §2C) — downgrades past
   `e4f8a2b6d0c5` cannot restore scrubbed audit data; a forward-fix is
   preferred.
4. If the container crash-loops at startup, check whether it is the DB
   (connection errors) or a migration failure — the two look identical
   from outside; the log line differs.

### Database down (Neon outage / bad DATABASE_URL / pool exhaustion)
1. Confirm: `curl http://127.0.0.1:8123/healthz` returns 200 while
   every API call 500s — `/healthz` does NOT touch the DB (known gap,
   D-1), so "healthy" with failing API calls means "DB down".
2. Wait out a transient Neon blip (startup retries ARE built —
   main.py retries DB-dependent startup steps 5 times over ~60 s;
   a restart after the DB is back is the recovery if those give up).
3. If the data itself is damaged, restore — see
   `docs/backup-recovery.md` (Neon PITR first, weekly `pg_dump`
   second). RPO 1 h, RTO 4 h.

### R2 down / uploads failing
1. CEO/HO daily-report attachments are the silent case — check
   `~/bot-log-scan.log` for the R2 failure lines and re-attach the
   missing documents.
2. If R2 is down for an extended period, the app keeps working without
   uploads (local fallback is dev-only and refuses in production);
   tell users to hold attachments and upload once R2 is back.
3. Bucket contents lost: restore previous versions from the R2
   dashboard (versioning on — see `docs/backup-recovery.md`).

### Caddy down (tls profile)
1. The app is still up on `127.0.0.1:8123` — verify with
   `curl http://127.0.0.1:8123/healthz`.
2. `docker compose -f docker-compose.prod.yml logs caddy` (usual cause:
   certificate issuance failed — DNS not pointed at the server, or
   ports 80/443 blocked), fix, `up -d caddy`.

### VPS down / cron jobs missed
1. Scheduled jobs are the first casualty: check `~/bot-alerts.log`,
   `~/bot-audit-retention.log`, `~/bot-log-scan.log` timestamps after
   any VPS outage; re-run `scheduled_alerts` by hand if a day was
   missed (it is idempotent).
2. The dead-man check (alert if a job has not run in > 36 h) is a
   separate workstream; until it lands, the log timestamps are the
   check.

## When to revisit

- User count grows well past 25, or exports/dashboard load starts
  hitting the 8-connection pool regularly.
- Any single-layer outage costs more than a day of work.
- At that point: second app instance (after the export budget moves
  out of process memory), Neon replica/standby, and R2 versioning +
  lifecycle as a baseline — in that order.
