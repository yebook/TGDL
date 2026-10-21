# Research: Local Bot API real download progress

- Query: Discuss a real local Telegram download progress design; do not implement code or change the running application.
- Scope: mixed; current repository and read-only pinned public upstream source. No Telegram requests, runtime/cache traversal or credential reads.
- Date: 2026-10-21

## Findings

### Existing architecture and files

- `backend/app/bot/client.py:98`: the application issues one normal Bot API `getFile` and receives the path only when the local acquisition completes.
- `backend/app/workers/download.py:280`: local acquisition awaits that single request while keeping the job's ownership slot; cancellation must not assume the underlying native download stopped.
- `backend/app/workers/download.py:191`: existing progress persistence is throttled to twice per second and stores completed bytes and speed. It currently describes cloud streaming or local cache-to-destination copying, not the local network acquisition.
- `backend/app/services/tasks.py:209` and `backend/app/schemas/responses.py:76`: per-file DTO already carries acquisition mode, phase, completed/total bytes, speed and elapsed seconds.
- `frontend/src/api/mediaProgress.ts:12`: only `saving` currently has measured progress. A local `fetching` phase intentionally has an indeterminate indicator. A new explicit availability/source field would distinguish unavailable observations from a real 0-byte measurement.
- `deploy/bot-api/native-proxy.patch:1`: source patch already exists and is compiled against fixed official revisions; progress could extend this mechanism without introducing a different Telegram client.
- `deploy/bot-api/Dockerfile:4`: Bot API revision `e3e9dd8e5b3d7ab8537cd5a10dc31d5ffa8f82d1`, TDLib submodule `bc9c263e2bfee06aaab41e82db51a103376030bc`.
- `deploy/bot-api/supervisor.py:140`: private encrypted-command/file-status channel already exists and publishes generation, desired command, credential revision and heartbeat. It does not currently expose file progress or a generic HTTP service.

### Verified upstream observation points

Exact public raw source was fetched into memory only. Local compiled source had previously been removed during the authorized temporary cleanup, so it was not assumed to remain available.

