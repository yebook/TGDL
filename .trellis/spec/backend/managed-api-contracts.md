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
secret retains the stored value; explicit clear removes it. Application API
credentials are managed in WebUI with no bare-environment fallback. Offline configuration saving
must not require Telegram connectivity. Desired mode is distinct from verified
applied mode; unrelated settings edits must not restart the API.
Initial runtime state is cloud/idle; a settings change schedules the safe
transition. Unacknowledged/incomplete child commands have bounded
persistent deadlines and fail with retry available instead of staying busy forever.

Cloud-only effective proxy changes also use a persisted `cloud_reconnect` action
and the existing busy/draining barrier, including deployments without a managed
local API. Serialize accepted operations; reject additional connection changes
while busy with `409 LOCAL_API_BUSY`, rather than silently replacing a target.
Unrelated settings saves remain allowed and must not replace the operation's
connection snapshot. This action drains owners/poll/notifications and replaces
only the cloud client: never logOut, start/stop a supervisor child, reset request
safety, or bypass cloud cooldown/uncertainty. Failed cloud reconnect permits
correction or retry without granting external deployments local-process control.

Identity publication is atomic with connection-configuration writes within the
single application runtime: `SettingsService.connection_publication_lock` covers
the final current-signature/operation/phase checks, Bot registration and in-memory
client/readiness publication in one short synchronous section. Settings save and
runtime init/update/action use the same order, publication lock then DB transaction.
Never hold this thread lock across network I/O or an await, nor acquire it while
already holding an unrelated DB write transaction. Close obsolete clients outside
the lock. Nonmanaged cloud reconnect must respect the same durable cooldown and
manual-validation barriers as managed operation; consumer/claim barriers must not
depend only on managed_local_api.

The nonroot API supervisor stays alive without credentials, controls only the
fixed official API child and uses no Docker socket. Private control volume and
independent Fernet key are shared only with app/API, never full app data or
master.key. Versioned desired.json carries command_id, action start/stop/restart,
credential_revision, api_id, api_hash_encrypted, proxy_encrypted and
proxy_signature. The proxy signature hashes the canonical effective proxy (only
enabled=false when disabled); encrypted proxy contains strict complete settings.
Applied status carries command/revision, matching proxy signature, allowlisted
process state/error, generation and heartbeat. Proxy active requires the current
expected command/revision and signature, nonempty generation and fresh heartbeat;
idle uses the last committed applied command. Handle matching failed command
before requiring successful signature/generation to avoid misleading timeouts.
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

`LocalApiStatus.drain_reason` is a required read-only projection:
`null | "connection" | "downloads"`. It is null outside draining, downloads when
durable fetching/saving/cancelling jobs require waiting, otherwise connection.
Do not persist it or compare translated message strings in frontend logic.

The draining phase includes finishing in-flight polling/notifications and closing
the old client, not just downloads. Its default presentation is preparing the
connection switch; mention waiting for downloads only when active download owners
or durable fetching/saving/cancelling jobs actually require that wait. Queued jobs
alone do not justify a download-wait message. Keep locks and transition barriers
unchanged; do not speed up the label by skipping safe cleanup.

Preflight required credentials, supervisor and topology before logout. Record
logout intent before the remote request; success allows immediate local start
but persists cloud availability no earlier than response time plus 600 seconds.
Timeout/crash around logout stays uncertain: no automatic replay or cloud
reconnection. Explicit acknowledged recovery may continue local start/verify,
but must not claim the remote outcome became known. Candidate validation,
runtime getMe, polling and sending respect the same operation/cooldown barriers.
Only verified target identity permits applied-mode commit and resumption.

### Bounded waits and recovery

Use `services.connection_waits` for cooperative network operations: identity25s,
logout35s and client close5s. Never detach a still-running coroutine to claim
its lock is released, and never cancel a database `to_thread` write by timeout.
Poll/notification barrier acquisition uses <=1s attempts, releasing partial locks;
45s without consumer release becomes `failed/LOCAL_API_DRAIN_TIMEOUT`. Retry
resets its wait baseline but must still acquire the original locks. Actual file
owners/durable acquisitions wait without forced release; show download waiting.

