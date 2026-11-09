import time

from fastapi import HTTPException
from sqlalchemy import MetaData, create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError, TimeoutError as SATimeoutError
from sqlalchemy.orm import declarative_base, sessionmaker

from src.config import settings

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

if settings.DATABASE_URL.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    _url = make_url(settings.DATABASE_URL)
    _host, _port = _url.host or "", _url.port
    _connect_args = {
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
        _connect_args["options"] = (
            "-c statement_timeout=15000 -c lock_timeout=5000 -c idle_in_transaction_session_timeout=60000"
        )
    _engine_kwargs.update(
        connect_args=_connect_args,
        # A managed database allows a small fixed number of connections (about
        # 22 on the smallest DigitalOcean cluster) and a rolling deploy briefly
        # runs two instances, so keep each well under half of that.
        pool_size=4,
        max_overflow=4,
        pool_timeout=10,
        pool_recycle=1800,
        pool_use_lifo=True,
    )

engine = create_engine(settings.DATABASE_URL, **_engine_kwargs)

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
    surfacing as a generic 500."""
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
