"""Alembic environment: owns app tables only, never loop or memory tables."""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from src.models import Base

# Loop persistence tables are managed by LangGraph's saver setup, never Alembic.
# Memory vector tables are managed by the memory layer, never Alembic.
EXCLUDED_TABLES = frozenset(
    {
        "checkpoints",
        "checkpoint_blobs",
        "checkpoint_writes",
        "memory_embeddings",
    }
)


def include_object(
    object: object,  # noqa: ARG001
    name: str | None,
    type_: str | None,
    reflected: bool,  # noqa: ARG001
    compare_to: object | None,  # noqa: ARG001
) -> bool:
    """Keep autogenerate and upgrades away from externally owned tables."""
    if type_ == "table" and name is not None:
        if name in EXCLUDED_TABLES:
            return False
        if name.startswith("memory_") or name.startswith("embedding"):
            return False
    return True


def resolve_url(configured_url: str, env_url: str = "") -> str:
    """Pick the migration database URL, preferring the explicit config value."""
    configured = configured_url.strip()
    if configured and "://" in configured and not configured.startswith("driver://"):
        return configured
    candidate = env_url or os.environ.get("POSTGRES_URI", "").strip()
    if candidate.startswith("postgresql://"):
        return candidate.replace("postgresql://", "postgresql+psycopg://", 1)
    if candidate:
        return candidate
    return "postgresql+psycopg://postgres:postgres@localhost:5432/postgres"


target_metadata = Base.metadata


def run_migrations_offline(url: str) -> None:
    """Run migrations without an engine connection."""
    context.configure(
        url=url,
        target_metadata=target_metadata,
        include_object=include_object,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online(url: str, section: dict[str, str]) -> None:
    """Run migrations against the configured database."""
    configuration = dict(section)
    configuration["sqlalchemy.url"] = url
    engine = engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                include_object=include_object,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


def run_env() -> None:
    """Entry point used when Alembic executes this file as its environment."""
    config = context.config
    if config.config_file_name is not None:
        # Never silence app loggers: migrations share the process with tests.
        fileConfig(config.config_file_name, disable_existing_loggers=False)
    url = resolve_url(config.get_main_option("sqlalchemy.url") or "")
    if context.is_offline_mode():
        run_migrations_offline(url)
    else:
        run_migrations_online(url, config.get_section(config.config_ini_section, {}))


try:
    _HAS_RUNTIME = context.config is not None
except AttributeError:
    # Imported outside an Alembic run (unit tests): expose helpers only.
    _HAS_RUNTIME = False

if _HAS_RUNTIME:
    run_env()
