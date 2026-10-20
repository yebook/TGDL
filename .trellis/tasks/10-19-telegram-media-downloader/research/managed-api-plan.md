# Research: WebUI-managed local Bot API

- Query: Configure Telegram application credentials, switch API modes, observe and restart the local API process from WebUI without restarting the application/container.
- Scope: internal architecture and existing official-reference evidence; proposed implementation handoff, not implemented behavior.
- Date: 2026-10-20
- Authorization: User accepted the managed-process direction and requested visible state and manual recovery. No further product confirmation is needed to implement this scope.

## Findings

### Minimal deployment boundary

Keep the local API as an unprivileged sidecar. Replace its `exec` entrypoint with a small Python supervisor which remains alive while credentials are missing, the local process is stopped, or cloud mode is selected. Start the sidecar in the standard deployment even for cloud mode; otherwise WebUI cannot create a missing container. Application and API containers continue to have no Docker socket or host process access. One deployment upgrade is required to install this supervisor and control mount. Afterwards ordinary configuration, mode changes and process restart need no container operation.

Keep direct and TUN deployments as two deployment-time choices. The managed process stays in the chosen namespace. WebUI must report `restart_required` for topology changes; it cannot add NET_ADMIN/TUN/devices or replace a shared network namespace dynamically. Do not silently bypass a failed proxy. Preserve standalone `compose.proxy.yaml`, not an overlay.

`managed=false` explicitly retains compatibility with an externally administered local API: retain its existing URL/cache contract and existing offline mode saving. Process control buttons are unavailable and no commands are written. Do not infer managed capability merely from a reachable port.

### Files found and existing patterns

| File | Existing behavior / consequence |
| --- | --- |
| `backend/app/services/settings.py:36` | Connection signature includes token/mode/cloud proxy. Local process generation must be considered when verifying managed connection state. |
| `backend/app/services/settings.py:42` | Settings decrypt secrets, then apply environment overrides. Reuse source/editable reporting and blank-preserves/explicit-clear behavior for credentials. |
| `backend/app/services/settings.py:129` | Offline save encrypts secrets and serializes against job claims using `BEGIN IMMEDIATE`. Existing immediate mode activation must change for managed mode. |
| `backend/app/services/settings.py:149` | Proxy desired-file atomic handoff already exists; reuse its shape without coupling API command revisions to every settings revision. |
| `backend/app/core/security.py:26` | Fernet/master.key is existing at-rest model. Never share the complete application data mount or master.key with API supervisor. |
| `backend/app/workers/runtime.py:50` | Recovery compares `.tgd-api-generation`; preserves active owners before reconciliation. Every managed process start must publish a new generation after the prior child has exited. |
| `backend/app/workers/runtime.py:89` | `ensure_client` connects immediately to saved mode. Must use applied runtime configuration and operation barrier, never desired mode during migration. |
| `backend/app/workers/runtime.py:132` | Poll loop persists update batch before advancing cursor; must join outstanding poll/batch before cloud logout. |
| `backend/app/workers/runtime.py:181` | Dispatch currently gates signature and proxy, but an in-memory-only check is insufficient for racing operation requests. |
| `backend/app/workers/runtime.py:266` | Notifications have separate client ownership and durable outbox. Pause new notifications and drain an in-flight send before closing old client. |
| `backend/app/workers/download.py:61` | Claim already checks mode inside write transaction; add the persisted operation barrier here. |
| `backend/app/workers/download.py:424` | Uncertain local fetch remains recovery-blocked until real API generation changes. A restart action must permit this case without deleting its recovery data. |
| `backend/app/main.py:272` | Manual candidate validation makes real `getMe` calls; it must not log into the target before migration or into cloud during cooldown. |
| `backend/app/main.py:314` | Extend authenticated system status with managed API status, separately from Bot online and proxy active. |
| `deploy/bot-api/entrypoint.sh:4` | Missing credentials currently terminate container; supervisor must instead acknowledge waiting_credentials. |
| `deploy/bot-api/entrypoint.sh:13` | Generation currently changes once per container; move to per-child lifecycle. |
| `deploy/network/supervisor.py:85` | Strict regular-file/NOFOLLOW reading, bounded input, atomic writes and sanitized errors are reusable patterns. |
| `deploy/network/supervisor.py:222` | Existing child terminate/wait/kill/reap lifecycle should be mirrored. Never publish restart success before wait proves old child exited. |
| `deploy/bot-api/Dockerfile` | Fixed official revision `e3e9dd8e5b3d7ab8537cd5a10dc31d5ffa8f82d1`; add Python and minimal crypto dependency for supervisor, not Docker tooling. |

### Concrete cross-worker handoff contract

