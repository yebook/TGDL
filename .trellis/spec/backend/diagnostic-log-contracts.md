# Persistent diagnostic log contracts

## 1. Scope / Trigger

Read for Telegram transport, request protection, runtime/download lifecycle,
application startup, system status or diagnostic export changes. This log
supports incident timelines, not a diagnosis of Telegram moderation decisions.

## 2. Signatures

GET /api/v1/system/logs is an administrator-only read operation returning
{events: Array<Record<string, string|number|boolean|null>>, truncated: boolean}.
SystemStatus.diagnostic_log is {state: ready|error, message: string,
max_file_bytes: number, backup_count: number}. Storage is a fixed JSONL file and
three archives below data_dir/logs, not a database table or arbitrary file API.

## 3. Contracts

### Persistence and bounded work

Use data_dir/logs/events.jsonl with 10 MiB active-file rotation and three
archives. The existing /data volume preserves logs across container recreation.
Use thread-safe writes/rotation, private files, and fixed paths; do not follow
symlink log files/directories. Bound serialized event size, parsing and export
work. Never hold database transactions across log filesystem I/O. Logging I/O
failure must not alter a durable download result, release acquisition ownership,
burn a retry budget or replay logout. Report sink failure in system status.
The sink does not acquire business lifecycle locks. Async transport callers use
a worker thread and await completion to retain first-error evidence; existing
connection/poll/notify locks may remain held during this short I/O. A slow log
volume can delay calls and draining. This is not a fully asynchronous logging
queue, and must not be described as guaranteeing zero disk-related delay.

### Event boundary

Emit UTC ISO timestamps with timezone and fixed event/method/outcome/error
classifications. Allowed fields: timestamp, event, level, mode, method, outcome,
error_code, http_status, duration_ms, retry_at, task_id, job_id, request_id,
suppressed_count. Validate values by category, not just field name. IDs are
internal identifiers, never Telegram user/chat IDs or Bot credentials.

Never emit arbitrary exception messages, stacks, request bodies, URLs, Bot
Tokens/API Hashes, cookies/passwords, captions/titles, original filenames or
storage paths. A token regex alone cannot make arbitrary upstream text safe.
Record the first failure and recovery, recognized 401/403/409/429, network and
bad-request classifications; avoid normal empty long-poll success spam and
summarize repeated failures/waits. Cancellation is distinct from a failed
request. Include cloud file HTTP failures as well as SDK methods, manual probes
and migration clients, using the same sink.
Classify aiohttp_socks proxy connection/timeout/error types as NETWORK_ERROR
even when they do not inherit aiohttp.ClientError; do not log proxy addresses,
passwords or raw proxy exceptions. Cloud requests must also report these
failures to the existing shared request backoff.

### HTTP and UI contracts

SystemStatus.diagnostic_log exposes state ready|error, sanitized message,
max_file_bytes and backup_count. GET /api/v1/system/logs requires administrator
authentication and returns {events: [...], truncated: boolean}: at most 1000
recent events, oldest first within the returned window. No caller-supplied
paths. Project parsed data through the same field/value rules again; corrupted
or malicious lines cannot become a disclosure channel. Bound reads, line size,
response size and file count; communicate omitted history with truncated.

WebUI uses its existing request/session handling. Export only on user action,
with busy/stale/unmount guards; no log-content polling. Download JSONL locally,
release object URLs, and show normal transient empty/error/truncation feedback.
Status polling does not reset page inputs or trigger a download.

## 4. Validation & Error Matrix

| Condition | Required behavior |
| --- | --- |
| Missing administrator session | 401, no event disclosure |
| Malformed/unrecognized field or value | Omit instead of echoing raw content |
| Oversized or corrupt line/history | Bounded read and truncated feedback |
| Symlink or inaccessible log path | Safe diagnostic error, no external file read |
| Logging disk full/failure | Visible sink error; business result unchanged |
| Writer error but readable old logs | Allow bounded export attempt |
| Repeated failure or empty polling | First failure/summary/recovery, no event flood |
| Page stale/busy/unmounted | No duplicate or late download |

## 5. Good / Base / Bad Cases

Good: a first getMe 401 and paused requests survive application recreation in
the data volume, with no token in disk or export. Base: a successful download
has an internal task ID and timestamp. Bad: a safe UI error hides a raw
token-bearing exception written to disk, or a log failure changes a successful
job into a retry and duplicates work.

## 6. Tests Required

Use temporary storage and fake Telegram, not user data or credentials. Cover
malicious exception strings/fields, first failure/recovery, repetition, empty
poll silence, persistence, concurrent rotation, inaccessible logs/symlinks,
export authentication/projection/truncation/malformed and oversized lines,
and real worker success/error association. Isolated desktop/mobile checks cover
download intent and feedback. Logging cannot recover missing past events.

## 7. Wrong vs Correct

Wrong: log str(exc), then redact a few token patterns only at download time.
Correct: classify upstream errors into fixed categories before writing and
validate field values again at export. Wrong: use Docker logs alone as a
durable audit across container replacement. Correct: keep bounded event logs
under the existing application data volume, and state retention limitations.
