# Cloud and local Bot API contracts

## 1. Scope / Trigger

Read when changing Telegram transports, settings mode selection, task acquisition,
recovery, proxy reporting or Compose profiles. Local large-file support remains a
release requirement; cloud is an additional entry point.
For managed deployment, [managed-api-contracts.md](managed-api-contracts.md)
supersedes manual-only mode activation, optional API service and logout rules
below. The following manual migration description applies to externally managed
deployments; all file acquisition and recovery invariants still apply.

## 2. Signatures

`UserSettings.bot_api_mode` and `SettingsPatch.bot_api_mode` accept `cloud|local`.
`PATCH /api/v1/settings` persists the mode offline; `POST /api/v1/settings/validate`
uses the candidate mode without saving. `TelegramClient(token, config, settings)`
selects the transport. `Job.acquisition_mode` records the mode at claim for
restart recovery.

## 3. Contracts

Fresh databases and standalone schema both default to `cloud`. Settings
DTO `values`, `sources` and `editable` include `bot_api_mode`; secrets remain
write-only. A saved mode is desired configuration, not proof of connection.

Cloud uses `https://api.telegram.org`, needs no application API ID/Hash, and caps
individual files at `20 * 1024 * 1024` bytes. Check message size, getFile metadata,
HTTP Content-Length and cumulative streamed bytes; unknown lengths still obey
the cap. Accept only safe relative file paths from getFile, disable redirects,
and never expose token-bearing URLs in diagnostics. File data uses the existing
part/hash/fsync/non-overwriting publication journal pipeline.

Cloud HTTP CONNECT/SOCKS5 proxy configuration belongs to the app session and
covers both API calls and file downloads without direct fallback. Connection
signatures include token, mode and cloud proxy values. Proxy active status needs
successful runtime identity verification using that signature. Local mode keeps
the supervisor desired/applied acknowledgement and shared-cache behavior.

Mode changes and job claims synchronize through database write transactions.
Reject a mode change while jobs are fetching, saving, cancelling or recovery
blocked. Claim checks the current mode and records acquisition ownership.
Recovery follows each job's recorded mode: cloud interrupted streams do not wait
for local API generation; uncertain local acquisitions retain their slots.
Existing Bot identity, queued-job and notification ownership guards still apply.

Root `compose.yaml` is the single standard deployment, including app, managed
API supervisor plus a bounded private-volume initializer. No proxy
Compose variant or deployment profile is required. Cloud HTTP proxy remains
in the application SDK; local direct/proxy uses patched native TDLib configured
through encrypted exact supervisor commands. Neither needs host TUN. WebUI never operates Docker. API migration is coordinated by
the managed service's durable barriers and logout rules. Official migration and
cooldown requirements are documented in `docs/telegram-api.md`. Local login is
immediate after successful cloud logout; the 10-minute restriction applies to
returning to cloud. Never replay logout on restart, bypass uncertain outcomes,
silently fall back to direct when proxy is enabled, or discard pending updates.

Local `getMe` may use TDLib's cache and empty `getUpdates` can succeed without
upstream connectivity. Neither proves live Telegram delivery. Diagnose receipt
through processed updates and notification outcomes, separating API health from
upstream transport. Docker containers do not inherit the host GUI/system proxy;
use explicit WebUI proxy settings when the default route cannot reach Telegram.
Changing that proxy uses normal managed drain/restart with existing cooldown and
logout state intact, never another cloud logout or a protection reset.

## 4. Validation & Error Matrix

| Condition | Result |
| --- | --- |
| Invalid mode | 422, no upstream request |
| Active/recovery-blocked acquisition during mode change | 409 `API_MODE_BUSY` |
| Stale revision | 409 `REVISION_CONFLICT` |
| Cloud file exceeds limit at any boundary | `CLOUD_FILE_TOO_LARGE`; no published file |
| Unsafe cloud file path | `INVALID_API_PATH`; no arbitrary fetch |
| File endpoint returns 429 or 5xx | Retryable `CLOUD_DOWNLOAD_FAILED` |
| File endpoint redirects or returns another non-200 | Non-retryable `CLOUD_DOWNLOAD_REJECTED`; no redirect follow |
| Missing local service | Offline save allowed; online connection fails safely |

## 5. Good / Base / Bad Cases

Good: a confirmed cloud file streams through a configured proxy and publishes
atomically. Base: saving local mode without a reachable API preserves configuration
and reports connectivity separately. Bad: treating a cloud stream interruption as
uncertain local getFile, bypassing an unknown file's cap, or changing mode while
an old acquisition still owns a slot.

## 6. Tests Required

Backend checks cover current cloud defaults, offline persistence, candidate-only
validation, mode save/claim protection, cloud success/failure/size boundaries,
cancellation and transport-specific recovery. Assert no final publication on
failure and no premature local slot release. Browser checks verify mode persistence
and unsaved candidate validation. Parse default and local profile Compose with
empty application credentials without printing expanded secrets. Actual Telegram,
proxy routing, >20 MB local transfers and fnOS remain separate real-device gates.

## 7. Wrong vs Correct

Wrong: choose recovery semantics from whichever global mode happens to be saved
after restart, or mark proxy active because PATCH succeeded. Correct: record each
claim's transport ownership and acknowledge only an actually verified connection.
