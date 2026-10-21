# Managed local Bot API

## 1. Scope / Trigger

Read for managed local API credentials, lifecycle, mode transitions, runtime
barriers, supervisor handoff, deployment or status controls. This supersedes the
manual-only migration contract only when `Config.managed_local_api` is true.

## 2. Signatures

- Settings: `telegram_api_id: str`, write-only `telegram_api_hash: str`,
  `clear_telegram_api_hash: bool` in patches; configured hash flag in responses.
- Config: `managed_local_api`, `local_api_control_dir`; deployment enables these.
- Persistent singleton local API runtime stores operation/phase, applied mode,
  command/credential revision, generation, logout uncertainty and cloud cooldown.
- `POST /api/v1/system/local-api/restart` and `/retry`: authenticated, same-origin,
  CSRF protected; accepted operations return 202 with local API status.
- Retry accepts `acknowledge_logout_uncertain: bool = false`. Uncertain logout
  requires explicit acknowledgement; never infer it from ordinary retry.
- `/api/v1/system/status.local_api`: managed, state, desired_mode, applied_mode,
  operation_id, phase, message, generation, last_heartbeat_at, cloud_available_at,
  can_restart, can_retry. State is independent of Bot identity/online status.

## 3. Contracts

API Hash is encrypted with the app's Secrets at rest and never echoed. Blank
secret retains the stored value; explicit clear removes it. Old deployment API
credentials can remain an overridable fallback. Offline configuration saving
must not require Telegram connectivity. Desired mode is distinct from verified
applied mode; unrelated settings edits must not restart the API.
First enabling managed mode on a legacy desired-local database must use unknown
applied mode and wait for explicit retry. An upgrade alone cannot initiate Bot
logout or target login. Unacknowledged/incomplete child commands have bounded
persistent deadlines and fail with retry available instead of staying busy forever.

The nonroot API supervisor stays alive without credentials, controls only the
fixed official API child and uses no Docker socket. Private control volume and
independent Fernet key are shared only with app/API, never full app data or
master.key. Versioned desired.json carries command_id, action start/stop/restart,
credential_revision, api_id and api_hash_encrypted. Applied status carries the
command ID, allowlisted process state/error, generation and heartbeat only.
Bounded regular-file reads reject symlinks; private atomic writes sync before
replace. Secrets go to child environment, never argv/status/logs. Identical
command replay cannot restart a healthy child. Publish a new cache generation
only after the previous child has exited. Heartbeat or command mismatch is not
proof that the requested process is running.

Persist an operation before accepting a switch/restart. Worker claim checks its
barrier in the same write transaction. Stop new polling/notification requests,
join in-flight requests and persist received updates/results before cloud logout.
Drain known live file owners and durable active jobs without cancelling them as
a shortcut. Local recovery-blocked acquisitions require a proven child restart
before generation reconciliation can release uncertain slots.

Preflight required credentials, supervisor and topology before logout. Record
logout intent before the remote request; success allows immediate local start
but persists cloud availability no earlier than response time plus 600 seconds.
Timeout/crash around logout stays uncertain: no automatic replay or cloud
reconnection. Explicit acknowledged recovery may continue local start/verify,
but must not claim the remote outcome became known. Candidate validation,
runtime getMe, polling and sending respect the same operation/cooldown barriers.
Only verified target identity permits applied-mode commit and resumption.

Manual restart drains owners and uses a new command; the app and container keep
running. Supervisor/container failure itself may require host restart policy or
deployment intervention and must be displayed honestly. Standard Compose always
includes a network supervisor. Direct and proxy commands use the same topology
and are switched from WebUI; fresh matching heartbeat/revision acknowledgement
is required for either. Commands use the frozen operation snapshot before cloud
logout. `waiting_network` has a bounded deadline and appears in DTOs/diagnostics.
Proxy-only changes drain consumers without replaying logout. Enabled proxy
failure never falls back to direct or an obsolete configuration. Managed=false
disables process actions for independently administered APIs.

