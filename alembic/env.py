from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool, text

from alembic import context

from src.config import settings
from src.database import Base
from src import models  # noqa: F401 - registers all model classes on Base.metadata

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

# Migrations run as MIGRATION_DATABASE_URL (a schema-owner role) when it is set,
# so the web process can hold a least-privilege runtime role. `%` must be
# doubled: this value goes through ConfigParser interpolation, and an
# unescaped `%` in a URL-encoded password makes set_main_option raise.
config.set_main_option("sqlalchemy.url", settings.MIGRATION_DATABASE_URL.replace("%", "%%"))

target_metadata = Base.metadata

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

    Calls to context.execute() here emit the given string to the
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
