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

# Retention: prune dumps older than RETENTION_DAYS (default 28).
find "$BACKUP_DIR" -maxdepth 1 -name 'bot-*.dump' -type f -mtime +"$RETENTION_DAYS" -delete

echo "backup.sh: wrote $OUT ($(du -h "$OUT" | cut -f1))"
