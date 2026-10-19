# Base images are pinned by digest so a build is reproducible and can't silently
# pick up a changed upstream image; Dependabot (see .github/dependabot.yml)
# proposes digest bumps, which is how they stay patched. Node 20 reached
# end-of-life in April 2026, so the (build-stage-only) Node image is 22.
# --- Stage 1: build the React (Vite) frontend ---
FROM node:22-slim@sha256:43ac6c60b8f89723f746e8a92ce91abd5017e627ce1ddfe4238355d3a30b772c AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
# `npm ci` (not `npm install`): installs exactly what package-lock.json pins
# and fails if it's out of sync with package.json, instead of silently
# resolving newer ^-range versions — so the frontend that ships is the one
# that was tested, not whatever the registry served on deploy day.
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- Stage 2: Python runtime serving the API + built frontend ---
FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# curl only (for the HEALTHCHECK below). No gcc/libpq-dev: requirements.txt
# pins psycopg2-binary, which ships its own libpq as a prebuilt wheel, and
# every other dependency also installs from wheels on python:3.11-slim — so
# a C toolchain in the runtime image would just be unused attack surface.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY alembic ./alembic
COPY alembic.ini .
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

# /app/uploads must exist (and be owned by appuser) before USER switches
# below — it's also a volume mount point in docker-compose.yml, and Docker
# initializes a named volume's ownership from whatever already exists at
# that path in the image the first time it's created. Without this, the
# volume comes up owned by root and the app can never write to it.
#
# ONLY that directory is handed to appuser. The application code, migrations
# and built frontend stay root-owned and read-only to the process, so a
# compromised app can't rewrite its own source (or plant code that runs on
# the next start). The account has no login shell either.
RUN mkdir -p /app/uploads \
    && useradd --create-home --uid 1000 --shell /usr/sbin/nologin appuser \
    && chown appuser:appuser /app/uploads
USER appuser

# Render.com injects a dynamic $PORT at runtime. Never hardcode a port here.
# Default to 8000 for local docker runs where $PORT is not set.
ENV PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f "http://127.0.0.1:${PORT}/healthz" || exit 1

# Runs through `sh -c` so $PORT is expanded at container start (Render sets
# this dynamically) — but a plain shell-form CMD would leave uvicorn as a
# child of that shell, which doesn't forward SIGTERM to it, so `docker stop` /
# a platform redeploy would wait out the full grace period and hard-kill
# instead of uvicorn shutting down cleanly. `exec` replaces the shell
# process with uvicorn itself (becomes PID 1), fixing that while keeping
# the variable expansion.
#
# No --proxy-headers / --forwarded-allow-ips='*': trusting X-Forwarded-For from
# any peer lets a client spoof its IP, so uvicorn's own handling is switched off
# (--no-proxy-headers; it is on by default). The app resolves the real client IP
# itself (src/client_ip.py) via CLIENT_IP_HEADER / TRUSTED_PROXY_HOPS.
# --no-server-header omits "Server: uvicorn".
CMD ["sh", "-c", "exec uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8000} --no-proxy-headers --no-server-header"]
