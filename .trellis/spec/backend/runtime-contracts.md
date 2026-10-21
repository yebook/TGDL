# Backend runtime contracts

## 1. Scope / Trigger
Read for changes to backend/app main, services, workers, storage, database and deployment integration. One process owns a data directory using instance.lock.

Task record removal and migration `0005` follow
[task-deletion-contracts.md](task-deletion-contracts.md). Media-file deletion
remains outside scope; task soft deletion retains stored-file and recovery data.

## 2. Signatures
create_app(config: Config | None = None) -> FastAPI. API prefix /api/v1: setup, auth/login|me|logout|password, tasks, tasks/{id}, tasks/{id}/cancel|retry, settings, settings/validate, system/status. Database.transaction uses BEGIN IMMEDIATE; Alembic runs before readiness. Models separate MediaRequest, MediaItem, Job, FileReservation, StoredFile, PublishJournal and Notification.

Migration `0004` adds nullable `MediaItem.caption_snippet` (`String(80)`) for
normalized media-caption naming. Existing rows stay NULL; the HTTP DTO does not
expose this internal field. Startup migrates before intake/readiness.

`app.storage.naming.text_summary(value: str | None) -> str` normalizes bounded
display snippets. `request_summary(request, items) -> str` consumes items already
ordered by message ID. `TaskService.destination(request, summary, timezone) -> str`
builds the new date-first destination only when no destination is frozen yet.

## 3. Contracts
Input schemas live in app/schemas/contracts.py, output/OpenAPI models in app/schemas/responses.py, environment in app/core/config.py. Frontend DTOs are currently maintained by hand against those models. Confirmation defaults 1800 seconds (30 minutes; range 30..86400 seconds), global file concurrency 3 (1..16), automatic retries 3 beyond initial attempt (0..10). Settings PATCH carries revision; blank secrets preserve saved values, explicit clear flags remove them. Environment-owned values are not UI editable. Root IDs must belong to configured storage_roots; no browser-provided arbitrary paths.

WebUI confirmation duration is edited in minutes (0.5..1440); the API, stored
`confirmation_timeout_seconds` and environment override remain integer seconds.
Convert explicitly at the UI boundary and round only a user edit back to seconds.
Opening settings or saving another field must preserve any old integer duration,
including 31 seconds. Persisted durations and already-issued card deadlines are
never reset by the new default. Missing duration settings use 1800 seconds.

Session cookies are HttpOnly/SameSite strict. Mutations require X-CSRF-Token; supplied Origin must match scheme and authority. Passwords use Argon2, saved secrets encryption. Back up master.key together with database. Errors are {error:{code,message,request_id,details}} without raw validation inputs or upstream credentials.

Administrator setup and password change accept 6–20 characters inclusive, without character-class requirements. Reject 5 or 21 characters with 422; test both accepted boundaries (6 and 20). Login retains its existing broader input limit so an older longer password can authenticate and be changed. Never echo rejected passwords in error details.

Settings persistence and online validation are separate. `PATCH /api/v1/settings` saves locally valid Bot Token and allowed user IDs without calling Telegram; it still enforces revision, environment ownership, ranges and secret handling. `POST /api/v1/settings/validate` checks the candidate form against its selected Bot API without persisting it. An unreachable API must not block saving; manual validation may return `BOT_VALIDATION_FAILED` (422). A saved token is not proof of Bot identity or connectivity. Runtime must verify identity before using a changed token and retain cross-Bot active-request and notification ownership protection.

After token changes, gate new polling/dispatch/notifications on the connected token digest matching saved settings. Claim queued jobs for the verified bot_id only; retain the matching client/id snapshot across asynchronous operations. Existing in-flight workers keep their ownership until completion. Never send another Bot's durable notifications through a newly connected client. Regression checks must prove offline saving while identity lookup fails, manual validation without persisted revision/secret changes, and cross-Bot job/outbox isolation.

Persist updates/cursor before advancing polling. Confirmation precedes getFile. Download and notification retries are independent. Stream into a part file, hash and fsync, publish using non-overwriting hard link and persist journal through uncertain boundaries. Existing successful files are not fetched again. Local HTTP cancellation does not prove Telegram stopped getFile: uncertain jobs retain global slots until reconciliation. Root marker/device changes block storage without fallback.

