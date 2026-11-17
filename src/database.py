import logging
import time

from fastapi import HTTPException
from sqlalchemy import MetaData, create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError, TimeoutError as SATimeoutError
from sqlalchemy.orm import declarative_base, sessionmaker

from src.config import settings

_db_logger = logging.getLogger("business_ops_tracker.database")

# Deterministic names for unnamed indexes / unique constraints / foreign keys:
# without this, Postgres invents them, and a later migration has to guess (or
# introspect) what to drop. Existing objects keep whatever name they already
# have. CHECK constraints are always named explicitly (ck_<table>_<rule>), so
# there is deliberately no "ck" pattern here — it would double-prefix them.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}

# hide_parameters: SQLAlchemy error messages otherwise include the bound
# parameter values of the failing statement — i.e. phone numbers, addresses,
# vitals, password hashes — and those end up in the platform's log stream.
_engine_kwargs = {"pool_pre_ping": True, "hide_parameters": True}

# The Postgres connect args, shared by the main engine and the /readyz
# probe engine (m-16) below. Empty for SQLite.
_postgres_connect_args: dict = {}

if settings.DATABASE_URL.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    _url = make_url(settings.DATABASE_URL)
    _host, _port = _url.host or "", _url.port
    _postgres_connect_args = {
        "connect_timeout": 10,
        "application_name": "business-ops-web",
        # Detect a dead connection through the provider's proxy in seconds
        # rather than waiting on the OS default (~2 hours).
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 3,
    }
    # Server-side limits so one runaway query or a forgotten open transaction
    # can't hold locks (and the small connection pool) hostage. Connection
    # poolers in transaction mode (Neon "-pooler" hosts, DigitalOcean's pool
    # port 25061) reject startup `options`; there the same limits should be set
    # per role instead (ALTER ROLE ... SET, see deploy/db_roles.sql).
    if "-pooler" not in _host and _port != 25061:
        _postgres_connect_args["options"] = (
            "-c statement_timeout=15000 -c lock_timeout=5000 -c idle_in_transaction_session_timeout=60000"
        )
    _engine_kwargs.update(
        connect_args=_postgres_connect_args,
        # A managed database allows a small fixed number of connections (about
        # 22 on the smallest DigitalOcean cluster) and a rolling deploy briefly
        # runs two instances, so keep each well under half of that.
        # PM-5/SM-11: 5+5 = 10 per instance (was 4+4 = 8). The raise is
        # deliberately modest — two instances still peak at 20 of ~22 —
        # because the real fix is PM-1: reports now aggregate in SQL and
        # hold a connection for milliseconds instead of seconds. The
        # audit row still takes a second pooled connection per audited
        # request (src/utils.py _audit_session, outside this workstream's
        # files); batching audit writes onto the caller's connection or a
        # dedicated audit pool is a follow-up, not done here.
        pool_size=5,
        max_overflow=5,
        pool_timeout=10,
        pool_recycle=1800,
        pool_use_lifo=True,
    )

engine = create_engine(settings.DATABASE_URL, **_engine_kwargs)


