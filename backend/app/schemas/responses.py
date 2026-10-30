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
    state: Literal["disabled", "pending", "applying", "active", "failed", "unavailable"]
    desired_revision: int
    applied_revision: int | None
    message: str


class SettingsResponse(BaseModel):
    revision: int
    values: UserSettings
    sources: dict[str, Literal["default", "database", "environment"]]
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


class DownloadProgressResponse(BaseModel):
    downloaded_bytes: int
    total_bytes: int | None
    total_kind: Literal["exact", "estimated", "unknown"]
    speed_bps: float | None
    active: bool
    completed: bool


class MediaResponse(BaseModel):
    id: str
    job_id: str | None
    media_type: Literal["video", "image"]
    original_name: str | None
    size_bytes: int | None
    duration_seconds: int | None
    status: str
    phase: str
    acquisition_mode: Literal["cloud", "local"] | None
    download_progress: DownloadProgressResponse | None = None
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


class RequestSafetyStatus(BaseModel):
    state: Literal["ready", "throttled", "backoff", "auth_paused"]
    retry_at: str | None
    message: str
    can_retry: bool


class BotStatus(BaseModel):
    state: Literal["unconfigured", "connecting", "online", "error"]
    username: str
    message: str
    request_safety: RequestSafetyStatus


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
    selected_root_id: str
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
    drain_reason: Literal["connection", "downloads"] | None
    message: str
    generation: str | None
    last_heartbeat_at: str | None
    cloud_available_at: str | None
    can_restart: bool
    can_retry: bool


class NetworkProbeResult(BaseModel):
    state: Literal["reachable", "failed", "timeout", "not_run", "not_applicable"]
    latency_ms: int | None = None
    http_status: int | None = None
    failure_kind: Literal["dns", "proxy_connection", "proxy_auth", "tcp", "tls", "timeout", "network_unknown"] | None = None
    failure_stage: Literal["dns", "proxy", "connect", "tls", "response", "unknown"] | None = None


class NetworkTestResponse(BaseModel):
    checked_at: str
    settings_revision: int
    origin: Literal["app"] = "app"
    route: Literal["direct", "proxy"]
    proxy_scheme: Literal["http", "socks5"] | None
    bot_api_mode: Literal["cloud", "local"]
    telegram: NetworkProbeResult
    google: NetworkProbeResult
    local_api_tcp: NetworkProbeResult
    local_api: LocalApiStatus | None
    proxy_status: ProxyStatus
    local_api_upstream: Literal["unverified", "not_applicable"]
    next_allowed_at: str


class DiagnosticLogStatus(BaseModel):
    state: Literal["ready", "error"]
    message: str
    max_file_bytes: int
    backup_count: int
    retention_days: int
    cleanup_time: str
    timezone: str
    total_bytes: int
    file_count: int
    last_cleanup_at: str | None
    next_cleanup_at: str | None


class SystemStatus(BaseModel):
    diagnostic_log: DiagnosticLogStatus | None = None
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
