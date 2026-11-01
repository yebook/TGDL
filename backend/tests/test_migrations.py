from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config as AlembicConfig
from alembic.migration import MigrationContext
from alembic.util import CommandError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.models import Base, Job, MediaItem, MediaRequest, Settings, StoredFile


def migration_config() -> AlembicConfig:
    return AlembicConfig(str(Path(__file__).resolve().parents[1] / "alembic.ini"))


def test_initial_schema_matches_models_and_repeated_startup_is_idempotent(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    try:
        with engine.begin() as connection:
            migration = migration_config()
            migration.attributes["connection"] = connection
            command.upgrade(migration, "head")
            assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == "saved_media_deletion"
            assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
            connection.exec_driver_sql("INSERT INTO settings VALUES (1, 8, 1, '{}')")
            command.upgrade(migration, "head")
            assert connection.exec_driver_sql("SELECT revision FROM settings").scalar() == 8
            assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    finally:
        engine.dispose()


def test_current_initial_database_upgrades_without_changing_history_or_media(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'existing.db'}")
    media = tmp_path / "video.mp4"
    media.write_bytes(b"preserved actual media")
    try:
        with engine.begin() as connection:
            migration = migration_config()
            migration.attributes["connection"] = connection
            command.upgrade(migration, "initial")
            with Session(bind=connection) as session:
                session.add(Settings(id=1, revision=17, values={"download_root_id": "default"}))
                request = MediaRequest(bot_id=7, chat_id=42, user_id=42, status="succeeded", collect_until=0)
                session.add(request)
                session.flush()
                item = MediaItem(request_id=request.id, bot_id=7, chat_id=42, message_id=1,
                                 file_id="retained-file", media_type="video")
                session.add(item)
                session.flush()
                job = Job(request_id=request.id, item_id=item.id, state="succeeded", relative_path="video.mp4")
                session.add(job)
                session.flush()
                session.add(StoredFile(bot_id=7, file_unique_id="retained-unique", job_id=job.id, root_id="default",
                                       relative_path="video.mp4", size_bytes=len(media.read_bytes()), sha256="retained-hash"))
                session.commit()
            tables = ["settings", "requests", "media_items", "jobs", "stored_files"]
            before = {table: connection.exec_driver_sql(f"SELECT * FROM {table}").all() for table in tables}
            command.upgrade(migration, "head")
            assert {table: connection.exec_driver_sql(f"SELECT * FROM {table}").all() for table in tables} == before
            assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == "saved_media_deletion"
            assert connection.exec_driver_sql("SELECT COUNT(*) FROM file_deletion_operations").scalar() == 0
            assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
            assert media.read_bytes() == b"preserved actual media"
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
