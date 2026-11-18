#!/bin/sh
# Stop-app-before-migrate deploy for the compose path (PM-7).
#
# Usage (on the server, from the repo root):
#   ./scripts/deploy.sh [--no-build]
#
# Why this exists: `docker compose up -d --build` recreates the app
# container while the migration is still running, so the OLD code keeps
# serving against a NEW schema — a migration that drops/renames a
# column 500s in-flight old-code requests until the container swap.
# This script stops the app first, migrates, then starts the new code:
#   docker compose -f docker-compose.prod.yml stop app caddy
#   docker compose -f docker-compose.prod.yml run --rm migrate
#   docker compose -f docker-compose.prod.yml up -d app caddy
#
# Caddy is stopped too (F-1/F-10): on a first deploy Caddy is not
# running yet, so ending with `up -d app` alone would leave the domain
# serving nothing; and on later deploys a running Caddy would keep
# accepting connections and returning 502s while the app is down.
#
# Expected downtime: the site is down from `stop app caddy` until the
# new container passes /readyz — a few minutes on the VPS, dominated
# by the image rebuild (typically 1-3 min) plus boot + seeding
# (~30-60 s). Announce the window before running it; month-end
# mornings are the worst time to deploy. If the migration fails, the
# script stops before starting anything — bring the old code back with
# `docker compose -f docker-compose.prod.yml up -d app caddy` and investigate
# (a failed `alembic upgrade head` leaves the database untouched; see
# docs/backup-recovery.md). If the app does not become healthy, check
# `docker compose -f docker-compose.prod.yml logs app`.
#
# PM-15 pre-flight: before ANYTHING is stopped, the script validates the
# compose file (`docker compose config -q`), checks the required vars are
# in .env, and checks the database is reachable (pg_isready) — a missing
# or invalid .env used to turn a deploy into an outage before any check
# fired. Optional pre-deploy snapshot: PRE_DEPLOY_SNAPSHOT=1 takes a
# pg_dump via scripts/backup.sh first (kept 90 days in
# ~/bot-backups/pre-deploy by default), so a bad migration has something
# to restore from instead of relying on the never-tested restore path.
# PM-11: GIT_SHA is exported from `git rev-parse` so the built image
# reports the deployed commit on /healthz.
set -eu
cd "$(dirname "$0")/.."
COMPOSE="docker compose -f docker-compose.prod.yml"
BUILD="--build"
if [ "${1:-}" = "--no-build" ]; then
    BUILD=""
fi

# --- PM-15 pre-flight (runs BEFORE the stop step below) ---
echo "==> Pre-flight checks..."
if [ ! -f .env ]; then
    echo "==> ERROR: no .env file in $(pwd) — the compose file reads its required vars from it (see docs/BOT_GoLive_Runbook_2026-10-21.md §2B)" >&2
    exit 1
fi
for _var in DATABASE_URL JWT_SECRET_KEY DATA_ENCRYPTION_KEY DOMAIN; do
    if ! grep -q "^${_var}=" .env || [ -z "$(sed -n "s/^${_var}=//p" .env | head -n 1 | tr -d '"' | tr -d "'")" ]; then
        echo "==> ERROR: ${_var} is missing or empty in .env — refusing to deploy" >&2
        exit 1
    fi
done
unset _var
docker compose -f docker-compose.prod.yml config -q
echo "==> Pre-flight: compose file valid, required vars present."
# DB reachable: parse DATABASE_URL out of .env (never `export` it — it
# would land in shell history on a shared server, D-m10).
_DB_URL="$(sed -n 's/^DATABASE_URL=//p' .env | head -n 1 | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//")"
if command -v pg_isready >/dev/null 2>&1; then
    pg_isready -d "$_DB_URL" >/dev/null || { echo "==> ERROR: database not reachable (pg_isready failed for DATABASE_URL) — refusing to deploy" >&2; exit 1; }
elif command -v docker >/dev/null 2>&1; then
    docker run --rm -e "PGURL=$_DB_URL" postgres:16-alpine sh -c 'pg_isready -d "$PGURL"' >/dev/null || { echo "==> ERROR: database not reachable (pg_isready via docker failed) — refusing to deploy" >&2; exit 1; }
else
    echo "==> WARNING: no pg_isready or docker to check DB reachability; continuing without the check" >&2
fi
echo "==> Pre-flight: database reachable."
GIT_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo dev)"
export GIT_SHA
if [ "${PRE_DEPLOY_SNAPSHOT:-0}" = "1" ]; then
    echo "==> Pre-deploy snapshot (PRE_DEPLOY_SNAPSHOT=1)..."
    _MIG_URL="$(sed -n 's/^MIGRATION_DATABASE_URL=//p' .env | head -n 1 | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//")"
    BACKUP_DIR="${PRE_DEPLOY_BACKUP_DIR:-$HOME/bot-backups/pre-deploy}" \
    RETENTION_DAYS="${PRE_DEPLOY_RETENTION_DAYS:-90}" \
    DATABASE_URL="$_DB_URL" MIGRATION_DATABASE_URL="${_MIG_URL:-$_DB_URL}" \
    ./scripts/backup.sh
    unset _MIG_URL
fi
unset _DB_URL
echo "==> Pre-flight passed."
# --- end pre-flight ---

echo "==> Stopping app and Caddy (the site is down from here until the new container is healthy — Caddy is stopped too so it does not serve 502s during the deploy)..."
# A not-yet-deployed app is fine (first deploy): stop exits non-zero
# when there is no container, and that must not abort the deploy.
$COMPOSE stop app caddy || true
echo "==> Migrating..."
# shellcheck disable=SC2086 -- $BUILD is intentionally empty with --no-build
$COMPOSE run --rm $BUILD migrate
echo "==> Starting app and Caddy..."
# shellcheck disable=SC2086
$COMPOSE up -d $BUILD app caddy
echo "==> Waiting for /readyz (up to 2 minutes)..."
# curl lives inside the image (Dockerfile), so this works even when the
# host has no curl.
for i in $(seq 1 60); do
    if $COMPOSE exec -T app curl -fs "http://127.0.0.1:8000/readyz" >/dev/null 2>&1; then
        echo "==> Deploy complete: app is healthy."
        exit 0
    fi
    sleep 2
done
echo "==> ERROR: app did not become healthy within 2 minutes — check \`docker compose -f docker-compose.prod.yml logs app\`" >&2
exit 1
