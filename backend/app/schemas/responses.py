"""Public API DTOs. These models are the OpenAPI source for WebUI types."""
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.core.file_types import MediaType
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


class LocalFiles(BaseModel):
    present: int = 0
    missing: int = 0
    changed: int = 0
    unavailable: int = 0
    not_saved: int = 0


class CancelCleanupEntry(BaseModel):
    kind: Literal["cache", "saved", "temporary"]
    path: str | None = None
    outcome: Literal["pending", "deleted", "missing", "retained_shared", "retained_unverified", "failed", "uncertain"]
    message: str | None = None


class CancelCleanupResponse(BaseModel):
    state: Literal["pending", "running", "partial", "complete"]
    message: str
    can_retry: bool
    entries: list[CancelCleanupEntry] = Field(default_factory=list)


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
    cancel_cleanup: CancelCleanupResponse | None = None
    local_files: LocalFiles = Field(default_factory=LocalFiles)


class FileDeleteResults(BaseModel):
    deleted: int = 0
    missing: int = 0
    retained_shared: int = 0
    retained_unverified: int = 0
    failed: int = 0
    uncertain: int = 0


class TaskDeleteResponse(BaseModel):
    deleted_ids: list[str]
    operation_id: str | None = None
    files: FileDeleteResults = Field(default_factory=FileDeleteResults)


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


class CacheOperationResponse(BaseModel):
    id: str
    kind: Literal["clean", "save", "download", "redownload"]
    state: Literal["queued", "running", "succeeded", "failed", "cancelled", "uncertain"]
    error_code: str | None = None
    message: str | None = None


class RetainedCacheResponse(BaseModel):
    id: str
    state: Literal["retained", "missing", "changed", "deleted", "uncertain"]
    completeness: Literal["complete", "partial", "unknown", "absent"]
    path: str | None
    size_bytes: int | None
    allocated_bytes: int | None
    can_clean: bool
    can_save: bool
    can_resume_download: bool
    can_redownload: bool
    unavailable_reason: str | None = None
    resume_unavailable_reason: str | None = None
    recovery_guidance: str | None = None
    operation: CacheOperationResponse | None = None


class SpaceVolumeResponse(BaseModel):
    location: Literal["target", "cache", "target+cache"]
    free_bytes: int | None
    required_bytes: int
    reserve_bytes: int
    concurrent_bytes: int
    missing_bytes: int | None
    error_code: str | None = None


class StorageFailureResponse(BaseModel):
    phase: Literal["before_download", "downloading", "before_save", "saving"]
    sufficient: bool
    volumes: list[SpaceVolumeResponse]


class MediaResponse(BaseModel):
    id: str
    job_id: str | None
    media_type: MediaType
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
    local_file_state: Literal["present", "missing", "changed", "unavailable", "not_saved"] = "not_saved"
    attempts: list[AttemptResponse]
    storage_failure: StorageFailureResponse | None = None
    cache: RetainedCacheResponse | None = None
    caches: list[RetainedCacheResponse] = Field(default_factory=list)


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


class CacheCleanupResult(BaseModel):
    before_bytes: int | None
    after_bytes: int | None
    released_bytes: int | None


class CacheMaintenanceStatus(BaseModel):
    state: Literal["idle", "running", "uncertain", "succeeded", "failed"]
    operation_id: str | None
    can_clean: bool
    blocked_reason: str | None
    message: str
    last_result: CacheCleanupResult | None


class CacheStatus(BaseModel):
    free_bytes: int | None
    total_bytes: int | None
    used_bytes: int | None
    file_count: int | None
    usage_state: Literal["ready", "refreshing", "stale", "unavailable", "unsupported"]
    measured_at: str | None
    refresh_error: str | None = None
    scope: Literal["current_bot_media"]
    maintenance: CacheMaintenanceStatus


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


class TelegramProtocolTarget(BaseModel):
    dc: int
    host: str
    port: int
    state: Literal["reachable", "failed", "timeout"]
    latency_ms: int | None = None
    failure_kind: Literal["dns", "proxy_connection", "proxy_auth", "tcp", "timeout", "protocol", "network_unknown"] | None = None


class TelegramProtocolResult(BaseModel):
    state: Literal["reachable", "partial", "failed", "timeout", "unavailable", "unsupported", "not_applicable"]
    origin: Literal["bot_api"] = "bot_api"
    route: Literal["direct", "proxy"]
    settings_revision: int
    coverage: Literal["fixed_production_dcs"] = "fixed_production_dcs"
    targets: list[TelegramProtocolTarget] = []
    applied_route: Literal["matched", "different", "unavailable"] = "unavailable"
    observed_generation: str | None = None
    observed_command_id: str | None = None
    reason: Literal["helper_unavailable", "stale_observation", "configuration_changed", "round_timeout"] | None = None


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
    telegram_protocol: TelegramProtocolResult
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