Recommended ownership: deployment worker owns `deploy/bot-api/*`, both Compose files, `.env.example` and supervisor tests; backend worker owns settings/model/migration/runtime/download/API/schema and backend tests; frontend worker owns Settings/System views, API DTOs and frontend tests. Backend and deployment workers share only this fixed protocol, avoiding runtime-file overlap.

Config additions: `managed_local_api: bool` (default false outside supplied managed Compose), `local_api_control_dir: Path` (`/run/tgd-api-control` inside containers). Compose explicitly enables managed mode and mounts an isolated private control volume into app and API, never network container. Preserve user data/cache/download paths and UID/GID. This directory is not the read-only media cache mount.

Settings additions:

- `telegram_api_id: str = ""`: positive decimal integer string; do not invent an account identifier format. Empty is permitted for cloud/offline saving.
- `telegram_api_hash: str = ""`: write-only secret, blank preserves existing value. `clear_telegram_api_hash: bool` explicitly clears. Encrypt at rest using existing Secrets. Never serialize hash back in values.
- `configured.telegram_api_hash: bool`; source/editable entries for both fields. Neither secret nor a masked hash should be placed in public errors or status.
- `bot_api_mode` is desired mode in managed mode. Saving mode/credentials creates a durable pending operation with a configuration snapshot/revision; runtime uses applied mode until the coordinated boundary. Saving unrelated fields must not restart API.

Persistent singleton `LocalApiRuntime` (dedicated table/migration, not arbitrary public settings JSON): `applied_mode`, `operation_id`, `phase`, `desired_revision`, `credential_revision`, `command_id`, `target_mode`, `generation`, `cloud_available_at`, `logout_uncertain`, timestamps and sanitized `error_code`. Keep credential/token snapshots encrypted if persisted; never put plaintext in operation rows. New operation captures consistent token/config identity and cannot be changed under an active switch by another settings save. Reject conflicting connection-affecting saves with 409; allow unrelated settings edits. Store explicit none/unknown for legacy applied mode rather than claiming an unverified local connection.

Private file protocol version 1:

```json
{
  "version": 1,
  "command_id": "opaque unique operation command id",
  "action": "start",
  "credential_revision": 1,
  "api_id": "123456",
  "api_hash_encrypted": "Fernet ciphertext"
}
```

`action` is exactly `start|stop|restart`. `stop` permits omitted/blank credential fields; `start/restart` require complete valid credentials. Replaying the same command ID never restarts a healthy child again. A fresh command with unchanged credentials and action=start is idempotent. Restart is an explicit new command ID. Reject unknown fields/version, invalid types, unbounded files, symlinks/nonregular files and malformed credentials. Command ID, not generic settings revision, determines execution. Command ordering and restart acknowledgement must survive supervisor restart; a cached acknowledgement cannot claim a child from the previous supervisor is still running.

Response file:

```json
{
  "version": 1,
  "command_id": "same id as desired command",
  "state": "running",
  "generation": "new random child instance id",
  "heartbeat_at": 1792454400.0,
  "error_code": ""
}
```

`state`: `waiting_credentials|stopped|starting|running|stopping|failed`. `running` proves child alive and local HTTP readiness, not Bot identity or actual Telegram connectivity. Heartbeat rewritten periodically even without a new command; app treats missing/stale/invalid response or mismatched command as unavailable/pending, never active. Include only bounded allowlisted error codes; no raw child stderr, command payload, API ID/hash/token, arbitrary URL or host paths.

Use existing Fernet for encrypted handoff with an independent `control.key`, generated once in this private directory with 0600 and atomic exclusive creation. Both containers run the same trusted nonroot UID; do not mount application master.key. Desired and applied files 0600, directory 0700, strict reads and same-directory atomic replace/fsync. A named persistent private volume avoids leaving extra plaintext handoff files in project directories. The control key and ciphertext co-location follows the project's local-secret threat model: it is not protection against host root or a compromised trusted container. Never claim otherwise. Hash is decrypted only in supervisor memory, passed through child environment, never argv or diagnostics; OS administrators can inspect process environment. Supervisor should not retain raw secret values in status/errors. Legacy `.env` TELEGRAM_API_ID/HASH may bootstrap the managed child only when explicitly selected as credential source; existing environment values must not permanently prevent WebUI override. Credential precedence must be explicit in UI, and legacy env changes continue to require deployment recreation.

Authenticated `GET /api/v1/system/status` includes `local_api: LocalApiStatus` (can also include it in settings response for convenient initial render):

```text
managed: bool
state: unavailable|waiting_credentials|stopped|starting|running|stopping|failed
desired_mode: cloud|local
applied_mode: cloud|local|null
operation_id: str|null
phase: idle|draining|logging_out|starting|verifying|cooldown|blocked|logout_uncertain|failed
message: safe localized text
generation: str|null
last_heartbeat_at: RFC3339|null
cloud_available_at: RFC3339|null
can_restart: bool
can_retry: bool
```

