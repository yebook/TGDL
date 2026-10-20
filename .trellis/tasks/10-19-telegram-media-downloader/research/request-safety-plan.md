# Research: Telegram request safety

- Query: Reduce avoidable Telegram request bursts/retries and pause revoked Bot credentials, across cloud/local APIs and every request path.
- Scope: internal code, official-reference pointers (not fetched during this research)
- Date: 2026-10-20

## Findings

### Files and current seams

- `backend/app/bot/client.py:26`: every production Bot is constructed here; identity (40), updates (44), getFile (50), send (76), callback answer (88), logout (94). `file_chunks` (56) performs direct aiohttp GET, outside aiogram request middleware; handle its HTTP 429/401 explicitly.
- `backend/app/workers/runtime.py:96`: ensure_client reconstructs clients after connection signature/generation changes. Failed initial identity is retried by poll_loop every five seconds (151–199). Runtime has a single data-directory/process owner, but temporary clients elsewhere require a shared service, not per-client state.
- `runtime.py:355`: notify_loop currently delays only the failed notification on RetryAfter; another pending notification is immediately eligible. `next_notification` (255) increments attempts before actually sending. Forbidden/BadRequest currently makes that notification permanently failed, appropriately not a bot-wide auth failure.
- `backend/app/workers/download.py:256`: local getFile holds acquisition ownership until real completion; avoid introducing arbitrary cancellation/timeout or turning known preflight deferrals into acquisition uncertainty. RetryAfter becomes retryable RATE_LIMITED at 369; Unauthorized becomes non-retryable BOT_UNAUTHORIZED at 371, but currently does not stop other requests.
- `backend/app/main.py:274`: manual validation constructs independent candidate TelegramClient; broad exception wrapping erases useful throttled/paused guidance.
- `backend/app/services/local_api.py:271`: reconcile constructs independent cloud preflight/logout client (327) and target identity client (383). Locks join poll+notify before irreversible logout; persisted logout intent is set at 331, before remote logout. Shared safety preflight must precede intent or distinguish provably unsent failures from uncertain outcomes.
- `backend/app/services/settings.py:36`: connection signature includes token/mode/proxy; unsuitable as cooldown key because changing mode/proxy would bypass limits. Settings save (147 onward) remains offline and must not clear cooldowns on unrelated save or restart.
- `backend/app/db/session.py:27`: short BEGIN IMMEDIATE transaction is existing serialization convention. No sleep/network calls under transactions.
- `backend/app/db/models.py:26`: LocalApiRuntime exists but is migration-state specific; use separate request-safety model and migration 0007 rather than adding unrelated controls to local API JSON.
- `backend/app/main.py:328`, `schemas/responses.py:100`: existing BotStatus + system status can convey pause/wait status. Frontend LocalApiPanel maps unknown states to generic disconnected, so explicit paused/rate-limited text mapping is useful; do not mistake running child for healthy Bot.

### Minimal recommended architecture

1. `services/request_safety.py`: app-owned RequestSafety(db), durable rows keyed by full token digest and scope (bot / chat ID). Never persist actual token, proxy, upstream exception or token-bearing URL. Fields: next allowed time, retry deadline, credential pause, bounded network failure counter/backoff deadline. Use epoch UTC; maximum wins when concurrent responses extend deadlines. Chat rows must be bounded/cleaned eventually.
2. Share service through Runtime/main and pass explicitly to every TelegramClient constructor, including local_api.reconcile and manual validation. aiogram session middleware is an alternative, but direct file_chunks still needs explicit hook. A small central `_request` wrapper makes actual-send preflight/exception observation clear. All calls must pass it, including callback/getMe/getFile/logOut; file request uses equivalent before/observe hooks.
3. Conservative defaults selected by the parent session, not claims of Telegram safety thresholds: bot-wide request start spacing 1s, outbound sendMessage 1.1s per private chat and 3.1s per group. Callback acknowledgements should share bot-wide limit but not wait behind chat message cadence; getUpdates long poll must not hold a global limiter lock for 25 seconds. Reserve starts transactionally, release mutex before network I/O.
4. RetryAfter from any request extends bot-wide retry deadline and, for sendMessage, chat deadline as well. Prevent subsequent requests through newly created clients, mode/proxy changes and app restarts. Do not internally replay requests: durable worker/outbox retains ownership and retry responsibility. A conservative bot-wide wait is safer than guessing scope from exception text.
5. Distinguish normal pacing from remote cooldown/paused: short pacing can await and recheck in cancellable slices; long RetryAfter/auth pause should defer quickly with a known AppError so poll/migration locks are not held minutes and queued notifications/jobs do not consume attempts for requests never sent. Dispatch and outbox selection should check gate before claim/increment; race-time guard remains final defense.
6. Unauthorized(401) pauses that exact credential digest persistently across runtime reconstruction/restart; changing mode/proxy/restarting child cannot unpause it. New token gets independent state. Explicit manual validation may allow one rate-limited probe of the paused token, clearing pause only on successful identity; simultaneous probes must serialize and must not bypass RetryAfter or migration barriers. Save stays offline. Never pause all Bots because an unsaved candidate token failed.
7. Forbidden(403) is commonly user blocking bot: fail its notification; do not declare token revoked. Poll conflict(409) should report another consumer/conflict and back off, never automatically logOut/deleteWebhook. Network errors use bounded exponential backoff with jitter (e.g. 5→10→20→…60s), reset on actual request success, with no token/proxy leak.
8. Cloud file HTTP 429: consume sane Retry-After header when available, fallback conservative wait when absent; update shared cooldown, keep retryable download classification. HTTP401 should pause token; HTTP403 on file URL alone should not automatically imply revoked token. Stream bytes are not separate API calls and should not acquire pacing per chunk.

