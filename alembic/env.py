import logging
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool, text

from alembic import context

from src.config import migration_database_url

logger = logging.getLogger("alembic.env")

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
#
# disable_existing_loggers=False is load-bearing, not boilerplate: this env.py
# runs *inside* the app process (main.py's run_migrations(), during startup),
# not as a standalone CLI. fileConfig's default (True) silently disables every
# logger that already exists at that point — uvicorn's, and the
# "business_ops_tracker.seed" logger that announces the one-time production
# chairman bootstrap password. With them disabled, that password was generated
# and stored but never shown anywhere, locking every fresh production deploy
# out of its only admin account.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# C-1 (PM review 2026-11-18): migrations need ONLY the database URL.
# This used to import the full app Settings(), so the compose `migrate`
# service and the DO PRE_DEPLOY job had to carry JWT_SECRET_KEY,
# DATA_ENCRYPTION_KEY and ALLOWED_* just to run `alembic upgrade head` —
# and neither deploy path booted without them. `migration_database_url()`
# reads only the DB env vars (MIGRATION_DATABASE_URL, else DATABASE_URL)
# plus the DB TLS vars, and never constructs Settings().
#
# Migrations run as MIGRATION_DATABASE_URL (a schema-owner role) when it is set,
# so the web process can hold a least-privilege runtime role. `%` must be
# doubled: this value goes through ConfigParser interpolation, and an
# unescaped `%` in a URL-encoded password makes set_main_option raise.
config.set_main_option("sqlalchemy.url", migration_database_url().replace("%", "%%"))


def _load_target_metadata():
    """Model metadata for autogenerate, best-effort.

    Importing `src.models` pulls in the full app config (crypto/database),
    which a migrate-only environment deliberately does not have — that is
    the C-1 fix, not a failure. `alembic upgrade/downgrade` does not use
    the model tables at all, so a migrate job runs fine with a fallback;
    only `revision --autogenerate` needs the real metadata, and that runs
    from a dev checkout with the full env, where this import succeeds.
    The fallback is an empty MetaData carrying the SAME naming convention
    as `src.database.NAMING_CONVENTION` (duplicated here on purpose —
    importing `src.database` would pull in the full Settings() this fix
    removes): batch-mode migrations name unnamed FKs from it, and without
    it `upgrade head` fails on SQLite with "Constraint must have a name"
    (c9ec96d6b1e5). Keep the two in sync. For an autogenerate run the
    fallback would silently diff an empty model, so that command re-raises
    the original import error instead of proceeding.
    """
    try:
        from src import models  # noqa: F401 - registers all model classes on Base.metadata
        from src.database import Base

        return Base.metadata
    except Exception as exc:  # noqa: BLE001 - migrate-only envs lack the full app config by design
        cmd = getattr(config, "cmd_opts", None)
        cmd_name = getattr(cmd, "cmd", [None])[0] if cmd is not None else None
        if cmd_name == "revision":
            raise
        logger.warning(
            "alembic env: model metadata unavailable (%s) — running migrations "
            "with a naming-convention-only metadata. upgrade/downgrade are "
            "unaffected; autogenerate comparison needs a checkout with the "
            "full app environment.",
            exc,
        )
        from sqlalchemy import MetaData

        return MetaData(
            naming_convention={
                "ix": "ix_%(column_0_label)s",
                "uq": "uq_%(table_name)s_%(column_0_name)s",
                "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            }
        )


target_metadata = _load_target_metadata()

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, compare_type=True
        )

        with context.begin_transaction():
            if connection.dialect.name == "postgresql":
                # Serialise concurrent upgraders (two instances booting during a
                # rolling deploy, or a deploy job racing a web start): the second
                # waits here, then finds the schema already current. Transaction-
                # scoped, so it releases on commit/rollback with nothing to clean up.
                # lock_timeout makes a migration that needs a table lock fail fast
                # if a long-running query is holding it, instead of queueing
                # behind that query and blocking every request queued behind
                # the migration.
                connection.execute(text("SET LOCAL lock_timeout = '10s'"))
                connection.execute(text("SELECT pg_advisory_xact_lock(727272)"))
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