`managed=false` returns unavailable with a clear external-management explanation, not an application error. Bot and proxy remain distinct status objects. UI can show “本地 API 正在运行 / Bot 连接失败” without conflating the layers. A dead/stale control supervisor disables process actions with “管理组件未连接，需检查部署”; WebUI cannot revive an exited supervisor/container without an external container engine.

Mutation endpoints: `POST /api/v1/system/local-api/restart`, `POST /api/v1/system/local-api/retry`. Standard auth/Origin/CSRF; return 202 + LocalApiStatus for accepted operation, 409 for incompatible/in-progress action, 422 for missing required inputs, 503 when managed supervisor unavailable. Idempotency should attach/reuse current compatible action instead of enqueueing several restarts. Restart means local API child, not app, network namespace or container. User request is already explicit permission for that action; no second generic confirmation is necessary. Add start/stop only if necessary for actual UI flow; save-mode is sufficient for normal enable/disable.

### Safe switch and restart state machine

1. Persist operation and claim/intake barrier in one database write transaction. `DownloadWorker.claim` reads this barrier in its own write transaction, so no new job may be claimed after an operation has been accepted, including a race where runtime checked an old boolean. Queued jobs remain durable.
2. Stop new polling, inbox dispatch, notification acquisition, identity checks and manual validation against either endpoint. Let outstanding poll finish and persist returned updates/cursor, then acknowledge poll loop paused. Join any in-flight notification send and save result; do not lose outbox or pending updates. Use an async lock/ack barrier around acquisition/recheck, not only the top of each loop. No cancellation is allowed between receiving updates and durable persistence. A finite 25-second long poll may be awaited for less UI interruption/complexity.
3. Drain running file owners and durable fetching/saving/cancelling jobs. Existing clients stay alive for those jobs until they complete. Report waiting active count and elapsed phase. A blocked job is not zero activity. Mode switches remain blocked on recovery; manual local restart can resolve uncertain local fetches after live owners have drained. Never kill local API during a known live copy/publication operation.
4. Preflight target credentials, managed supervisor heartbeat, selected topology/proxy readiness and cache path without logging the Bot into target. Do this before irreversible cloud logout. A TCP/local health probe without Bot token is allowed; `getMe` against target before logout is not.
5. Cloud→local: persist phase=logging_out and attempt intent before making exactly one cloud `logOut` through a dedicated cloud client using the old verified Bot's configured cloud proxy. No implicit direct connection. Success persists `cloud_available_at >= response time + 600s` (conservative) and phase=starting before starting local child/login. A network timeout or crash around the call leaves logout_uncertain: do not automatically replay logout, reconnect cloud, or claim success. Expose recoverable diagnostics/manual retry with an explicit explanation of ambiguity; a generic retry must not blindly issue logout again. Retain a conservative cooldown from attempt time and prohibit automatic cloud probing until uncertainty is resolved. This operation cannot be made exactly-once across a remote nontransactional call by DB alone.
6. Start local child with acknowledged command/generation, then create local Bot client and verify identity/token ownership. Persist applied_mode=local only after successful target verification. Resume polling/notifications/claim only after operation is idle. If startup/getMe fails after successful logout, remain paused with failed target and retry local; never automatically fall back to cloud.
7. Local→cloud: wait until persisted cloud_available_at; UI shows remaining time and phase=cooldown, not general failure. Stop local polling/file owners, command stop child, wait acknowledgement, then connect cloud. Persist applied cloud only after verified identity. Do not delete local cache/session data. Exact Telegram reverse migration behavior beyond documented cooldown remains an actual integration test item.
8. Credential update in applied-local mode follows the same barrier/drain, stops/reaps child and starts new generation. It does not call cloud logout again. Initial cloud Bot→local activation still follows logout even if there was no previously connected cloud consumer. A fresh managed installation with desired local cannot assume it never used cloud elsewhere.
9. On application restart load operation state before starting any runtime loops, including ensure_client and manual validation. Recover safe idempotent start/stop/verify phases using command IDs, and surface ambiguous logout phases for explicit recovery. Persist cloud cooldown across app/API container restart and token changes keyed by Bot identity/token snapshot; changing a credential field must not erase an existing Bot's cooldown.
10. Manual restart is allowed when local owners have drained, including durable recovery_blocked acquisitions. Confirm old child termination before changing generation; then existing generation reconciliation can requeue uncertain local jobs. Never discard journal, reservations or part files as a shortcut, never declare an API restart from an HTTP timeout alone.

### Important concurrent paths