### Ownership and migration hazards

- Do not cancel real local getFile owners on pause. Stop new claims/requests; let already issued local acquisition/copy/publish finish. Existing local uncertainty rules remain.
- A preflight pause/throttle error before issuing local getFile is known-safe and must not become recovery_blocked. Existing download uncertainty excludes AppError, providing the seam.
- Never automatically retry logout. Gate before persisting logout intent; guard may race with another in-flight response setting cooldown. If guard raises a dedicated provably-not-sent deferral after intent, return to a retryable pre-logout phase, not logout_uncertain. Transport failure after actual send remains uncertain.
- Mode migration still requires old cloud identity/token snapshot and new target token snapshot; each uses its own token gate. Token switch must preserve active-request/cross-Bot/outbox protections. Do not clear old token row while old file owners remain.
- Existing manual validation's connection_lock cannot serialize poll-loop client construction; shared durable reservations are needed. Status polling must read persisted safety state, never trigger identity requests or clear limits.
- Bound state cleanup without deleting a current/active owner's cooldown. Never clear remote RetryAfter on successful unrelated in-flight response; only failure streak reset is allowed.

### Targeted tests

- New `test_request_safety.py`: independent clients, same token across cloud/local/proxy; concurrent sends reserve distinct starts; per-chat fairness; bot-wide RetryAfter persisted on recreated service/database; maximum deadlines and no reset by success; token isolation; invalid credential durable pause; no raw tokens in rows/status/errors.
- Client tests with fake Bot/session: every identity/updates/getFile/send/answer/logout path gated and records RetryAfter/Unauthorized; direct file GET 429/header fallback/401 covered; a long poll doesn't block sends.
- Runtime tests: initial identity network failures back off; persistent 401 stops automatic probes; explicit single validation resumes only on success; cooldown deferrals do not increment notification/job retry counters; callback durable result still persists despite failed acknowledgement.
- Managed tests: preflight wait issues zero logout requests; no false unknown logout from a provably unsent deferral; after actual logout timeout still uncertain; target getMe gated; poll/notify join preserved; active local owners never released on auth pause.
- Existing related suites: `test_cloud_api.py`, `test_runtime_recovery.py`, `test_pipeline.py`, `test_local_api.py`, `test_local_api_review.py`, `test_management.py`; shared client/runtime changes justify widening backend regression once targeted passes. No actual Telegram requests needed.

### Related specs and external references

- `.trellis/spec/backend/runtime-contracts.md`: ownership, offline save, durable outbox and secret-safe exceptions.
- `.trellis/spec/backend/cloud-api-contracts.md`: transport/recovery mode ownership, no direct proxy fallback.
- `.trellis/spec/backend/managed-api-contracts.md`: joins, preflight, uncertainty, persisted cooldown and target validation barriers.
- Official pointers: https://core.telegram.org/bots/faq#my-bot-is-hitting-limits-how-do-i-avoid-this ; https://core.telegram.org/bots/api#responseparameters ; https://core.telegram.org/bots/api#logout ; https://telegram.org/tos/bot-developers . Request spacing reduces avoidable bursts; it cannot guarantee no ban or prove cause of prior personal-account logout.

## Caveats / Not Found

- No runtime secrets read, Telegram requests sent, services restarted, or product files modified.
- No external browsing was available/used in this research; official URLs above are reference pointers, not claims of newly verified current limits.
- Product has no existing request-safety persistence model or user-triggered Bot-auth recovery endpoint. Explicit validation is the smallest available recovery action; its API response must retain meaningful safe error codes instead of blanket validation failure.
- Research role intentionally did not load implementation/check JSONL manifests; code/spec and task planning artifacts were read directly.
