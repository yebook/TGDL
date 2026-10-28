# Idle CPU efficiency

## 1. Scope / Trigger

Read when changing background scheduling, empty queue checks, generation tracking
or supervisor heartbeat publication. User prioritized idle CPU on 2026-10-27;
memory/SDK/native tuning is outside this increment.

## 2. Signatures

- `TaskService.freeze_due()` keeps collection and expiry checks at the existing
  scheduler cadence, with a read-only due-work probe before settings decryption
  and the authoritative write transaction.
- `Runtime.counts() -> tuple[int, int, int]`: active, blocked, pending counts.
- `Runtime.notification_due() -> bool`: read-only due-outbox probe, without Bot
  filtering or cached negative results; obsolete foreign-Bot rows must still be
  handled by `next_notification`.
- `Runtime.recover_generation(initial=False)` writes `api-generation.json` only
  on initialization or a changed nonempty generation.
- `Supervisor.publish()` publishes changed status immediately; unchanged status
  is refreshed every 5 monotonic seconds. Applied heartbeat uses wall time.
- `SettingsService.read()` fetches current revision and encrypted values using a
  reusable Core select, then reuses a single decoded entry only when revision,
  complete persisted values and effective environment overrides all match.
- `LocalApiService.read()` uses a reusable Core select without caching state.
- `TaskService.list_tasks()` fetches page associations in three batch queries;
  `_dto` can consume those preloaded media/job/notification collections.

## 3. Contracts

Retain 0.5-second task scheduling and Telegram's existing long polling. Optimize
work per tick rather than delaying intake, grouping, confirmation or transitions.
Empty probes do not acquire SQLite write reservations or cache a negative result
across ticks. Due work is rechecked in the existing transaction; group inbox and
hard-cap rules, soft deletion and expiry comparisons remain unchanged.

Settings decoding cache is bounded to one entry, protected by its own short read
lock and returns deep model copies plus independent source dictionaries. Always
query the current committed row before lookup. Saves/restores, same-revision
encrypted-value changes and added/changed/removed environment overrides must
invalidate reuse naturally; invalid configuration raises rather than returning
the preceding cached entry. API ID/Hash retain their no-environment-override
contract. Cache keys retain encrypted stored data, never mutate it on decode.
No read/cache lock spans network I/O or a write transaction. Core singleton reads
must not retain an ORM identity map or uncommitted connection snapshot across calls.

Task page batching preserves existing item order, DTO fields, progress overlays,
search/filter/order/pagination and soft-deletion semantics. Fetch associations
only for current page request IDs. An empty page skips association queries.
Reference counts without runtime background loops: 20-row authenticated task
page uses 7 SELECTs (5 service + 2 auth), empty page uses 4 (2 service + 2 auth).

Counts use one aggregate restricted to active/pending states so the state index
can exclude historical terminal jobs. No pending jobs means no empty claims or
dispatch-only proxy/protection checks. An actual claim still observes all current
Bot, mode, generation, proxy, concurrency, cooldown and recovery barriers.
Cleanup remains independent of pending downloads: check due cleanup before
expensive generation validation, and validate fully before deleting anything.

An empty due-notification probe also skips notification dispatch-only settings,
proxy and generation work. Retain the 0.5-second notification retry cadence and
full authoritative `next_notification` transaction, Bot ownership, request
deletion/expiry and request-protection checks when due work actually exists.

Unchanged generation markers cause no write. A changed generation waits for
active ownership to drain before recovery; failed writes remain retryable.

Heartbeat coalescing compares the complete status excluding the timestamp.
Command/revision/proxy/state/generation/error changes publish immediately.
Only successful atomic writes advance the cached publication. The 15-second
staleness check, private file permissions and one-second supervisor control loop
remain unchanged. Never extend staleness windows to hide stalled processes.

## 4. Validation & Error Matrix

| Condition | Required behavior |
| --- | --- |
| No due collection or expiry | Read-only probe; no settings decryption/write reservation |
| Due collection/expiry | Existing authoritative transaction and business rules |
| No pending download | Skip dispatch-only work; continue reconciliation and cleanup |
| No verified client | Continue grouping/generation checks, skip dispatch |
| No due notification | Skip send-only checks and write reservation, without caching absence |
| Same generation | No marker rewrite |
| Changed generation with active owner | Defer recovery without losing the change |
| Unchanged supervisor status | At most one publication per 5-second interval |
| Changed status or failed preceding write | Publish/retry on the next applicable call |
| Unchanged saved settings and environment | Fresh row read, reuse decoded model, return independent copies |
| Save/restore or external same-revision value change | Read new committed values on the next read |
| Invalid changed environment | Raise validation error, never reuse older valid result |
| Rolled-back runtime-state update | Subsequent fresh reads retain committed state |
| Task page with deleted/off-page records | Exclude those records and their associations |

## 5. Good / Base / Bad Cases

Good: idle ticks issue fewer queries without changing dispatch latency. Base:
local API still maintains its Telegram connection. Bad: sleeping longer to mask
empty work, bypassing recovery checks, or claiming a container CPU percentage
from a short Windows synthetic sample.

## 6. Tests Required

Assert empty probes avoid writes, due work still freezes/expires, mixed job
states count correctly, actual dispatch keeps safety checks, and local cleanup
runs with an empty download queue. Exercise generation stability/change/active
ownership, unchanged heartbeat throttling, immediate changed publication,
stale detection and write-failure retry. Compare query/write counts per scheduler
tick using isolated real databases and fake network clients; label synthetic
measurements separately from actual Docker/Telegram acceptance.
`test_idle_read_efficiency.py` covers decode reuse, environment invalidation,
mutable-copy isolation, concurrent saves/reads, current runtime state and rollback,
task query counts and DTO parity, search/filter/page association isolation.
Retain configuration-backup restore and connection lifecycle regression coverage.

## 7. Wrong vs Correct

Wrong: cache settings or generation indefinitely, drop OS caches, or impose
arbitrary resource caps to make idle graphs appear lower.
Correct: eliminate redundant reads/writes while retaining current ownership and
failure semantics. Measure before and after under matching conditions.