The network supervisor treats invalid, stale or nonincreasing desired revisions
as revocation: install blocking egress rules and stop its child before reporting
failure, including when the previous mode was direct. Root-written network IPC
uses random exclusive no-follow temporary files and atomic replacement; reads
reject nonregular/hardlinked files and changed inode identity. The fixed
initializer mounts only private control volumes, validates nonroot UID/GID and
uses opened no-follow directory descriptors for ownership/mode changes, never
recursive media permission changes. Status tests must exercise actual HTTP DTOs
for `waiting_network` and proxy `unavailable`, as well as diagnostic projection.

Runtime checks fresh network acknowledgement before local dispatch/poll/notify;
the DB claim barrier atomically blocks settings edits/migrations. Kernel routing
rules provide proxy fail-closed behavior when a child dies. A metadata read in a
worker claim cannot atomically certify independent process/network liveness and
must not be described as such.

The network supervisor treats invalid, stale or nonincreasing desired revisions
as revocation: install blocking egress rules and stop its child before reporting
failure, including when the previous mode was direct. Root-written network IPC
uses random exclusive no-follow temporary files and atomic replacement; reads
reject nonregular/hardlinked files and changed inode identity. The fixed
initializer mounts only private control volumes, validates nonroot UID/GID and
uses opened no-follow directory descriptors for ownership/mode changes, never
recursive media permission changes. Status tests must exercise actual HTTP DTOs
for `waiting_network` and proxy `unavailable`, as well as diagnostic projection.

Runtime checks fresh network acknowledgement before local dispatch/poll/notify;
the DB claim barrier atomically blocks settings edits/migrations. Kernel routing
rules provide proxy fail-closed behavior when a child dies. A metadata read in a
worker claim cannot atomically certify independent process/network liveness and
must not be described as such.

## 4. Validation & Error Matrix

| Condition | Required behavior |
| --- | --- |
| Missing application credentials | Offline save allowed; waiting_credentials; no child |
| Missing/stale supervisor heartbeat | Unavailable; no invented running status |
| Active download | Draining state; no new claims; do not kill owner |
| Local recovery uncertainty | Block mode switch; allow safe restart after live owners drain |
| Conflicting connection save during operation | 409; retain current operation/config |
| Unknown logout result | Persistent logout_uncertain; generic retry refused |
| Cloud cooldown | Visible deadline; no early probe/connection |
| Child crash | Sanitized state, bounded recovery; manual restart available |
| Local target identity network failure | Persist LOCAL_API_CONNECTION_FAILED and actionable sanitized message; process running is not Bot readiness; preserve successful logout and request protection |
| External deployment | No managed process command; explicit compatibility status |

## 5. Good / Base / Bad Cases

Good: save local credentials offline, observe draining/logout/start/verify,
then resume downloads without replacing app/container. Base: cloud deployment
keeps supervisor waiting with no API credentials. Bad: a status poll or manual
candidate validation logs into local before logout; restarting a container is
reported as the meaning of the process button; a timeout clears uncertain slots.

## 6. Tests Required

Use temporary databases/control directories, fake Telegram clients and real
test child processes. Assert secret encryption/no echo, migration preservation,
command replay, child termination before generation replacement, stale heartbeat,
claim/operation races, poll persistence before logout, durable uncertainty and
cooldown across app restart, candidate-validation barriers, unrelated saves,
blocked recovery and external compatibility. Narrow browser tests cover status,
actions, credential blank/clear, unsaved form preservation and mobile layout.
Quiet Compose parsing must support blank credentials without dumping secrets.
Mocks cannot certify actual Telegram, local >20 MB, TUN or fnOS behavior.

Windows Docker Desktop host binds may appear inside Linux containers as
root-owned mode 777 regardless of host ACLs. Do not relax the control protocol's
UID/mode checks: use a private Linux data volume initialized to the app/API UID
and mode 700. Preserve existing app/download/cache mounts and include this
control volume in backups. Linux/NAS bind directories retain the same checks.

## 7. Wrong vs Correct

Wrong: `settings.bot_api_mode` immediately reconnects while an async migration
is still running; a failed logout automatically falls back to cloud.
Correct: desired/applied modes and persisted barriers control every upstream
consumer, uncertain remote outcomes remain explicit, and the UI reports them.