Verification has a120s no-progress deadline excluding actual future request
protection waits. Failed identity/network operations are recoverable; do not loop
forever in verifying. A proven restart unable to reconcile generation within150s
stays blocked for explicit recovery, never releases uncertain download slots.
Keep existing60/150s supervisor command acknowledgement/completion deadlines.
Timeout during logout persists logout_uncertain and must not be replayed.

Busy HTTP connection mutations must not queue stale candidates behind long
network operations. Reject concurrent connection-changing commands promptly;
unrelated saves still respect revision/candidate checks and remain available.
Local request cancellation cannot prove server writes or upstream logout failed.

Manual restart drains owners and uses a new command; the app and container keep
running. Supervisor/container failure itself may require host restart policy or
deployment intervention and must be displayed honestly. Standard Compose has
app/API and private-volume initializer, with no network sidecar or TUN capability.
Encrypted supervisor commands carry the frozen proxy configuration and exact
credential/configuration revision. Proxy-only changes drain consumers and
restart the API child with a new generation without replaying cloud logout.

`validate_allowed(candidate)` retains HTTP409 `LOCAL_API_BUSY` while an operation
is unresolved. For `failed`, `blocked` or `logout_uncertain`, return the sanitized
failure/phase reason and direct the user to local API service controls; never
describe terminal failure as an operation that will finish by waiting. In-flight
phases describe applying API or proxy settings, including proxy-only restarts.
Validation neither retries an operation nor probes through these barriers.
Regression tests cover failure reasons, busy phases and unchanged probe gating.
Fresh matching command/configuration acknowledgement is required before local
use; Bot identity verification remains separate from a process listening port.
Managed=false disables controls for independently administered APIs.

The fixed upstream source patch starts TDLib with network None and false network
flag; queue proxy selection or explicit cached-proxy disable and initialization,
then enable Other only after all successes. Do not wait for pre-initialization
queued callbacks before sending parameters, which deadlocks. Proxy failure
cannot enable direct transport. This is native transport selection, not kernel
firewall containment. Patch exact pinned source and compile it in validation.

Enabled SOCKS5 usernames and passwords each have a 255-byte UTF-8 protocol limit;
enforce in shared settings validation before saving or irreversible logout, and
again in supervisor decrypted-payload validation. Disabled proxy fields may be
retained for editing; HTTP CONNECT retains normal character limits. Maximum
Unicode HTTP credentials must fit the encrypted IPC bounds.

The fixed initializer mounts only private control volume, validates nonroot
UID/GID and uses opened no-follow directory descriptors for ownership/mode
changes, never recursive media permission changes. Runtime gates local
dispatch/poll/notify on current acknowledged process settings, while transactional
phase barriers block conflicting acquisition. Metadata checks cannot atomically
certify independent process liveness.

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
| Consumer barrier never releases | Failed with drain timeout; retry reacquires original locks, no forced unlock |
| Hanging identity/close or verification never progresses | Bounded cooperative wait, actionable recoverable failure |
| Child crash | Sanitized state, bounded recovery; manual restart available |
| Local target identity network failure | Persist LOCAL_API_CONNECTION_FAILED and actionable sanitized message; process running is not Bot readiness; preserve successful logout and request protection |
| Externally managed API | No managed process command; explicit external status |

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
blocked recovery and externally managed API status. Narrow browser tests cover status,
actions, credential blank/clear, unsaved form preservation and mobile layout.
Quiet Compose parsing must support blank credentials without dumping secrets.
Mocks cannot certify actual Telegram, local >20 MB, real proxy or Docker host behavior.

Fault tests must cover hanging cooperative identity/close/logout, barrier held
across repeated reconciliation and retry, fresh retry baselines, normal live
download waiting, persistent cooldown and uncertain logout after restart, and
HTTP busy rejection without queued stale connection saves.

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
