# Research: Remaining idle application CPU

- Query: Attribute remaining app idle CPU after the 2026-10-27 optimization; focus CPU, not memory.
- Scope: Internal source inspection and isolated Docker measurements.
- Date: 2026-10-28 (Asia/Shanghai).

## Findings

### Environment and method

At investigation time `docker ps` listed only unrelated `pgvector-db`; no TGDL application or API container was running. No existing container, real settings, database, download, credential, or volume was modified. The final local app image was available: `b1b30a37690397aadb25ad6dd732e286cb2de553bd7198d038e6c16b4eb5043d`.

Each test created a separate `docker run --rm -i --network none --read-only --user 1000:1000 --cap-drop ALL --tmpfs /tmp:rw,nosuid,size=64m` container. The packaged application lifespan initializes a fresh SQLite database; its four actual background loops run manually with `run_background=False`. That configuration prevents duplicate loop startup, not loop replacement. Two seconds warm up, then at least 30 seconds measured. No Uvicorn HTTP server, browser, Telegram connection, Docker exec counter helper, or live Bot request is included.

The reproducible script is `research/idle_profile_harness.py` beside this report. Pipe its UTF-8 contents to `docker run` with `--entrypoint python b1b30a376903 -`. Environment variables: `BENCH_MODE=full|none|no_poll|no_tick|no_notify`, `BENCH_CONNECTED=1` for a fake verified local client, `BENCH_SECONDS=30`. Optional `BENCH_PROFILE=1` runs a separate synchronous method profile **after** the CPU measurement interval.

Process user/system CPU comes from Linux `resource.getrusage(RUSAGE_SELF)` and includes all process threads. Individual `asyncio.to_thread` operations use `time.thread_time()` around the actual worker call, so idle executor waiting is excluded. These are process CPU counters, not cgroup CPU counters; unrelated daemon and host virtual-machine work is not included. SQL execution counts use SQLAlchemy's `before_cursor_execute` hook. The hooks themselves add small measurement overhead.

### Whole-runtime samples

| Configuration | Wall seconds | User CPU seconds | System CPU seconds | Total CPU seconds | Approx. one-core CPU |
| --- | ---: | ---: | ---: | ---: | ---: |
| All four loops, blank settings | 30.0012 | 0.385133 | 0.138817 | 0.523950 | 1.75% |
| Same full runtime, independent repeat | 30.0023 | 0.427251 | 0.118467 | 0.545718 | 1.82% |
| No background loops (lifespan initialized) | 30.0305 | 0.000196 | 0.000013 | 0.000209 | 0.0007% |
| Suppress poll loop only | 30.0008 | 0.261825 | 0.047676 | 0.309501 | 1.03% |
| Suppress tick loop only | 30.0010 | 0.259636 | 0.066679 | 0.326315 | 1.09% |
| Suppress notify loop only | 30.0007 | 0.387126 | 0.077519 | 0.464645 | 1.55% |
| All loops, fake connected local client | 30.0025 | 0.527622 | 0.094772 | 0.622394 | 2.07% |

The suppression rows identify where work originates; their differences are not additive exact percentages. Runs were single samples, not statistically randomized trials, and some overlapped another isolated profiling process on the host. The repeated unprofiled full-runtime row supports the approximate scale. The no-background control is an initialized lifespan/event loop, not a running HTTP-server baseline.

The fake local client uses the real runtime `ensure_client`/tick/notify and database methods, but `updates()` only awaits 30 seconds and returns an empty list. Its matching supervisor acknowledgement is refreshed every five seconds, and no network exists. It is not an authenticated Telegram acceptance result or a measurement of native TDLib.

### CPU attribution from the initial blank-settings sample

| Worker operation | Calls / 30 seconds | Inclusive worker CPU seconds |
| --- | ---: | ---: |
| LocalApiService.read | 266 | 0.176210 |
| SettingsService.read | 118 | 0.084986 |
| TaskService.freeze_due | 59 | 0.047132 |
| Runtime.recover_generation | 59 | 0.006157 |
| RequestSafetyService.status | 59 | 0.002241 |

There were **443 SELECT statements in 30 seconds**, approximately 15 per second, despite no Bot and no task. These exactly match the 266 runtime-state reads, 118 settings reads and 59 empty freeze probes. Tokenless request-safety status does not access its database.

These top-level worker operation buckets total approximately 0.317 CPU seconds. They are inclusive method times, not exclusive costs of SQL/validation subfunctions. About 0.207 seconds of the 0.524 total remains outside these wrapped worker calls: coroutine/event-loop work, executor dispatch/callbacks, locks, SQL event hook dispatch outside buckets if any, and measurement overhead. This remainder is not a direct CPU attribution to one function.

The unconfigured poll path is particularly wasteful: `Runtime.poll_loop` at `backend/app/workers/runtime.py:247` retries `ensure_client` every 0.5 seconds when managed and not online. `ensure_client` at line 154 reads settings and runtime state, and may read managed state/safety again **before** its missing-token branch. Tick simultaneously calls reconciliation every 0.5 seconds. Notify also reads transition state before discovering there is no client.

For the fake connected-local sample, the function mix changes substantially: 122 state reads, 120 settings reads, 60 notification probes, 59 task count aggregates, 59 freeze probes and 59 cache-cleanup due queries. Total **484 SELECTs** plus one empty intake persistence transaction during the long-poll return. Main worker CPU buckets were state reads 0.120 seconds, settings 0.097, notification probe 0.047, counts 0.045, freeze probe 0.044, cache due probe 0.031. A genuine long poll already avoids the frequent no-token `ensure_client` retries; simply optimizing the missing-token path will not remove these connected-idle scheduler costs.

