"""Alembic env: versions app tables only, framework tables stay excluded."""

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core.config import get_settings  # noqa: E402
from models import Base  # noqa: E402
from services.database import alembic_include_object, normalize_postgres_uri  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

postgres_uri = normalize_postgres_uri(get_settings().postgres_uri)
if not postgres_uri:
    raise RuntimeError("POSTGRES_URI must be set to run migrations")
# Sync migrations run on psycopg3, whose SQLAlchemy dialect needs the explicit prefix.
config.set_main_option(
    "sqlalchemy.url", postgres_uri.replace("postgresql://", "postgresql+psycopg://")
)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations without a live connection."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        include_object=alembic_include_object,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against the configured database."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=alembic_include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
