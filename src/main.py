"""
Business Operations & CEO Tracking System — API entrypoint.

This process is a pure JSON API (mounted under /api/*) plus a static-file
server for the built React frontend. All page rendering/routing now happens
client-side; FastAPI never returns HTML except the SPA's index.html shell.
"""
import logging
import os
import secrets
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, OperationalError

from src.auth import (
    InsufficientRoleException,
    NotAuthenticatedException,
    PasswordChangeRequiredException,
    ProfileSetupRequiredException,
    get_current_user,
    hash_password,
)
from src.config import settings
from src.database import SessionLocal, engine, probe_engine, verify_pooler_timeouts
from src.edge import EdgeMiddleware
from src.models import BusinessUnit, User
from src.routers import admin, auth, ceo, ho, ops, users

REPO_ROOT = os.path.dirname(os.path.dirname(__file__))
FRONTEND_DIST = os.path.join(REPO_ROOT, "frontend", "dist")

logger = logging.getLogger("business_ops_tracker.seed")


def _configure_app_logging() -> None:
    """uvicorn only configures its own loggers, so without a handler here the
    app's INFO lines are dropped and its errors (business_ops_tracker.error:
    the id-tagged tracebacks behind every generic 500) print without a level,
    name or timestamp."""
    app_logger = logging.getLogger("business_ops_tracker")
    if app_logger.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    app_logger.addHandler(handler)
    app_logger.setLevel(logging.INFO)
    # Alembic's env.py installs its own root handler at migration time; without
    # this every app log line would then be printed twice.
    app_logger.propagate = False


_configure_app_logging()


def run_migrations() -> None:
    """Applies all pending Alembic migrations — safe to call repeatedly, a
    no-op once the DB is already at head. Called on startup unless
    RUN_MIGRATIONS_ON_STARTUP=false (migrations then run as a release job).
    alembic/env.py connects with settings.MIGRATION_DATABASE_URL."""
    alembic_cfg = Config(os.path.join(REPO_ROOT, "alembic.ini"))
    alembic_cfg.set_main_option("script_location", os.path.join(REPO_ROOT, "alembic"))
    command.upgrade(alembic_cfg, "head")


# ---------------------------------------------------------------------------
# Startup retries (D-2). Startup used to be all-or-nothing: one Neon blip
# at boot crashed the process and `restart: unless-stopped` turned it into
# a tight crash loop, with no way to tell "DB down" from "bad migration"
# without reading logs. DB-dependent startup steps are now retried with
# backoff (5 attempts over ~60 s) and the two failure kinds log distinct
# lines. Traffic is still only served after the steps succeed — lifespan
# is not restructured to yield early.
# ---------------------------------------------------------------------------

STARTUP_DB_MAX_ATTEMPTS = 5
# Seconds between attempts: 5 + 10 + 15 + 30 = 60 s of backoff across the
# 5 attempts (plus the attempts' own time).
STARTUP_DB_RETRY_DELAYS = (5, 10, 15, 30)

_DB_UNREACHABLE_MARKERS = (
    "could not connect",
    "connection refused",
    "connection timed out",
    "could not translate host",
    "name or service not known",
    "server closed the connection",
    "connection reset",
)


def _is_db_unreachable(exc: BaseException) -> bool:
    """True when the error means "the database cannot be reached" as
    opposed to "the database answered and the step itself failed". An
    OperationalError is the usual shape; the message markers cover drivers
    that surface the same failure differently."""
    if isinstance(exc, OperationalError):
        return True
    message = str(exc).lower()
    return any(marker in message for marker in _DB_UNREACHABLE_MARKERS)


