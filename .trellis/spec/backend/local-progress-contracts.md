# Local API acquisition progress

## 1. Scope / Trigger

Read when changing the native progress patch, local observation transport, worker ownership, per-file progress DTO or their UI. Progress is telemetry for an existing download, never a second acquisition path.

## 2. Signatures

Private patched Bot API POST `/botTOKEN/tgdGetFileProgress`, form `file_ids` JSON array (1..16 opaque strings, each <=4096 UTF-8 bytes, total input <=80KiB). Standard response result contains `generation` and `files`; each row has `file_id`, `available`, and, when available, `downloaded_bytes`, nullable `total_bytes`, `total_kind: exact|estimated|unknown`, `active`, `completed`.

Supervisor passes `TGD_PROGRESS_GENERATION` to the new child using the same generation committed in applied status. Public `MediaResponse.download_progress` is null or `{downloaded_bytes,total_bytes,total_kind,speed_bps,active,completed}`; no token, file alias, native file ID or generation reaches WebUI. No database schema change: observations are ephemeral and bounded.

## 3. Contracts

The native method is local-build capability and already-active Client only. Missing/unsupported/unknown Client returns unavailable/error without starting a Client, initializing TDLib, updating account/flood statistics, invoking getRemoteFile/getFile/downloadFile/logout or reconnecting. The existing getFile callback records aliases; updateFile refreshes a bounded per-Client in-memory snapshot cache (128 files,1024 aliases, brief terminal retention). A query reads those snapshots only. Preserve the native proxy startup-offline gate.

The worker's existing single getFile retains acquisition ownership. Approximately one read/second per active job uses a short bounded HTTP timeout, <=512KiB response, no redirects and no host/environment proxy inheritance for private service calls. Reads bypass Telegram request pacing and never call request-safety observe/reset or masquerade as Telegram success. Unsupported endpoints back off. Observation errors, malformed payloads, missing data and timeouts only remove progress availability.

The app registry holds at most16 job observations, expires public samples after4 seconds, and clears on acquisition exit. Native successful terminal snapshots retain60 seconds. Managed service generation must match fresh running supervisor acknowledgement, current runtime client generation, applied mode/command/credential revision; unmanaged APIs without this proof expose null progress. A failing generation provider also yields unavailable progress without failing task reads or downloads.

An ordinary supervisor/container restart may launch a new child for the same committed command. Persisted `applied_generation` records the previous managed operation and is not the current child authority. Prove the current generation from fresh running supervisor status and the runtime's verified current client, with idle/local mode, exact committed command/revision and current configuration/proxy signature guards. Never update durable generation or issue an identity probe merely to display telemetry. A restart regression must begin with an actually persisted older generation and exercise normal client initialization; otherwise matching synthetic acknowledgements miss this lifecycle.

Validate exact Bot/file/job/attempt/boot/API child generation and supervisor freshness before publishing observations and when projecting DTO. An observer cannot write after leaving local fetching, cancellation, retry, terminal state or generation replacement. Cancel/join only the observer on acquisition exit; do not propagate its errors/cancellation into getFile or release an uncertain local slot. A native completed flag or 100% byte count never completes a Job.

Use monotonic read intervals for speed from newly acquired bytes; first cached bytes do not imply transfer speed, and counter decreases reset the baseline. Track read freshness separately from last byte-change time. Fresh unchanged bytes can mean zero throughput, not unavailable. Exact positive size permits percentage; expected_size is estimated and must not be presented as exact. Unknown total keeps real byte counts with indeterminate progress. The destination saving stage retains its separate counters and publication journal.

## 4. Validation & Error Matrix

| Condition | Behavior |
| --- | --- |
| Missing Client / capability / nonlocal mode | Reject or unavailable; no new Client or upstream operation |
| Invalid/oversize batch or fields | Bounded sanitized failure; do not echo credentials |
| Unknown file alias | available=false, never fabricated zero progress |
| Generation/owner mismatch or stale read | Public progress=null; acquisition unchanged |
| Progress timeout/unsupported/malformed response | Fallback/backoff; no retry-budget or request-protection effects |
| getFile completes, phase changes or retry starts | Observer joined/old record cleared; no late overwrite |
| Native reports completed | Keep awaiting getFile/save/publish lifecycle |

## 5. Good / Base / Bad Cases

Good: one getFile is pending while cached updateFile snapshots produce increasing measured bytes; progress bridge fails and the download continues. Base: external stock API returns unavailable progress. Bad: poll getFile again, infer bytes by stat of sparse cache files, turn a progress error into download failure, or reuse another attempt's sample.

## 6. Tests Required

Actual production native cache tests cover aliases, Bot isolation through separate Clients, eviction/bounds, TTL, exact/estimated/unknown size and shared files. Compile pinned sources and verify absent-client/malformed progress routes in network isolation without client creation/egress. Backend controlled-clock/fake transport tests cover response bounds, single getFile, unavailable reads, speed/counter reset, cancellation, attempts/generation/staleness and DTO guards; preserve protection and ownership. Frontend tests cover measured zero versus unknown, exact versus estimated/unknown totals, cache hits, cancellation priority and transition to saving/cloud. Browser verification uses isolated synthetic state, not production media or credentials. Real Telegram/large-file/Docker host claims require separate evidence.

## 7. Wrong vs Correct

Wrong: progress at100 marks succeeded, or a local observation success clears Telegram backoff. Correct: only existing getFile/save/hash/publish flow determines success; read-only telemetry is independent of Telegram protection and acquisition lifecycle.
