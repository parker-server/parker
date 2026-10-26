from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# 1. Import your App Configuration and Models
import os
import sys

# Add project root to python path so we can import 'app'
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.config import settings
from app.database import Base
from app.models import * # Import all models so Base.metadata finds them

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# 2. Overwrite the DB URL in alembic.ini with our App Settings
# This ensures we use the exact same DB path as the app
config.set_main_option("sqlalchemy.url", settings.database_url)

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 3. Set the Metadata target
target_metadata = Base.metadata


def include_name(name, type_, parent_names) -> bool:
    """Keep tables that have no model out of autogenerate and `alembic check`.

    comics_fts is an FTS5 virtual table (plus its comics_fts_* shadow tables) that
    migrations create with raw SQL. Without this, autogenerate would drop it.
    """
    if type_ == "table":
        return not name.startswith("comics_fts")
    return True


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        include_name=include_name,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # IMPORTANT for SQLite:
        render_as_batch=True
    )

    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_name=include_name,
            # IMPORTANT for SQLite:
            render_as_batch=True
        )

        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()