def _run_startup_step(step_name: str, func, *, attempts: int = STARTUP_DB_MAX_ATTEMPTS,
                      delays=STARTUP_DB_RETRY_DELAYS, sleep=time.sleep):
    """Runs one DB-dependent startup step, retrying transient failures with
    backoff. Logs a distinct line for "database unreachable" vs a step that
    failed on its own merits ("migration failed" / "seeding failed"), then
    re-raises after the last attempt so the process still fails loudly
    instead of serving traffic against a broken schema."""
    last_exc: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return func()
        except Exception as exc:  # noqa: BLE001 — retried, then re-raised
            last_exc = exc
            if attempt == attempts:
                break
            delay = delays[min(attempt - 1, len(delays) - 1)]
            if _is_db_unreachable(exc):
                logger.warning(
                    "Startup step %r: database unreachable (attempt %d/%d): %s — retrying in %ss",
                    step_name, attempt, attempts, exc, delay,
                )
            elif step_name == "migrations":
                logger.warning(
                    "Startup step 'migrations': migration failed (attempt %d/%d): %s — retrying in %ss",
                    attempt, attempts, exc, delay,
                )
            else:
                logger.warning(
                    "Startup step %r failed (attempt %d/%d): %s — retrying in %ss",
                    step_name, attempt, attempts, exc, delay,
                )
            sleep(delay)
    logger.error(
        "Startup step %r failed after %d attempts; giving up: %s", step_name, attempts, last_exc
    )
    raise last_exc  # type: ignore[misc]


# File a generated (random) bootstrap Chairman password is written to
# instead of the log stream (D-13). It lives NEXT TO the uploads
# directory — a sibling of LOCAL_UPLOAD_DIR, never inside it — with 0600
# perms, and the server operator fetches it with:
#   docker compose exec web cat /app/.bootstrap_chairman_password
# then deletes the file after the first login. m-17: the uploads directory
# is served (session-gated) in non-production, so a live credential file
# must not sit in it; the file is also deleted automatically after the
# first successful Chairman login (see routers/auth.py login()).
BOOTSTRAP_PASSWORD_FILENAME = ".bootstrap_chairman_password"


def _bootstrap_password_path() -> Path:
    return Path(settings.LOCAL_UPLOAD_DIR).parent / BOOTSTRAP_PASSWORD_FILENAME


def _delete_bootstrap_password_file() -> bool:
    """Deletes the bootstrap password file after the first successful
    Chairman login. Best effort: returns False (never raises) when there
    is nothing to delete or the delete fails, so login can never fail
    over cleanup."""
    try:
        _bootstrap_password_path().unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _write_bootstrap_password_file(password: str) -> Path:
    """Writes the generated bootstrap password to the 0600 file above and
    returns its path. The full value never goes to the log stream."""
    path = _bootstrap_password_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, password.encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(path, 0o600)
    return path


# Real business unit names, keyed by BU number — falls back to "Business
# Unit N" for any number not listed here (e.g. while the remaining ones are
# still being confirmed).
BUSINESS_UNIT_NAMES = {
    1: "ABC Convenience Store",
    2: "ABC Logistics (Logistics)",
    3: "ABC Logistics (Flour)",
    4: "ABC Logistics (Project)",
    5: "ABC Telecom",
    6: "ABC Content Solution",
    7: "ABC Beauty (Principal)",
    8: "ABC Beauty (Distribution)",
    9: "ABC Medical",
    10: "Hope Land Real Estate",
    11: "Myanmar Herbal Medicine",
    12: "MIB Coffee",
    13: "MIB Production",
    14: "MIB FMCG Distribution",
    15: "MIB FMCG Principal (PM)",
    16: "MIB FMCG Principal (HM)",
    17: "MIB FMCG Principal (Shiny)",
}


def seed_business_units(db) -> None:
    existing_codes = {bu.code for bu in db.query(BusinessUnit).all()}
    for i in range(1, settings.TOTAL_BUSINESS_UNITS + 1):
        code = f"BU{i}"
        if code in existing_codes:
            continue
        db.add(BusinessUnit(code=code, name=BUSINESS_UNIT_NAMES.get(i, f"Business Unit {i}")))
    try:
        db.commit()
    except IntegrityError:
        # D-m3: same race as the chairman seed below — two instances in a
        # rolling deploy can both pass the existence check above and race
        # the unique `code` constraint. The loser rolls back and moves on
        # instead of crashing startup; the winner already seeded the BU.
        db.rollback()