Bot confirmation messages display file sizes using the same 1024-based B/KB/MB/GB/TB
convention as the WebUI: integer bytes, one decimal for larger units, `0 B` for zero
and `未知` for missing size. Do not append a literal byte unit after a formatted
value. Persisted metadata and API numeric size/progress fields remain byte counts.

New request destinations use `YYYY-MM-DD` for single files and
`YYYY-MM-DD/<safe-message-summary-or-fallback>_<request-short-id>` for groups.
Do not add `videos`, `images` or `groups` classification directories. Derive the date from
`MediaRequest.created_at` in the configured archive timezone when freezing the
card. Persist the destination for consent and job creation; never migrate old
monthly/type-prefixed destinations or recompute an existing job path during retry/recovery.
Mixed media in a group continue to share one destination.

For new cards, derive the task title from the first nonblank media caption in
message order, otherwise an original filename or media description. Persist only
a bounded normalized caption summary for this purpose; never associate unrelated
adjacent text-only messages. Keep new task titles as the summary alone, without
an automatically appended request ID. List secondary text and a separate Bot
message line identify the task using its full request ID. Normalize whitespace
and strip control/format characters;
summaries are at most 80 characters including any truncation ellipsis.
Freeze one persisted
`MediaRequest.title`: API/list/detail, Bot confirmation `{title}`, queued/result
messages and `/status` use that same value. Do not rename old requests or rewrite
issued notifications. Group folder names derive from the same summary, sanitize
as one safe bounded UTF-8 path component and retain their short-ID suffix
(`_<request.id[:8]>`, entire folder component at most 180 UTF-8 bytes). Message
text cannot add directory levels or change file naming templates. Re-freezing
after a consent-affecting configuration change must not append duplicate suffixes.

Bot callback feedback is a presentation boundary: `TaskService.confirm` returns
its existing state string and `IngestService.callback` translates it to friendly
Chinese text. `TelegramClient.answer` uses `answer_callback_query` with
`show_alert=True` so both confirmation and cancellation have visible feedback.
Keep user/chat/allowlist/nonce checks before disclosing task status. Invalid or
unauthorized callbacks get generic guidance without request details. Expired
confirm/cancel clicks explain that confirmation expired and media must be resent;
they create no jobs. Repeated confirmation never creates duplicate jobs. A cancel
click on an already queued/downloading request does not cancel through this old
confirmation card: report its real state and direct the user to WebUI cancellation.
Do not show internal English state names or claim a cancellation that did not
occur. A failed callback acknowledgement must not undo durable business changes.

Proxy desired.json revision is separate from applied.json acknowledgement. Saving is not activation; disabling in a proxy-capable stack requires redeployment. Live generation reconciliation must not delete or reset files owned by active workers.

## 4. Validation & Error Matrix
| Condition | Result |
| --- | --- |
| Missing session | 401 |
| Wrong Origin/CSRF/bootstrap | 403 |
| Stale settings revision | 409 |
| Invalid schema/range | 422; no secret echo |
| Existing destination | Never overwrite |
| Missing/changed root marker | Fail or recovery block; no alternate directory |
| Desired-file write failure | Persisted and applied states reported separately |
| Expired confirmation/cancel button | Visible expired guidance; no jobs created |
| Repeated confirm / cancel after confirmation | Report actual state; no duplicate jobs or false cancellation |
| Invalid/unauthorized callback | Generic visible guidance; no task detail disclosure |
| Missing caption / migrated NULL snippet | Original filename or media description fallback |
| Caption contains path/control/reserved syntax | Normalize display text; sanitize group folder as one bounded component |
| Existing frozen title/destination | Keep issued title/path, including during re-confirmation/retry |

## 5. Good / Base / Bad Cases
Good: a mixed group confirms once under YYYY-MM-DD/<summary>_<short-id>, retries preserve its directory. Base: a single image archives directly under YYYY-MM-DD after Bot confirmation. Bad: link-only media, unauthorized callback or symlink escape must not obtain/publish media.

