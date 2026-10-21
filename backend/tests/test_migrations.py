from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config as AlembicConfig
from alembic.migration import MigrationContext
from alembic.util import CommandError
from sqlalchemy import create_engine

from app.db.models import Base


def migration_config() -> AlembicConfig:
    return AlembicConfig(str(Path(__file__).resolve().parents[1] / "alembic.ini"))


def test_initial_schema_matches_models_and_repeated_startup_is_idempotent(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    try:
        with engine.begin() as connection:
            migration = migration_config()
            migration.attributes["connection"] = connection
            command.upgrade(migration, "head")
            assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == "initial"
            assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
            connection.exec_driver_sql("INSERT INTO settings VALUES (1, 8, 1, '{}')")
            command.upgrade(migration, "head")
            assert connection.exec_driver_sql("SELECT revision FROM settings").scalar() == 8
            assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    finally:
        engine.dispose()


def test_unpublished_schema_is_rejected_without_deleting_data(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'unpublished.db'}")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
            connection.exec_driver_sql("INSERT INTO alembic_version VALUES ('0007')")
            connection.exec_driver_sql("CREATE TABLE preserved_test_state (value INTEGER NOT NULL)")
            connection.exec_driver_sql("INSERT INTO preserved_test_state VALUES (42)")
            migration = migration_config()
            migration.attributes["connection"] = connection
            with pytest.raises(CommandError, match="0007"):
                command.upgrade(migration, "head")
            assert connection.exec_driver_sql("SELECT value FROM preserved_test_state").scalar() == 42
            assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == "0007"
    finally:
        engine.dispose()