def _ensure_user(db, username, password, role, require_password_change, business_unit_id=None, full_name=None) -> None:
    user = db.query(User).filter(User.username == username).first()
    if user:
        return
    db.add(
        User(
            username=username,
            hashed_password=hash_password(password),
            role=role,
            business_unit_id=business_unit_id,
            require_password_change=require_password_change,
            full_name=full_name,
            # CEOs land in the mandatory Profile Setup flow after their first
            # password rotation; Chairman/HO have no profile, so the column
            # default (True) is left as-is for them.
            is_profile_completed=(role != "CEO"),
        )
    )


def seed_users(db) -> dict:
    # Seeded on first boot only (_ensure_user is a no-op once the account
    # exists) with a random password per account, so every seeded account —
    # including the admin-tier ones — must rotate it on first login.
    #
    # In production this can't use a hardcoded, source-visible password:
    # anyone who's read this file could log in as e.g. ceo_bu14 the moment
    # a fresh prod database comes up, before its real owner ever has. So a
    # real (ENVIRONMENT=production) deploy only bootstraps the one Chairman
    # account — with the password from BOOTSTRAP_CHAIRMAN_PASSWORD when that
    # secret is configured (never logged), otherwise a random one written to
    # the 0600 file /app/.bootstrap_chairman_password (see D-13, m-17: a
    # sibling of the uploads directory, never inside it) — and stops there;
    # every other account is created afterward through Manage Users
    # (Chairman-only), which already hands out its own one-time temporary
    # password per account instead of a fixed, publicly-known string.
    #
    # Returns the {username: password} pairs it generated this run (empty
    # when nothing was seeded), so a dev/test caller can log in with them;
    # the lifespan caller ignores the return value.
    if settings.ENVIRONMENT.lower() == "production":
        if db.query(User).filter(User.username == "chairman").first() is None:
            configured_password = settings.BOOTSTRAP_CHAIRMAN_PASSWORD
            bootstrap_password = configured_password or secrets.token_urlsafe(18)
            _ensure_user(db, "chairman", bootstrap_password, "Chairman", True, full_name="Chairman")
            try:
                db.commit()
            except IntegrityError:
                # Two instances/workers can both pass the "does chairman
                # exist?" check above during a rolling deploy and race to
                # insert it — the username's unique constraint makes the
                # loser's commit fail. That's fine: it means the winner
                # already seeded the account, just not with this process's
                # password, so roll back and move on instead of crashing
                # this instance's startup.
                db.rollback()
            else:
                if configured_password:
                    # Deliberately does not echo the password.
                    logger.info(
                        "First boot: seeded the 'chairman' account from the configured "
                        "BOOTSTRAP_CHAIRMAN_PASSWORD secret; a password change is required at first login."
                    )
                else:
                    # D-13: the generated password is NOT printed to the log
                    # stream — anyone with log access would hold the Chairman
                    # credential. Only the file path is logged (m-17: not
                    # even a prefix hint — a prefix narrows a brute-force
                    # search); the full value is in the 0600 file,
                    # retrievable by the server operator (see
                    # _write_bootstrap_password_file). The file is deleted
                    # automatically after the first successful Chairman login.
                    password_file = _write_bootstrap_password_file(bootstrap_password)
                    logger.warning(
                        "First boot: seeded the 'chairman' account with a random temporary "
                        "password — the full value is NOT shown here; it was written to %s. "
                        "Delete that file after first login.",
                        password_file,
                    )
        return {}

    # Defence in depth for the dev seeding below (audit finding L4):
    # config.py already refuses to boot on an unrecognised ENVIRONMENT,
    # but this branch must be unreachable outside development/test on its
    # own merits — if a future environment name ever slips past the config
    # check, fail loudly here rather than seed dev accounts.
    if settings.ENVIRONMENT not in ("development", "test"):
        raise RuntimeError(
            f"Refusing to seed dev accounts with ENVIRONMENT={settings.ENVIRONMENT!r}: "
            "the dev seed branch is for development/test only."
        )

    # M-02: the publicly-known-password branch is deleted. Dev accounts are
    # seeded ONLY when ALLOW_DEV_SEED=true is set explicitly AND the
    # database is local (sqlite or localhost) — one wrong ENVIRONMENT value
    # on a fresh production database can no longer create 25 accounts with
    # passwords readable in the public source. Each account gets a random
    # password, written once to a 0600 file (same pattern as the bootstrap
    # file) and returned to the caller; the values are never logged.
    if not settings.ALLOW_DEV_SEED:
        logger.warning(
            "Dev seed accounts are disabled (ALLOW_DEV_SEED is not true) — "
            "no dev accounts seeded. Create accounts through Manage Users."
        )
        return {}
    if not _is_local_database_url(settings.DATABASE_URL):
        raise RuntimeError(
            "Refusing to seed dev accounts: ALLOW_DEV_SEED is set but DATABASE_URL "
            "is not local (sqlite or localhost) — dev seeding is for local "
            "databases only."
        )

    generated: dict[str, str] = {}

    def _seed_dev_account(username, role, business_unit_id=None, full_name=None):
        # _ensure_user is a no-op when the account already exists, so only
        # newly created accounts get a generated password recorded here.
        if db.query(User).filter(User.username == username).first() is not None:
            return
        password = secrets.token_urlsafe(18)
        _ensure_user(db, username, password, role, True,
                     business_unit_id=business_unit_id, full_name=full_name)
        generated[username] = password

    _seed_dev_account("chairman", "Chairman", full_name="Chairman")
    _seed_dev_account("ho_staff", "HO", full_name="Head Office Staff")

    business_units = {bu.code: bu.id for bu in db.query(BusinessUnit).all()}
    for i in range(1, settings.TOTAL_BUSINESS_UNITS + 1):
        code = f"BU{i}"
        _seed_dev_account(
            f"ceo_bu{i}",
            "CEO",
            business_unit_id=business_units.get(code),
            full_name=f"CEO - Business Unit {i}",
        )
    db.commit()
    if generated:
        seed_file = _write_dev_seed_password_file(generated)
        logger.warning(
            "Dev seed accounts created with random passwords (rotate them at first "
            "login) — passwords written once to %s (0600); they are NOT shown here. "
            "NEVER run this mode against a shared database.",
            seed_file,
        )
    return generated


