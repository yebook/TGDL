"""Public API DTOs. These models are the OpenAPI source for WebUI types."""
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.contracts import UserSettings

TaskStatus = Literal[
    "collecting", "awaiting_confirmation", "expired", "queued", "fetching", "saving", "cancelling",
    "retry_wait", "recovery_blocked", "succeeded", "already_exists", "partial_success", "failed", "cancelled",
]


class SetupStatus(BaseModel):
    initialized: bool


class CurrentUser(BaseModel):
    username: str
    csrf_token: str


class ProxyStatus(BaseModel):
    state: Literal["disabled", "pending", "applying", "active", "failed", "restart_required"]
    desired_revision: int
    applied_revision: int | None
    message: str


class SettingsResponse(BaseModel):
    revision: int
    values: UserSettings
    sources: dict[str, Literal["default", "database", "environment", "deployment_fallback"]]
    editable: dict[str, bool]
    configured: dict[str, bool]
    proxy_status: ProxyStatus


class TaskSummary(BaseModel):
    id: str
    title: str
    kind: Literal["single", "group"]
    status: TaskStatus
    created_at: str
    updated_at: str
    item_count: int
    completed_count: int
    total_bytes: int | None
    completed_bytes: int
    effective_destination: str
    notification_status: Literal["sent", "pending", "failed"]
    can_cancel: bool
    can_retry: bool
    can_delete: bool


class TaskDeleteResponse(BaseModel):
    deleted_ids: list[str]


class TaskPage(BaseModel):
    items: list[TaskSummary]
    total: int
    page: int
    page_size: int


class AttemptResponse(BaseModel):
    sequence: int
    started_at: str
    finished_at: str | None
    error_code: str | None


class MediaResponse(BaseModel):
    id: str
    job_id: str | None
    media_type: Literal["video", "image"]
    original_name: str | None
    size_bytes: int | None
    duration_seconds: int | None
    status: str
    phase: str
    completed_bytes: int
    total_bytes: int | None
    speed_bps: float | None
    elapsed_seconds: float
    relative_path: str | None
    error_code: str | None
    error_message: str | None
    linked_job_id: str | None
    attempts: list[AttemptResponse]


class TaskDetail(TaskSummary):
    items: list[MediaResponse]
    expires_at: str | None


class BotStatus(BaseModel):
    state: Literal["unconfigured", "connecting", "online", "error"]
    username: str
    message: str


class RootStatus(BaseModel):
    id: str
    label: str
    available: bool
    free_bytes: int | None
    total_bytes: int | None
    message: str


class CacheStatus(BaseModel):
    free_bytes: int | None
    total_bytes: int | None


class StorageStatus(BaseModel):
    roots: list[RootStatus]
    cache: CacheStatus


class QueueStatus(BaseModel):
    active: int
    pending: int
    concurrency: int


class RecoveryStatus(BaseModel):
    blocked_jobs: int
    message: str


class LocalApiStatus(BaseModel):
    managed: bool
    state: Literal["unavailable", "waiting_credentials", "stopped", "starting", "running", "stopping", "failed"]
    desired_mode: Literal["cloud", "local"]
    applied_mode: Literal["cloud", "local"] | None
    operation_id: str | None
    phase: Literal["idle", "draining", "logging_out", "starting", "verifying", "cooldown", "blocked", "logout_uncertain", "failed"]
    message: str
    generation: str | None
    last_heartbeat_at: str | None
    cloud_available_at: str | None
    can_restart: bool
    can_retry: bool


class SystemStatus(BaseModel):
    version: str
    bot: BotStatus
    storage: StorageStatus
    proxy: ProxyStatus
    queue: QueueStatus
    recovery: RecoveryStatus
    local_api: LocalApiStatus


class BotInfo(BaseModel):
    id: int
    username: str


class ValidationResponse(BaseModel):
    valid: bool
    warnings: list[str]
    bot: BotInfo | None


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str
    details: Any = None


class ErrorResponse(BaseModel):
    error: ErrorBody = Field(description="Safe error data; never contains credentials or raw paths")