## 6. Tests Required
From backend: uv run ruff check app tests; uv run mypy app; run pytest on affected test files/selectors and directly related contracts. Expand regression only when shared infrastructure, failures, unresolved concerns or release gates justify it; see index.md. Temporary databases/roots and fake Telegram services must assert persisted side effects for duplicates, journal crashes, cancellation, notification retry, missing files and root changes. Real Telegram >20MB, fnOS and TUN evidence is separately required in docs/verification.md; mocks cannot pass those release gates.

For callback/date changes, cover both buttons before/after expiration, repeat
clicks, authorization and visible alert payload; assert job counts and durable
state. Check daily video/image/group paths at timezone day boundaries, and retain
old monthly/type-prefixed request/job paths across confirmation, retry and recovery.
Caption tests cover reordered group arrivals, blank/long/control/unsafe text,
fallback names, duplicate titles and Bot/API consistency. Migration tests retain
old rows with nullable summaries. No browser suite is needed when frontend
contracts/assets remain unchanged.

## 7. Wrong vs Correct
Wrong: release the global slot as soon as an HTTP await is cancelled, or show proxy active immediately after writing desired.json. Correct: retain uncertain upstream ownership until reconciliation; show activation only from the supervisor acknowledgement matching the desired revision.

Local TUN proxy desired/applied revisions identify network configuration, not
every settings save. Preserve the existing desired payload and revision when
proxy values match; changing log retention or templates must not reload sing-box
or repeat connectivity probes. Proxy status compares the network acknowledgement
against the effective proxy payload revision, which may differ from global
settings revision. Actual proxy changes advance the network revision; verify
unchanged-file/active-ack behavior and actual-change pending behavior in tests.

Wrong: rebuild an existing task name/path from current settings, or use a caption
as a relative directory path. Correct: persist the bounded caption summary,
freeze the shared title and sanitized date-first destination before confirmation,
and keep existing task/job snapshots across upgrades and retries.

## Local UI-only startup boundary

Deployment update verification: compare mounts as a mapping keyed by container
Destination rather than Docker's non-stable array order. Preserve the old image
using the running container's inspected Image identifier, not an intermediate
build digest. For private named control volumes on Windows, use a stopped,
read-only volume helper and stream the backup archive to the host if host bind
write permissions reject the helper; never loosen control-volume permissions.
Back up before starting a version with age-based log maintenance, and verify
database integrity and a private media hash manifest without printing secrets
or media filenames. Do not use Telegram probes or manual log clearing as an
application deployment smoke test.

- Scope: Windows Docker preview before API credentials exist; see `docs/local-run.md`.
- Command: `docker compose --env-file .runtime/local-docker/.env -f .runtime/local-docker/compose.web.yaml up -d --no-build` after building `tgd-app:0.1.0`.
- Contract: bind loopback only; isolated Docker data/download/cache roots; API base remains `http://bot-api:8081`. Missing API credentials must never be replaced with fake values. Keep the Docker roots when switching to full Compose; do not reuse native Windows storage identity markers.
- Validation: default Compose parses without application credentials and starts only the app; the optional `local-api` service entrypoint rejects missing application credentials. Healthy UI does not mean Telegram-ready.
- Cases: good = initialize UI then switch stacks with preserved data; base = uninitialized UI with live/ready OK; bad = two stacks writing one database or claiming real download success from readiness.
- Required checks: app image build, Compose config quiet, live/ready OK, setup status and browser initialization page. Real Telegram >20MB remains a separate gate.
- Wrong: show parsed Compose output containing credentials or copy native QA storage. Correct: use `config --quiet`, inspect only credential presence, and keep dedicated Docker data.

Deployment portability: public docs and build configuration use project-relative paths rather than developer-machine locations. `.env.example` defaults to `./data`, `./downloads`, `./bot-api`, and `./network`; Compose resolves these from the selected Compose file's directory, not the env file's directory. Existing NAS shares may use user-supplied absolute host paths; fixed container paths remain `/data`, `/downloads`, and `/var/lib/telegram-bot-api`. Exclude root runtime folders from Git/build context without excluding `deploy/bot-api` or `deploy/network` source. Do not rewrite an existing deployment's paths or storage identity when changing template defaults.