def _is_local_database_url(url: str) -> bool:
    """True for sqlite URLs and for Postgres on localhost — the only
    databases dev seeding may ever touch (M-02)."""
    if url.startswith("sqlite:"):
        return True
    try:
        host = (make_url(url).host or "").lower()
    except Exception:  # noqa: BLE001 - an unparseable URL is not local
        return False
    return host in {"localhost", "127.0.0.1", "::1"}


def _write_dev_seed_password_file(passwords: dict[str, str]) -> Path:
    """Writes the generated dev-seed passwords to a 0600 file next to the
    uploads directory (same pattern as the bootstrap file) and returns its
    path. The values are never logged."""
    path = Path(settings.LOCAL_UPLOAD_DIR).parent / ".dev_seed_passwords"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(
            fd,
            "".join(f"{username}: {password}\n" for username, password in sorted(passwords.items())).encode("utf-8"),
        )
    finally:
        os.close(fd)
    os.chmod(path, 0o600)
    return path


@asynccontextmanager
async def lifespan(app: FastAPI):
    # D-m2: log the effective client-IP source at startup so a misconfigured
    # proxy (wrong header / hop count) is visible in the logs instead of
    # silently mis-attributing every throttle bucket and audit IP.
    logger.info(
        "Client-IP source: CLIENT_IP_HEADER=%r TRUSTED_PROXY_HOPS=%s",
        settings.CLIENT_IP_HEADER or "(X-Forwarded-For, right-counted)",
        settings.TRUSTED_PROXY_HOPS,
    )
    # m-28: fail startup in production when the client-IP self-check fails
    # — a misconfigured proxy silently disables per-IP login throttling,
    # and the app must not serve traffic in that state.
    from src.throttle import enforce_client_ip_configuration

    enforce_client_ip_configuration()
    if settings.RUN_MIGRATIONS_ON_STARTUP:
        _run_startup_step("migrations", run_migrations)
    else:
        logger.info(
            "RUN_MIGRATIONS_ON_STARTUP is false: schema migrations are expected to have "
            "been applied already by a release job."
        )
    db = SessionLocal()
    try:
        # Seeding is idempotent (existence checks before insert), so a
        # retried attempt after a partial failure is safe.
        _run_startup_step("seeding", lambda: (seed_business_units(db), seed_users(db)))
    finally:
        db.close()
    # M-09: on a Neon pooler URL the query timeouts are not sent at connect
    # time — warn loudly if the per-role timeouts (deploy/db_roles.sql)
    # cannot be verified as set. Never fails startup (see database.py).
    verify_pooler_timeouts(engine)
    yield


