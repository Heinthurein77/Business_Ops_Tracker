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
set -eu
cd "$(dirname "$0")/.."
COMPOSE="docker compose -f docker-compose.prod.yml"
BUILD="--build"
if [ "${1:-}" = "--no-build" ]; then
    BUILD=""
fi
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