- `settings/validate` is a real upstream call; block managed cross-mode candidate validation until the switch is committed, and block all cloud validation/identity calls before cooldown. Offline save is still permitted for valid candidates.
- `poll_loop.ensure_client` must not select desired configuration during operation or reconnect automatically after logout failure.
- `tick_loop.freeze_due` can prepare durable confirmations while paused, but it must not emit them or claim jobs until resumed; expiration must remain honest.
- `notify_loop` must preserve Bot ownership and join sends. Pausing notifications must not increment attempts in a tight retry loop.
- New token or proxy changes during drain cannot silently change the logout destination/identity; snapshot or reject connection-affecting changes. Unrelated UI saves need no new API process command.
- An externally running Bot consumer cannot be stopped by this app; docs must still tell users this app must be the sole consumer, and polling conflicts must produce visible errors.

### Verification and affected files

Backend: schemas/contracts.py and responses.py, services/settings.py and new services/local_api.py, db/models.py and migration0006, workers/runtime.py and download.py, bot/client.py (dedicated cloud logout method), core/config.py, main.py. Add targeted tests for actual persisted state and race barriers, not implementation mirror tests.

Deployment: deploy/bot-api Dockerfile/entrypoint/supervisor, compose.yaml and compose.proxy.yaml, .env.example, deploy sources and deployment/local-run/telegram-api docs. Frontend: API DTOs, settings store/view and system status components, tests/e2e affected flows only.

Required meaningful checks:

- Missing credentials keep supervisor alive; start/stop/restart use one child and unique real generations; identical command replay does not restart; terminate timeout/kill/reap precedes restart; crash/heartbeat invalidation; malformed/symlink oversized payload rejected; no secrets in diagnostics/status/argv.
- Offline credential save, hash encryption/no echo/clear/source semantics; unrelated field save does not restart; old env external-managed compatibility; migration retains settings/jobs.
- Concurrent operation acceptance versus worker claim produces no new post-barrier claim. In-flight update persists before logout; no new notify/getMe/validate during paused phase. Ownership and custom proxy remain consistent.
- Successful mocked logout persists cooldown, starts local only afterwards; failed preflight never logs out; ambiguous timeout/crash does not reconnect cloud or replay logout; app restart preserves pause/cooldown. Local retry after post-logout failure never uses cloud.
- Restart while live file owner exists waits; recovery_blocked with no live owner can restart; generation reconciliation preserves files/journals and releases uncertain slots only after observed real child replacement.
- UI preserves unsaved fields during status polling, shows phase/service/Bot/proxy separately, disables conflicting actions, renders cooldown and retry/restart outcome, works narrow viewport. Use fake API fixtures, never existing user session.
- Parse both Compose topologies with empty credentials using quiet output; no Docker socket, API public port, mount permission regression or secret in expanded output. No actual user service restart or migration in research.

### External references / official constraints

- https://core.telegram.org/api/obtaining_api_id — application credential creation; no personal user session belongs in this app.
- https://core.telegram.org/bots/api#logout — cloud logout before local use; local can log in immediately after success, cloud unavailable for 10 minutes.
- https://github.com/tdlib/telegram-bot-api#moving-a-bot-to-a-local-server — single local/cloud ownership and migration flow.
- https://core.telegram.org/bots/api#close — local-instance move method; not a replacement for cloud logout.
- Existing researched evidence: research/technical-evidence.md; docs/telegram-api.md:50,71,171. This pass uses those retained official-reference facts and did not send any live Telegram/Bot request or fetch private credentials.

### Related specs

- backend/cloud-api-contracts.md: current “no automatic logout / optional profile / env-only credentials” is superseded only for explicitly managed deployment; keep external API compatibility documented.
- backend/runtime-contracts.md: claim transactions, runtime client identity, generation recovery, encryption and offline saving remain required.
- backend/acceptance-contracts.md: isolated fixtures and secret-free output; mock success is not real Telegram success.
- frontend/ui-contracts.md and state-management.md: stable polling, sources/conflicts, transient operation feedback with persistent actual service state.

## Caveats / Not Found

- This is a concrete handoff proposal, not shipped behavior. No product files changed, no secrets read, no actual Bot message/logout/connection, no deployment restart.
- User requirement authorizes WebUI management, not unrestricted remote Docker control. Container/supervisor crash itself still depends on restart policy/host operations; child restart only is the product operation.
- No official exactly-once logout token or definitive remote query for ambiguous logout was established. Persisted intent plus an honest uncertain state is required; do not claim fully automatic recovery of that case.
- Actual official-server graceful termination/background transfer quiescence, reverse migration, invalid API credential error behavior, >20 MB download, proxy MTProto/DNS path and fnOS permission behavior remain real-environment gates. A local cached getMe cannot establish the live Telegram transport route.
- A named private volume with crypto key is still local secret storage, not a host-root defense. Document backup and key retention as needed without exposing keys in user diagnostic exports.
