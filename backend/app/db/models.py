"""SQLAlchemy owns persisted contracts; epoch seconds are always UTC."""
import time
import uuid
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def uid() -> str:
    return uuid.uuid4().hex[:20]


class Base(DeclarativeBase):
    pass


class Settings(Base):
    __tablename__ = "settings"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    revision: Mapped[int] = mapped_column(default=1)
    schema_version: Mapped[int] = mapped_column(default=1)
    values: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class LocalApiRuntime(Base):
    __tablename__ = "local_api_runtime"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    values: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Admin(Base):
    __tablename__ = "admins"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str]


class Session(Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(primary_key=True)
    csrf_token: Mapped[str]
    expires_at: Mapped[float]


class BotIdentity(Base):
    __tablename__ = "bot_identities"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(default="")


class PollCursor(Base):
    __tablename__ = "poll_cursor"
    bot_id: Mapped[int] = mapped_column(primary_key=True)
    next_offset: Mapped[int] = mapped_column(default=0)


class IngestUpdate(Base):
    __tablename__ = "ingest_updates"
    __table_args__ = (UniqueConstraint("bot_id", "update_id"),)
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    bot_id: Mapped[int]
    update_id: Mapped[int]
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    processed: Mapped[bool] = mapped_column(default=False)


class MediaRequest(Base):
    __tablename__ = "requests"
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    bot_id: Mapped[int]
    chat_id: Mapped[int]
    user_id: Mapped[int]
    kind: Mapped[str] = mapped_column(default="single")
    group_key: Mapped[str | None]
    title: Mapped[str] = mapped_column(default="媒体下载")
    status: Mapped[str] = mapped_column(default="collecting")
    created_at: Mapped[float] = mapped_column(default=time.time)
    updated_at: Mapped[float] = mapped_column(default=time.time)
    deleted_at: Mapped[float | None]
    collect_until: Mapped[float]
    nonce: Mapped[str] = mapped_column(default=lambda: uuid.uuid4().hex[:10])
    expires_at: Mapped[float | None]
    card_message_id: Mapped[int | None]
    config_revision: Mapped[int] = mapped_column(default=1)
    destination: Mapped[str] = mapped_column(default="")
    root_id: Mapped[str] = mapped_column(default="default")
    result_revision: Mapped[int] = mapped_column(default=0)


class MediaItem(Base):
    __tablename__ = "media_items"
    __table_args__ = (UniqueConstraint("bot_id", "chat_id", "message_id"),)
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    request_id: Mapped[str] = mapped_column(ForeignKey("requests.id"))
    bot_id: Mapped[int]
    chat_id: Mapped[int]
    message_id: Mapped[int]
    file_id: Mapped[str]
    file_unique_id: Mapped[str | None]
    media_type: Mapped[str]
    planned_filename: Mapped[str | None]
    original_name: Mapped[str | None]
    caption_snippet: Mapped[str | None] = mapped_column(String(80))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    duration_seconds: Mapped[int | None]


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    item_id: Mapped[str] = mapped_column(ForeignKey("media_items.id"), unique=True)
    request_id: Mapped[str] = mapped_column(ForeignKey("requests.id"), index=True)
    state: Mapped[str] = mapped_column(default="queued", index=True)
    phase: Mapped[str] = mapped_column(default="queued")
    acquisition_mode: Mapped[str] = mapped_column(default="local", server_default="local")
    root_id: Mapped[str] = mapped_column(default="default")
    relative_path: Mapped[str]
    attempt_sequence: Mapped[int] = mapped_column(default=0)
    auto_retries: Mapped[int] = mapped_column(default=0)
    max_auto_retries: Mapped[int] = mapped_column(default=3)
    next_run_at: Mapped[float] = mapped_column(default=time.time)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    boot_id: Mapped[str | None]
    completed_bytes: Mapped[int] = mapped_column(default=0)
    speed_bps: Mapped[float | None]
    started_at: Mapped[float | None]
    finished_at: Mapped[float | None]
    error_code: Mapped[str | None]
    error_message: Mapped[str | None]
    linked_job_id: Mapped[str | None]


class Attempt(Base):
    __tablename__ = "attempts"
    __table_args__ = (UniqueConstraint("job_id", "sequence"),)
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"))
    sequence: Mapped[int]
    started_at: Mapped[float] = mapped_column(default=time.time)
    finished_at: Mapped[float | None]
    error_code: Mapped[str | None]


class FileReservation(Base):
    __tablename__ = "file_reservations"
    __table_args__ = (UniqueConstraint("bot_id", "file_unique_id"),)
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    bot_id: Mapped[int]
    file_unique_id: Mapped[str]
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), unique=True)


class StoredFile(Base):
    __tablename__ = "stored_files"
    __table_args__ = (UniqueConstraint("bot_id", "file_unique_id"),)
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    bot_id: Mapped[int]
    file_unique_id: Mapped[str | None]
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"))
    root_id: Mapped[str]
    relative_path: Mapped[str]
    size_bytes: Mapped[int]
    sha256: Mapped[str]
    saved_at: Mapped[float] = mapped_column(default=time.time)


class PublishJournal(Base):
    __tablename__ = "publish_journal"
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), primary_key=True)
    temporary_path: Mapped[str]
    final_path: Mapped[str]
    stage: Mapped[str] = mapped_column(default="copying")
    size_bytes: Mapped[int | None]
    sha256: Mapped[str | None]


class Notification(Base):
    __tablename__ = "notification_outbox"
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    event_key: Mapped[str] = mapped_column(unique=True)
    request_id: Mapped[str | None] = mapped_column(ForeignKey("requests.id"))
    chat_id: Mapped[int]
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(default="pending")
    attempts: Mapped[int] = mapped_column(default=0)
    next_run_at: Mapped[float] = mapped_column(default=time.time)
    message_id: Mapped[int | None]
