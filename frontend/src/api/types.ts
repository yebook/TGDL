// API-boundary DTOs. Keep these aligned with backend OpenAPI, never infer parent state in views.
export interface Session { username: string; csrf_token: string }
export interface ProxyConfig {
  enabled: boolean; scheme: 'socks5' | 'http'; host: string; port: number; username: string; password: string
}
export interface SettingsValues {
  bot_api_mode: 'cloud' | 'local'
  telegram_api_id: string; telegram_api_hash: string
  confirmation_template: string; filename_template: string
  confirmation_timeout_seconds: number; download_concurrency: number; max_auto_retries: number
  diagnostic_log_retention_days: number
  diagnostic_log_cleanup_time: string
  allowed_user_ids: number[]; download_root_id: string; timezone: string; bot_token: string; proxy: ProxyConfig
}
export interface Settings {
  revision: number; values: SettingsValues
  sources: Record<string, string>; editable: Record<string, boolean>
  configured: { bot_token: boolean; proxy_password: boolean; telegram_api_hash: boolean }
  proxy_status: { state: string; desired_revision: number | null; applied_revision: number | null; message: string }
}
export interface Task {
  id: string; title: string; kind: 'single' | 'group'; status: string; created_at: string; updated_at: string
  item_count: number; completed_count: number; total_bytes: number | null; completed_bytes: number
  effective_destination: string | null; notification_status: string; can_cancel: boolean; can_retry: boolean; can_delete: boolean
}
export interface Attempt { sequence: number; started_at: string; finished_at: string | null; error_code: string | null }
export interface MediaItem {
  id: string; job_id: string | null; media_type: string; original_name: string | null; size_bytes: number | null
  duration_seconds: number | null; status: string; phase: string | null; completed_bytes: number; total_bytes: number | null
  speed_bps: number | null; elapsed_seconds: number | null; relative_path: string | null
  error_code: string | null; error_message: string | null; linked_job_id: string | null; attempts: Attempt[]
}
export interface TaskDetail extends Task { items: MediaItem[]; expires_at: string | null }
export interface TaskPage { items: Task[]; total: number; page: number; page_size: number }
export interface StorageRoot { id: string; label: string; available: boolean; free_bytes: number | null; total_bytes: number | null; message: string }
export interface LocalApiStatus {
  managed: boolean
  state: 'unavailable' | 'waiting_credentials' | 'stopped' | 'starting' | 'running' | 'stopping' | 'failed'
  desired_mode: 'cloud' | 'local'; applied_mode: 'cloud' | 'local' | null
  operation_id: string | null
  phase: 'idle' | 'draining' | 'waiting_network' | 'logging_out' | 'starting' | 'verifying' | 'cooldown' | 'blocked' | 'logout_uncertain' | 'failed'
  message: string; generation: string | null; last_heartbeat_at: string | null
  cloud_available_at: string | null; can_restart: boolean; can_retry: boolean
}
export interface BotRequestSafety {
  state: 'ready' | 'throttled' | 'backoff' | 'auth_paused'
  retry_at: string | null; message: string; can_retry: boolean
}
export interface SystemStatus {
  diagnostic_log?: DiagnosticLogStatus
  local_api: LocalApiStatus
  bot: { state: string; username: string | null; message: string; request_safety?: BotRequestSafety }
  storage: { roots: StorageRoot[]; cache: { free_bytes: number | null; total_bytes: number | null } }
  proxy: Settings['proxy_status']; queue: { active: number; pending: number; concurrency: number }
  version: string; recovery: { blocked_jobs: number; message: string }
}
export interface DiagnosticLogStatus {
  state: 'ready' | 'error'; message: string; max_file_bytes: number; backup_count: number
  retention_days: number; last_cleanup_at: string | null; next_cleanup_at: string | null
  cleanup_time: string; timezone: string; total_bytes: number; file_count: number
}
export interface DiagnosticLogExport {
  events: Array<Record<string, string | number | boolean | null>>; truncated: boolean
}