# The interactive API docs / OpenAPI schema enumerate every route and are
# only for development; a public deployment doesn't publish them.
app = FastAPI(
    title=settings.APP_NAME,
    lifespan=lifespan,
    docs_url=None if settings.IS_PRODUCTION else "/docs",
    redoc_url=None if settings.IS_PRODUCTION else "/redoc",
    openapi_url=None if settings.IS_PRODUCTION else "/openapi.json",
)
# The built JS bundle is ~1MB uncompressed; gzip cuts that (and every JSON
# response) by roughly 70-75% in transit. Only kicks in above minimum_size,
# so it's a no-op on small API responses.
app.add_middleware(GZipMiddleware, minimum_size=1000)
# Added LAST so it is the outermost layer (Starlette runs the last-added
# middleware first): host/origin/body-size checks happen before anything
# else touches the request, and its security headers land on every response
# including gzip's and the ones it generates itself. See src/edge.py.
app.add_middleware(EdgeMiddleware)

app.include_router(auth.router)
app.include_router(ceo.router)
app.include_router(ho.router)
app.include_router(admin.router)
app.include_router(users.router)
app.include_router(ops.router)


def _assert_unique_routes(fastapi_app) -> None:
    """m-25: two routers share the /api/admin prefix (admin.py and
    users.py), so a future route defined twice would silently shadow the
    first with no warning. Fail fast at startup on any (path, method)
    collision. Same path with different methods (GET + POST /api/ops/...)
    is legitimate and is not a collision."""
    from fastapi.routing import APIRoute

    seen: dict[tuple[str, str], str] = {}
    duplicates: list[str] = []
    for route in fastapi_app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in sorted(route.methods or ()):
            key = (route.path, method)
            if key in seen:
                duplicates.append(f"{method} {route.path} ({seen[key]} vs {route.name})")
            else:
                seen[key] = route.name
    if duplicates:
        raise RuntimeError(
            "duplicate API routes (same path + method) — one shadows the other: "
            + "; ".join(duplicates)
        )


_assert_unique_routes(app)


# ---------------------------------------------------------------------------
# Exception handlers: every auth failure becomes a JSON response, never a
# server-side redirect — the React app owns all navigation decisions.
# ---------------------------------------------------------------------------

@app.exception_handler(NotAuthenticatedException)
async def handle_not_authenticated(request: Request, exc: NotAuthenticatedException):
    return JSONResponse(status_code=401, content={"detail": "Not authenticated"})


@app.exception_handler(InsufficientRoleException)
async def handle_insufficient_role(request: Request, exc: InsufficientRoleException):
    return JSONResponse(status_code=403, content={"detail": "Insufficient role", "redirect_to": exc.redirect_to})


@app.exception_handler(PasswordChangeRequiredException)
async def handle_password_change_required(request: Request, exc: PasswordChangeRequiredException):
    return JSONResponse(
        status_code=403,
        content={"detail": "PASSWORD_CHANGE_REQUIRED", "redirect_to": "/force-password-change"},
    )


@app.exception_handler(ProfileSetupRequiredException)
async def handle_profile_setup_required(request: Request, exc: ProfileSetupRequiredException):
    return JSONResponse(
        status_code=403,
        content={"detail": "PROFILE_SETUP_REQUIRED", "redirect_to": "/profile-setup"},
    )


@app.api_route("/healthz", methods=["GET", "HEAD"])
def healthz():
    return {"status": "ok"}


# Readiness (D-1), split from liveness: /healthz only says the process is
# up, /readyz proves the database answers. The Docker HEALTHCHECK and the
# DO health check point at /readyz, so a dead database makes the platform
# restart / stop routing to the container instead of reporting "healthy"
# while every API call 500s. The ping runs in a daemon thread bounded by
# READY_TIMEOUT_SECONDS — a hung database must fail the probe fast, not
# hang the health check until the platform's own timeout.
READY_TIMEOUT_SECONDS = 3.0


