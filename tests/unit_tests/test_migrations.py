import importlib.util
from pathlib import Path
from types import ModuleType

from alembic.config import Config
from sqlalchemy import create_engine, inspect


def _load_env() -> ModuleType:
    env_path = Path("alembic/env.py")
    spec = importlib.util.spec_from_file_location("app_alembic_env", env_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_env = _load_env()
EXCLUDED_TABLES = _env.EXCLUDED_TABLES
include_object = _env.include_object


def _sqlite_config(path: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    return cfg


def test_include_object_excludes_loop_and_memory_tables() -> None:
    for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
        assert table in EXCLUDED_TABLES
        assert include_object(None, table, "table", True, None) is False
    assert include_object(None, "memory_embeddings", "table", True, None) is False
    assert include_object(None, "users", "table", True, None) is True
    assert include_object(None, "sessions", "table", True, None) is True


def test_upgrade_creates_only_app_tables(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from alembic import command

    db_file = tmp_path / "migration-test.db"
    command.upgrade(_sqlite_config(str(db_file)), "head")
    tables = set(inspect(create_engine(f"sqlite:///{db_file}")).get_table_names())
    assert {"users", "sessions", "alembic_version"} <= tables
    assert tables.isdisjoint({"checkpoints", "checkpoint_blobs", "checkpoint_writes"})


def test_downgrade_removes_app_tables(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from alembic import command

    db_file = tmp_path / "migration-rollback.db"
    cfg = _sqlite_config(str(db_file))
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "-1")
    tables = set(inspect(create_engine(f"sqlite:///{db_file}")).get_table_names())
    assert tables.isdisjoint({"users", "sessions"})