def build_probe_engine(url: str, connect_args: dict | None = None):
    """The /readyz probe's own engine (m-16): pool_size=1, max_overflow=0,
    pool_timeout=3, so a probe checkout can never eat the main pool and can
    never linger past the probe's own 3 s join. SQLite needs no pool
    arguments (its default pool ignores them / rejects them for :memory:)."""
    kwargs: dict = {"pool_pre_ping": True, "hide_parameters": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs.update(
            connect_args=connect_args or {},
            pool_size=1,
            max_overflow=0,
            pool_timeout=3,
        )
    return create_engine(url, **kwargs)


# The probe engine is a separate Engine (its own 1-connection pool), never
# the main engine — _db_is_ready in src/main.py uses this one.
probe_engine = build_probe_engine(settings.DATABASE_URL, _postgres_connect_args or None)


# ---------------------------------------------------------------- M-09
# A Neon "-pooler" host (or port 25061) silently drops the statement /
# lock timeouts above — the pooler rejects startup `options`, and the
# fallback is per-role ALTER ROLE in deploy/db_roles.sql, which is a
# manual, not-yet-applied go-live step. Startup must not fail over this
# (the pooler URL is what Neon shows by default, and the deployer may
# legitimately use it), but it must warn loudly when the role timeouts
# cannot be verified as set.

def is_pooler_database_url(url: str) -> bool:
    """True when the URL points at a transaction-mode pooler, where the
    startup `options` carrying the query timeouts are not sent."""
    try:
        parsed = make_url(url)
    except Exception:  # noqa: BLE001 - an unparsable URL is not a pooler URL
        return False
    return "-pooler" in (parsed.host or "") or parsed.port == 25061


def check_pooler_timeouts(connection) -> list[str]:
    """Asks a live connection for its effective statement_timeout /
    lock_timeout and returns the names that are unset (Postgres reports
    an unset timeout as '0'). Logs a prominent WARNING naming them, with
    the fix (apply deploy/db_roles.sql, or prefer the direct Neon host —
    see .env.example). `connection` only needs .execute(text(...)).scalar()."""
    missing: list[str] = []
    for name in ("statement_timeout", "lock_timeout"):
        try:
            value = connection.execute(text(f"SHOW {name}")).scalar()
        except Exception as exc:  # noqa: BLE001 - a failed SHOW is "cannot verify"
            _db_logger.warning("could not verify %s on a pooler connection: %s", name, exc)
            missing.append(name)
            continue
        if value is None or str(value).strip() in ("", "0"):
            missing.append(name)
    if missing:
        _db_logger.warning(
            "Neon pooler DATABASE_URL in use and the role timeouts are NOT set (%s): "
            "a runaway query has no statement/lock timeout and can hold a pooled "
            "connection indefinitely. Apply deploy/db_roles.sql (ALTER ROLE ... SET) "
            "or switch DATABASE_URL to the direct Neon host, which sends the "
            "timeouts at connect time.",
            ", ".join(missing),
        )
    return missing


def verify_pooler_timeouts(db_engine=None) -> list[str]:
    """Startup check (called from main.py's lifespan once the DB is up):
    on a pooler URL, opens one connection and verifies the effective
    timeouts. Returns the missing names (empty when fine / not a pooler
    URL). Never raises — an unverifiable check is a warning, not a reason
    to refuse startup."""
    eng = db_engine if db_engine is not None else engine
    if not is_pooler_database_url(settings.DATABASE_URL):
        return []
    try:
        with eng.connect() as conn:
            return check_pooler_timeouts(conn)
    except Exception as exc:  # noqa: BLE001 - see above: warn, don't fail boot
        _db_logger.warning("could not verify pooler timeouts at startup: %s", exc)
        return ["statement_timeout", "lock_timeout"]

if engine.dialect.name == "sqlite":

    @event.listens_for(engine, "connect")
    def _sqlite_enforce_foreign_keys(dbapi_connection, _record):
        # SQLite ignores FOREIGN KEY constraints unless asked, so local
        # development would otherwise silently accept orphan rows that
        # Postgres in production rejects.
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base(metadata=MetaData(naming_convention=NAMING_CONVENTION))


def get_db():
    db = _new_validated_session()
    try:
        yield db
    except Exception as exc:
        # A transient DB failure mid-request cannot be retried here — the
        # endpoint already ran and only it knows what to redo — so the
        # session is rolled back and the failure surfaces as a distinct
        # 503 "database unavailable" instead of the generic 500 the edge
        # middleware would otherwise produce. The retry (above) covers
        # session acquisition; this covers the failure's shape.
        if is_transient_db_error(exc):
            try:
                db.rollback()
            except Exception:
                pass
            raise DatabaseUnavailableError() from exc
        raise
    finally:
        db.close()


class DatabaseUnavailableError(HTTPException):
    """The database could not be reached (transient failure, pool
    exhaustion, or a dead connection). 503, not 500, so callers and
    operators can tell "the database is down" from "the app has a bug"."""

    def __init__(self, detail: str = "Database unavailable — please retry shortly"):
        super().__init__(status_code=503, detail=detail)


# Pause between the failed session-acquisition attempt and the one retry
# (D-3). A module constant so tests can shrink it to zero.
DB_RETRY_BACKOFF_SECONDS = 0.5


def is_transient_db_error(exc: BaseException) -> bool:
    """True for the DB failures worth one retry / a 503: lost connections,
    pool timeouts, and other operational errors. Deliberately NOT every
    DBAPIError — IntegrityError, ProgrammingError and DataError are
    application bugs or bad input, and retrying them (or calling them a
    503) would hide the real problem."""
    if isinstance(exc, (OperationalError, InterfaceError, SATimeoutError, TimeoutError, ConnectionError)):
        return True
    if isinstance(exc, DBAPIError):
        return isinstance(exc.orig, (OSError, TimeoutError, ConnectionError))
    return False


def _new_validated_session():
    """Opens a session and proves the pooled connection is alive with a
    cheap SELECT 1. On a transient failure the dead session is discarded
    and one fresh session is tried; if that fails too, the request gets a
    503 "database unavailable" instead of waiting out a pool timeout and
    surfacing as a generic 500.

    PM-5 note: the explicit SELECT 1 stays even though pool_pre_ping
    also validates on checkout — it is the probe the one-retry path
    above keys off (D-3, pinned by TestGetDbRetry), and pre_ping alone
    would surface a dead connection as a failure at first use with no
    retry. One extra round-trip per request is the price of the retry.

    Expected month-end behaviour (PM-5/SM-11): with the pool at 10, an
    11th concurrent request waits up to pool_timeout (10 s) and then
    gets 503 "Database unavailable — please retry shortly". That is pool
    exhaustion, not Neon being down: reports are SQL-aggregated now, so
    a sustained burst of 503s means genuinely more than 10 concurrent
    DB sessions (month-end morning), and hammering retry/refresh only
    queues more waiters — wait a minute and retry once.
    """
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        return db
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        db.close()
        if not is_transient_db_error(exc):
            raise
        time.sleep(DB_RETRY_BACKOFF_SECONDS)
        retry_db = SessionLocal()
        try:
            retry_db.execute(text("SELECT 1"))
            return retry_db
        except Exception as retry_exc:
            try:
                retry_db.rollback()
            except Exception:
                pass
            retry_db.close()
            if is_transient_db_error(retry_exc):
                raise DatabaseUnavailableError() from retry_exc
            raise