def _db_is_ready(timeout_seconds: float = READY_TIMEOUT_SECONDS) -> bool:
    result: dict[str, bool] = {}

    def _ping() -> None:
        try:
            # m-16: the probe uses its own 1-connection engine (pool_size=1,
            # pool_timeout=3 in src/database.py), so a probe checkout can
            # never eat the main pool — and under pool exhaustion the thread
            # no longer waits out the main pool's 10 s timeout after the
            # probe already returned 503.
            with probe_engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            result["ok"] = True
        except Exception:
            result["ok"] = False

    thread = threading.Thread(target=_ping, daemon=True)
    thread.start()
    thread.join(timeout_seconds)
    return result.get("ok", False)


@app.api_route("/readyz", methods=["GET", "HEAD"])
def readyz():
    if _db_is_ready():
        return {"status": "ok"}
    return JSONResponse(status_code=503, content={"status": "not_ready", "detail": "Database unavailable"})


# ---------------------------------------------------------------------------
# Local file storage fallback (see r2_storage.py) — serves whatever the local
# dev/Docker fallback wrote when R2 isn't configured. Development only:
# nothing is ever written locally in production (R2, or a loud failure, is the
# only path — see upload_fileobj()'s production gate), so the directory is
# neither created nor served there and /uploads/* is just a 404.
#
# Session-gated (audit finding L1): this used to be an unauthenticated
# StaticFiles mount, which made every uploaded file a bearer URL — anyone
# with the link had the file forever. The route below runs the exact same
# session validation as every API route (cookie + token_version, via
# get_current_user); the frontend session is that cookie, so a signed-in
# browser's plain <img>/link fetches keep working unchanged, and everyone
# else gets the usual 401.
# ---------------------------------------------------------------------------

if not settings.IS_PRODUCTION:
    os.makedirs(settings.LOCAL_UPLOAD_DIR, exist_ok=True)

    def _resolve_local_upload(file_path: str) -> Path:
        """Maps a /uploads/* tail onto a real file inside LOCAL_UPLOAD_DIR,
        else raises 404. resolve() collapses `..` segments BEFORE the
        containment check, so no tail can escape the upload directory."""
        base = Path(settings.LOCAL_UPLOAD_DIR).resolve()
        target = (base / file_path).resolve()
        if target != base and base not in target.parents:
            raise HTTPException(status_code=404, detail="Not found")
        # m-17: dotfiles are never served — the bootstrap/recovery
        # credential files live outside this directory now, but a dotfile
        # must never become downloadable through this route by mistake.
        if any(part.startswith(".") for part in Path(file_path).parts):
            raise HTTPException(status_code=404, detail="Not found")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="Not found")
        return target

    @app.get("/uploads/{file_path:path}")
    async def serve_local_upload(file_path: str, user: User = Depends(get_current_user)):
        return FileResponse(_resolve_local_upload(file_path))


# ---------------------------------------------------------------------------
# Static frontend (built React app) + SPA fallback
# ---------------------------------------------------------------------------

if os.path.isdir(os.path.join(FRONTEND_DIST, "assets")):
    app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIST, "assets")), name="frontend-assets")


_NOT_SPA_PREFIXES = {"api", "assets", "uploads"}


@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    """Serves the built React app for any non-API route, so client-side
    routing (React Router) handles the actual page. Falls back to a plain
    404 JSON body if the frontend hasn't been built yet (local API-only dev).

    A path that belongs to the API, the asset bundle or the upload store but
    matched nothing (a mistyped endpoint, a missing hashed asset, /uploads in
    production) is a real 404 — answering it with index.html would hand
    clients and scanners a 200 HTML page for a URL that doesn't exist."""
    if full_path.lstrip("/").split("/", 1)[0] in _NOT_SPA_PREFIXES:
        return JSONResponse(status_code=404, content={"detail": "Not found"})
    index_path = os.path.join(FRONTEND_DIST, "index.html")
    if os.path.isfile(index_path):
        return FileResponse(index_path)
    return JSONResponse(
        status_code=404,
        content={"detail": "Frontend build not found. Run `npm run build` in frontend/, or use the Vite dev server."},
    )