Bot API [Client.cpp at pinned revision](https://github.com/tdlib/telegram-bot-api/blob/e3e9dd8e5b3d7ab8537cd5a10dc31d5ffa8f82d1/telegram-bot-api/Client.cpp):

1. Lines 17032–17036: `process_get_file_query` reads the original opaque Bot API `file_id`, then resolves it with `check_remote_file_id`.
2. Lines 9033–9041: resolution uses TDLib `getRemoteFile`, not a public file-id decoding scheme.
3. Lines 17040–17049: `do_get_file` knows the numeric TDLib `file->id_`, records waiting queries in `file_download_listeners_`, and issues the existing `downloadFile(file_id, 1, 0, 0, false)` request.
4. Lines 9577–9580: TDLib `updateFile` is already received and forwarded to `on_update_file`.
5. Lines 9373–9388: `on_update_file` already accesses `local_->downloaded_size_`, `is_downloading_active_` and `is_downloading_completed_` for ongoing downloads.
6. Lines 17056–17070: success/error completes waiting `getFile` requests. Observation can preserve this lifecycle unchanged.
7. Lines 17952–17959: public `file_id` is emitted from `file->remote_->id_`, but the input alias should still be captured explicitly; a file may have multiple valid remote identifiers.

TDLib [td_api.tl at pinned revision](https://github.com/tdlib/td/blob/bc9c263e2bfee06aaab41e82db51a103376030bc/td/generate/scheme/td_api.tl):

- Lines 289–292: `localFile` has `downloaded_size`, `downloaded_prefix_size`, `download_offset`, `is_downloading_active`, `is_downloading_completed`. The documentation explicitly says downloaded_size is appropriate for progress; actual filesystem length can contain garbage and be larger.
- Lines 305–311: `file` has numeric `id`, `size` and `expected_size`; expected_size is approximate when the exact size is unknown.
- Line 10767: `updateFile file:file = Update`.
- Lines 11583–11590: TDLib `getFile` and `getRemoteFile` are explicitly offline methods. They differ from the public Bot API getFile which triggers acquisition here. Even these offline lookup calls are unnecessary for polling if mapping is captured at the existing request.
- Lines 13979–13985: `downloadFile` starts/changes a download; repeated calls are not an appropriate progress read API.

### Recommended architecture

Use the already-running native Bot API client's TDLib updates as the authoritative source. For the smallest integration surface, add a **private, local-mode-only, read-only progress method to the patched Bot API**, called only by the app backend over the existing Compose-private transport. This is a proposed extension, not an official Bot API method.

1. Capture original Bot API file-id alias and resolved TDLib numeric id during the existing getFile callback. Scope by the native Client/Bot plus API child generation. Never decode file_id in Python and never use numeric id across Bots or child generations.
2. Store a bounded in-memory snapshot only for active/briefly completed getFile acquisitions. Update snapshots from existing updateFile callbacks and existing getFile success/error handling; preserve actor ownership and avoid blocking native callbacks.
3. Read-only polling looks up an existing snapshot by Bot/file alias. It must not resolve/start missing Bots, invoke getRemoteFile/downloadFile/getFile, initialize another TDLib client, send logout or initiate a reconnect. Unknown/not-yet-bound returns unavailable/pending rather than false 0% or failure.
4. Reuse the current Bot-scoped authenticated route, restrict this method to local mode/private networking, and prevent it from being externally published. Method routing must explicitly avoid normal remote rate/account initialization side effects. Bot Token remains backend-only; never send it to WebUI or logs. If the existing router cannot enforce an already-active client-only query, use the IPC alternative below rather than quietly starting a new client.
5. Poll all active local observations in one batch approximately once per second (or capped once per job per second at the default three jobs). Local progress polling is separate from Telegram request pacing and must not mutate success/backoff/protection state. Frontend uses its existing task polling; it never contacts Bot API directly.
6. Accept readings only for exact active job/attempt/Bot/file and supervisor generation. Reject late replies after phase change, terminal state, cancellation/retry or generation change. A generic progress writer currently accepts any extant job, so add this guard before any future reuse for asynchronous observations.
7. Keep getFile success and the existing local copy/hash/fsync/publish pipeline as the source of task completion. Progress reaching total or TDLib reporting completed alone never marks the task successful or releases the getFile ownership slot.

### IPC alternative and tradeoff

A native producer could emit bounded snapshots into the existing permission-restricted control volume, or through a new Unix socket/private supervisor endpoint. App validates bot/file hashes, generation, sequence and freshness. This can avoid a custom Bot API HTTP method, but the supervisor cannot observe its child's in-memory file map without a new C++ emission protocol.

File snapshots need atomic replacement, restrictive mode, no token/file-path names, bounded active entries, TTL/cleanup, publication throttling and generation propagation before child startup. A socket additionally needs cross-container mount/permission/authentication and framing/backpressure handling. These are more moving parts than a narrow read-only method using the existing app-to-API connection. Do not repurpose the lifecycle `applied.json` into an unbounded high-frequency file list or write raw unsanitized TDLib updates into diagnostics.

If keeping the official HTTP method surface untouched becomes the stronger maintenance preference, choose the bounded private snapshot channel; both options use the same TDLib observation points and provide the same accuracy. This is a design choice to settle before implementation, not a reason to implement both.

### Accuracy, phases and failure behavior

- Exact positive size: show `240 MB / 600 MB · 40% · 4.2 MB/s` from downloaded_size and a monotonic local delta. Sample numbers are illustrative.
- Only expected_size known: explicitly mark total/percentage as estimated; the estimate can change. Do not display an exact 100% solely from an approximate denominator.
- Neither total known: show `已下载 240 MB · 4.2 MB/s` plus an indeterminate bar. Never invent a total or ETA.
- Cached/resumed downloads can begin above 0% or complete immediately; this is correct. Calculate speed from new byte deltas, not cached bytes divided by elapsed time. Reset speed baselines on generation/attempt changes and handle counter decreases without negative speed.
- Distinguish paused/no-current-throughput from acquisition failure. `is_downloading_active=false` alone is not a reliable task failure. The existing getFile result decides errors.
- No update for a few seconds is not automatically stale: a healthy read can report unchanged bytes and zero speed. Track observation-read freshness separately from last-byte-change time. Unavailable bridge or mismatched generation falls back to `下载中，暂无法获取进度`, leaving the acquisition intact.
- Two jobs for the same Bot/file share native bytes but retain their own attempt/ownership mapping. Observer removal never cancels a shared download. Terminal snapshots have bounded short retention; generation resets invalidate all old snapshots.
- Local network acquisition and destination saving are separate phases. Reset/display stage counters explicitly when entering saving, so a 100% network download does not imply that final storage publication completed; avoid a misleading unexplained 100%-to-0% overall bar.
- Cloud mode continues using existing streamed byte measurements. Unsupported/external stock local API gracefully reports progress unavailable; custom progress is a declared native-build capability, not a hidden fallback compatibility promise.
- Group-level percent can aggregate by bytes only when all included totals are known; otherwise keep file-level progress and completed file counts. Sum bytes across stages/files once, avoiding double counting downloaded and copied bytes.

### Rejected alternatives

| Approach | Assessment |
| --- | --- |
| Scan API cache and stat partially written files | Unreliable: sparse/preallocated files, parallel blocks, mapping ambiguity, cached data and sensitive paths. TDLib explicitly distinguishes downloaded_size from actual file length. |
| Parse native log text | Unstable, often no structured byte data, privacy/verbosity cost and source-version dependence. Keep logs diagnostic. |
| Reissue getFile/downloadFile repeatedly | May duplicate/alter acquisition and complicate slot/request safety; progress polling must be read-only. |
| Additional user-account MTProto client | Unnecessary login/session/account exposure and another download path; existing Bot TDLib already has the data. |
| Fake timer percentage | Misrepresents slow, stalled or cached downloads. |

### Effort and follow-up verification

Moderate cross-layer feature, not merely a frontend label change: C++ patch and regression-build upkeep, private observation contract and generation binding, guarded Python observer, DTO/phase semantics and UI. No new privileged container or TUN is needed. The current native build already compiles pinned C++; a clean rebuild previously took about half an hour, so retain reproducible checked patches rather than an unrestricted upstream fork. Upstream upgrades must re-review Client callback/method dispatch and TDLib field semantics and recompile.

If implemented later, scoped tests should cover fake updateFile samples, aliases and Bot isolation, known/estimated/unknown totals, cache hit/resume, 3+ concurrent jobs, duplicate files, stale reads/generation changes, observer failure without download cancellation, no extra Telegram operations, phase transitions and existing recovery ownership. Real local >20MB downloads should then be observed for increasing bytes and correct saved-file result. None of those implementation or live acceptance actions occurred during this research.

## Related specs

- `.trellis/spec/backend/managed-api-contracts.md`: exact supervisor acknowledgement, freshness, generation and acquisition ownership; no hidden logout replay.
- `.trellis/spec/backend/cloud-api-contracts.md`: phase/mode ownership and local recovery uncertainty.
- `.trellis/spec/backend/request-safety-contracts.md`: request pacing/protection separation and no personal-account login.
- `.trellis/spec/backend/diagnostic-log-contracts.md`: sanitize credentials, file identifiers and paths before logging.

## Caveats / Not Found

- Native progress fields and insertion points were verified against the fixed public source; no native progress method or export implementation exists today.
- Exact local-only method name, active-client router gate, payload cap, timeout, capability negotiation and progress generation handoff need implementation design/compile review. This note deliberately does not promise a ready-made official endpoint.
- No live Telegram traffic, credentials, actual proxy status or runtime media were inspected. No code, specs, tests, deployment, git state or running configuration were changed; only this research file was written.