### Separate synchronous profile: ORM overhead dominates SQLite execution

After a fresh 30-second unprofiled interval, a single-thread default-clock cProfile run called `state.read`, `settings.read`, and `freeze_due` 200 times each. This avoids profiling only the event-loop thread and missing worker functions. It also avoids cross-thread profiler timer artifacts. This profile is not used as the measured CPU percentage.

- 600 SELECTs: raw `sqlite3.Cursor.execute` cumulative wall time approximately **0.00590 seconds**.
- SQLAlchemy `Session._execute_internal`: **0.14489 seconds cumulative** across those queries.
- `LocalApiService.read`: **0.07876 seconds** / 200 calls.
- `SettingsService.read`: **0.10260 seconds** / 200 calls.
- `TaskService.freeze_due`: **0.13009 seconds** / 200 calls.
- SQLAlchemy session close: **0.03067 seconds cumulative**, a subset of the above operations.
- SQL cache-key generation: **0.01928 seconds cumulative**, another overlapping subset.
- Pydantic `validate_python`: **0.01782 seconds cumulative**; this includes validated default construction and final settings validation, not all settings-read cost.

Therefore, this synthetic tmpfs case is not an expensive SQLite scan or raw disk throughput problem. It is mostly repeated small ORM/session/query-building work, settings materialization and frequent thread/event-loop wakeups. Pydantic/template validation contributes, but claiming it alone dominates the idle CPU would overstate the measured evidence.

### Realistic next optimization options

1. Remove redundant runtime-state/settings retrieval within each scheduler iteration. Build a coherent short-lived read snapshot in one synchronous worker operation where safe, rather than separate sessions/executor crossings for the same unchanged state. Revalidate current operation/credentials/generation at authoritative claim/publication boundaries; do not carry an unchecked snapshot across network operations or silently weaken barriers.
2. Reuse validated settings only with explicit revision-aware invalidation for settings saves, restore and environment semantics. Decrypting/revalidating unchanged settings on every idle tick is unnecessary, but an indefinitely cached configuration would be incorrect. Validate defaults once rather than constructing a fully validated default model for every read where feasible.
3. Missing-token poll/notify paths should avoid connection-only checks and repeated state reads where their safe absence is already established. This targets the actual blank-config measurement; do not promise the same benefit in connected local mode.
4. Connected-idle tick still does several separate empty-work queries every 0.5 seconds. Consolidating read probes and scheduling next deadlines can reduce work materially. Keep due collection/expiry/notification cadence and immediate connection-change wakeup; larger idle backoff or an event-driven redesign is a separate behavior decision, not an invisible tuning constant.
5. Browser-driven task/status polling is excluded here. Another research agent is auditing that separately. It can add CPU on top of these numbers, particularly with task history, and must not be described as part of this no-browser baseline.

## Files Found / Code Patterns

- `backend/app/workers/runtime.py:247`: polling/connection refresh and no-client delays.
- `backend/app/workers/runtime.py:354`: reconcile, freeze, generation, counts and cleanup each scheduler tick.
- `backend/app/workers/runtime.py:462`: notification transition and due-work polling.
- `backend/app/services/local_api.py:171`: fresh ORM session/singleton load per runtime state read.
- `backend/app/services/local_api.py:367`: reconcile reloads runtime state and settings even in stable idle.
- `backend/app/services/settings.py:34`: new session, settings row load, decryption, defaults, environment lookup and validation per read.
- `backend/app/db/session.py:12`: SQLAlchemy engine/session factory and SQLite WAL pragmas.
- `backend/app/services/tasks.py:126`: empty freeze probe still constructs and executes ORM query.
- `research/idle-cpu-docker-acceptance.md`: previous Windows bind-backed running-container samples, different from current tmpfs harness.

## Related Specs / External References

- `.trellis/spec/backend/idle-cpu-contracts.md`: retain 0.5-second effective scheduling, no indefinite negative caching, preserve generation/safety/cleanup barriers.
- `.trellis/spec/backend/managed-api-contracts.md`: current operation, credential, proxy and generation verification boundaries.
- `.trellis/spec/backend/runtime-contracts.md`: durable database ownership and instance locking.
- Python 3.12 standard-library contracts: `resource.getrusage(RUSAGE_SELF)`, `time.thread_time`, `time.monotonic`, `cProfile`, `pstats`; documentation behavior used locally, no external browsing or dependency installation.

## Caveats / Not Found

- No TGDL container was running; the user's NAS process and authenticated connection were not profiled.
- The prior 2.67% was a running container using Windows bind mounts, whereas this 1.75–1.82% baseline uses tmpfs and no HTTP server. It is not evidence of a fresh production improvement. Windows bind filesystem and deployment differences may contribute; no matched bind-vs-tmpfs experiment was performed, so attribution to Windows I/O remains unproven.
- Native Bot API/TDLib CPU and Telegram network costs are excluded. Fake long polling measures application scheduling only.
- Two attempted concurrent per-worker cProfile experiments produced impossible negative cumulative timings when combined with custom per-thread timers; those results were discarded, never used for attribution. The accepted profile is the separate synchronous default-clock run above.
- No production code changed, no image rebuilt, no live Telegram request performed, and no unrelated service altered. No dependency installation or native compilation occurred. All test containers auto-removed.
