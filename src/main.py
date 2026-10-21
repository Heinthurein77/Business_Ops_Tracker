"""
Business Operations & CEO Tracking System — API entrypoint.

This process is a pure JSON API (mounted under /api/*) plus a static-file
server for the built React frontend. All page rendering/routing now happens
client-side; FastAPI never returns HTML except the SPA's index.html shell.
"""
import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError

from src.auth import (
    InsufficientRoleException,
    NotAuthenticatedException,
    PasswordChangeRequiredException,
    ProfileSetupRequiredException,
    get_current_user,
    hash_password,
)
from src.config import settings
from src.database import SessionLocal
from src.edge import EdgeMiddleware
from src.models import BusinessUnit, User
from src.routers import accounting, admin, auth, ceo, ho, modules, ops, users

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
    db.commit()


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


def seed_users(db) -> None:
    # Seeded on first boot only (_ensure_user is a no-op once the account
    # exists) with a known default password, so every seeded account —
    # including the admin-tier ones — must rotate it on first login.
    #
    # In production this can't use a hardcoded, source-visible password:
    # anyone who's read this file could log in as e.g. ceo_bu14 the moment
    # a fresh prod database comes up, before its real owner ever has. So a
    # real (ENVIRONMENT=production) deploy only bootstraps the one Chairman
    # account — with the password from BOOTSTRAP_CHAIRMAN_PASSWORD when that
    # secret is configured (never logged), otherwise a random one logged once
    # so the deployer can retrieve it from the deploy logs — and stops there;
    # every other account is created afterward through Manage Users
    # (Chairman-only), which already hands out its own one-time temporary
    # password per account instead of a fixed, publicly-known string.
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
                    logger.warning(
                        "First boot: seeded the 'chairman' account with a random temporary "
                        "password — record it now, it will not be shown again: %s",
                        bootstrap_password,
                    )
        return

    _ensure_user(db, "chairman", "Chairman123!", "Chairman", True, full_name="Chairman")
    _ensure_user(db, "ho_staff", "HOStaff123!", "HO", True, full_name="Head Office Staff")

    business_units = {bu.code: bu.id for bu in db.query(BusinessUnit).all()}
    for i in range(1, settings.TOTAL_BUSINESS_UNITS + 1):
        code = f"BU{i}"
        _ensure_user(
            db,
            f"ceo_bu{i}",
            "BUCeo2026!",
            "CEO",
            True,
            business_unit_id=business_units.get(code),
            full_name=f"CEO - Business Unit {i}",
        )
    db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.RUN_MIGRATIONS_ON_STARTUP:
        run_migrations()
    else:
        logger.info(
            "RUN_MIGRATIONS_ON_STARTUP is false: schema migrations are expected to have "
            "been applied already by a release job."
        )
    db = SessionLocal()
    try:
        seed_business_units(db)
        seed_users(db)
    finally:
        db.close()
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
app.include_router(accounting.router)
app.include_router(modules.router)


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
