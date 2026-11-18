#!/bin/sh
# Weekly database backup: pg_dump of the production database (R-4).
#
# Usage (on the server, from cron — see deploy/crontab):
#   BACKUP_DIR=~/bot-backups scripts/backup.sh
#
# - Connects as MIGRATION_DATABASE_URL when set (the schema-owner role,
#   which can read everything), else DATABASE_URL.
# - Prefers a local `pg_dump` binary; otherwise runs pg_dump from the
#   postgres:16-alpine image (the version this project uses locally in
#   docker-compose.yml) so the server needs no Postgres client installed.
# - Writes BACKUP_DIR/bot-YYYYMMDD-HHMM.dump (custom format, -Fc) and
#   prunes dumps older than 28 days (keeps ~4 weekly dumps). Monthly
#   long-term copies are manual: copy a dump out of BACKUP_DIR and name
#   it bot-monthly-YYYYMM.dump.
# - Exits non-zero (and prints to stderr, which cron mails/logs) if the
#   dump fails or the output file is empty — a silent empty backup is
#   worse than no backup.
# - PM-13: verifies the dump is restorable/parseable (`pg_restore -l`
#   lists its table of contents without restoring anything) before
#   calling it a backup — a dump that does not list is not a backup.
#   The off-server copy needs its own `pg_restore -l` check after every
#   copy; see docs/backup-recovery.md (the check here only proves the
#   on-server file).
# - On success upserts a `job_heartbeats` row for "backup" (G-4), so the
#   log-scan dead-man check notices if the Sunday cron silently dies
#   (weekly job, 8-day grace — see src/jobs/log_scan.py).
# - The dump contains the DATABASE CONTENTS only, not R2 file contents
#   and not the DATA_ENCRYPTION_KEY: keep the key copy off-platform
#   (see docs/backup-recovery.md), or the encrypted CEO fields in the
#   dump are unreadable after a restore.
set -eu

BACKUP_DIR="${BACKUP_DIR:-$HOME/bot-backups}"
RETENTION_DAYS="${RETENTION_DAYS:-28}"
DB_URL="${MIGRATION_DATABASE_URL:-${DATABASE_URL:-}}"

if [ -z "$DB_URL" ]; then
    echo "backup.sh: set DATABASE_URL (or MIGRATION_DATABASE_URL) first" >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d-%H%M)"
OUT="$BACKUP_DIR/bot-$STAMP.dump"

if command -v pg_dump >/dev/null 2>&1; then
    pg_dump "$DB_URL" -Fc -f "$OUT"
else
    # No local client: run pg_dump from the pinned Postgres image. The
    # URL is passed through the environment (never on the command line,
    # so it does not land in `docker ps` / shell history).
    docker run --rm -e "PGURL=$DB_URL" -v "$BACKUP_DIR:/backup" \
        postgres:16-alpine sh -c 'pg_dump "$PGURL" -Fc -f "/backup/bot-'"$STAMP"'.dump"'
fi

if [ ! -s "$OUT" ]; then
    echo "backup.sh: dump failed or empty: $OUT" >&2
    exit 1
fi

# PM-13: verify the dump is parseable/restorable before calling it a
# backup. `pg_restore -l` lists the dump's table of contents without
# restoring anything, so a truncated or corrupt dump fails here instead
# of being discovered during a real incident. Same tool selection as the
# dump step above: a local pg_restore, else the pinned Postgres image.
if command -v pg_restore >/dev/null 2>&1; then
    if ! pg_restore -l "$OUT" >/dev/null; then
        echo "backup.sh: dump failed verification (pg_restore -l): $OUT" >&2
        exit 1
    fi
elif command -v docker >/dev/null 2>&1; then
    if ! docker run --rm -v "$BACKUP_DIR:/backup" \
        postgres:16-alpine pg_restore -l "/backup/bot-$STAMP.dump" >/dev/null; then
        echo "backup.sh: dump failed verification (pg_restore -l via docker): $OUT" >&2
        exit 1
    fi
else
    echo "backup.sh: warning: no pg_restore or docker to verify the dump; skipping verification" >&2
fi

echo "backup.sh: dump verified (pg_restore -l lists $OUT)"

# Retention: prune dumps older than RETENTION_DAYS (default 28).
find "$BACKUP_DIR" -maxdepth 1 -name 'bot-*.dump' -type f -mtime +"$RETENTION_DAYS" -delete

echo "backup.sh: wrote $OUT ($(du -h "$OUT" | cut -f1))"

# G-4 (2026-11-17 review): heartbeat for the log-scan dead-man check,
# so a dead Sunday backup cron is visible instead of silent. Same
# upsert shape src/scripts/audit_retention.py uses for audit_retention
# (INSERT ... ON CONFLICT on job_heartbeats), run inside the app
# container — which has sqlalchemy and DATABASE_URL — because this
# script runs on the host and psql may not be installed. Best effort
# by design: a heartbeat failure must not fail a good backup; it only
# warns, and log_scan flags "backup" as stale (8-day weekly grace)
# until the next successful run writes one. Only reached on success:
# a failed/empty dump exits above before this line.
if [ -f docker-compose.prod.yml ] && command -v docker >/dev/null 2>&1; then
    if ! docker compose -f docker-compose.prod.yml exec -T app python -c 'import os
import sqlalchemy as sa
from datetime import datetime, timezone
engine = sa.create_engine(os.environ["DATABASE_URL"], hide_parameters=True)
with engine.begin() as conn:
    conn.execute(
        sa.text(
            "INSERT INTO job_heartbeats (job_name, last_run_at, last_status) "
            "VALUES (:job, :now, :status) "
            "ON CONFLICT (job_name) DO UPDATE SET "
            "last_run_at = EXCLUDED.last_run_at, last_status = EXCLUDED.last_status"
        ),
        {"job": "backup", "now": datetime.now(timezone.utc).replace(tzinfo=None), "status": "ok"},
    )
'; then
        echo "backup.sh: warning: backup heartbeat not written; log_scan will flag backup as stale" >&2
    fi
else
    echo "backup.sh: warning: docker compose unavailable; backup heartbeat not written" >&2
fi
