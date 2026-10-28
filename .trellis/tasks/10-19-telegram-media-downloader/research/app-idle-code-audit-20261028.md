# Research: Remaining app idle CPU call paths

- Query: Explain why app idle CPU remained about 2.67% after the first optimization, and identify small safe next interventions.
- Scope: internal, read-only code audit; Docker profiling is owned by the separate profiling researcher.
- Date: 2026-10-28

## Findings

### The previous container sample and scheduler benchmark exercised different work

The previous `idle-cpu-docker-acceptance.md` records blank configuration, no Bot client, no native API child and no real Telegram requests. The approximately 42.5% scheduler improvement was measured separately with a synthetic configured local client and **only tick_loop running**. A whole blank application runs poll, tick, notify and diagnostics. Consequently the scheduler reduction does not imply a comparable whole-container reduction. Neither number characterizes the user's authenticated local API, real historical data and browser polling.

### Empty configuration has a surprisingly active polling path

`backend/app/workers/runtime.py:247` poll_loop reads local transition, then calls ensure_client. With managed API enabled and a non-online Bot it sleeps only 0.5 seconds (`:256`).

`ensure_client` (`:154`) does this before the missing-token branch (`:187` onwards):

1. `SettingsService.read` in a worker thread.
2. `LocalApiService.read` for transition.
3. In local mode, proxy_status (supervisor file + runtime row).
4. In managed mode, another LocalApiService.read, and in local mode applied file read.
5. RequestSafetyService.status (a worker-thread hop even when the empty-token branch needs no SELECT).

Only then does it return the unconfigured state. Thus an unconfigured managed cloud-default application pays about four SELECTs and five thread-pool submissions per poll iteration, twice a second. This is static path counting, not a measured profile. Actual local/transition branches differ.

### The idle scheduler still repeatedly rebuilds all settings

`Runtime.tick_loop` (`backend/app/workers/runtime.py:354`):

```
every 0.5 seconds:
  connection_lock -> local_api.reconcile
    local_api.read SELECT
    settings.read SELECT + decrypt + defaults + validation
    idle and same mode -> return
  freeze_due read-only probe SELECT
  recover_generation -> cache/.tgd-api-generation read_text
  no client -> finish
  with client:
    settings.read SELECT + decrypt + defaults + validation (again)
    counts aggregate SELECT
    no pending work -> skip dispatch-only checks
    cleanup_cache -> due SELECT; absent work skips progress_generation
```

Reconcile source: `backend/app/services/local_api.py:367` (settings at `:378`, idle return `:386`). It needs current desired/applied state, but the same tick reads/decrypts settings again at runtime `:371`.

`backend/app/services/settings.py:34` reads Settings row in its own session, decrypts up to three secrets, constructs `UserSettings()` and `.model_dump()`, creates per-field source metadata, scans environment overrides, then validates a second UserSettings model. Template parsing/control-character checking and timezone validation run on every final model validation (`backend/app/schemas/contracts.py:60`, `:85`; `backend/app/core/templates.py:11`). These costs are plausible contributors; their proportions require profiling.

With no client and stable cloud-default managed state, static counts imply roughly 15 SELECTs/sec across tick (6), poll (8) and notify (1), plus about 19 thread-pool submissions/sec. Diagnostics adds minor once/minute work. Count estimates assume no requests, transitions, active work or errors and exclude startup. With a stable verified client, polling is mostly suspended on Telegram's existing 25-second getUpdates (`backend/app/bot/client.py:160`), so the branch mix changes substantially.

`backend/app/db/session.py:26` opens a fresh SQLAlchemy session for each read. Read probes avoid BEGIN IMMEDIATE now, but still incur ORM/session/connection checkout/rollback work. Multiple separate thread-pool hops also wake the event loop repeatedly.

### Browser-visible idle is substantially busier than server-only idle

- `frontend/src/components/WorkspaceProvider.vue:8`: system status every 3 seconds.
- `frontend/src/views/TasksView.vue:19`: task list every 3 seconds through usePolling's default interval (`frontend/src/composables/usePolling.ts:5`).
- Hidden browser tabs use 15 seconds (`usePolling.ts:32`), and multiple tabs multiply requests.
- `backend/app/services/tasks.py:261`: list_tasks executes COUNT and page query, then `_dto` per task (`:272`). `_dto` separately queries MediaItem, Job and Notification (`:222`-`:224`). That is **2 + 3N SELECTs**, so a full 20-task page performs 62 before authentication. This remains true for completely finished, unchanged tasks. Models and per-media metadata are loaded even though only aggregates are displayed. Detail adds one Attempt query per item (`:244`).
- `backend/app/main.py:374`: system_status reads settings, checks every storage root, disk usage, counts, safety and diagnostic stats, then LocalApiService.status reads settings again (`local_api.py:192`) and runtime/applied state. proxy_status independently reads runtime/applied again (`settings.py:59`).
- `backend/app/storage/files.py:75`: root.check reads identity and marker files, performs path/stat/mount checks and disk_usage. Diagnostic status (`backend/app/services/diagnostics.py:230`) checks fixed paths and lstat's log files without scanning history. Root count and mount type affect system-status cost.

No Docker HEALTHCHECK instruction exists in the current app Dockerfile/compose. /health/live is a trivial response (`backend/app/main.py:188`). Browser polling and storage checks cannot explain a sample taken without an authenticated browser, but could be material in the user's normal idle observation.

#### Measured task-list SELECT validation

`research/task_list_query_audit.py` ran with the existing backend Python 3.12 virtual environment, an isolated TemporaryDirectory database, disabled background workers, synthetic expired requests each with one video item, no Bot credentials and FastAPI TestClient. A SQLAlchemy before_cursor_execute listener counted actual SELECT statements only after setup/login and seeding. Each sample ran one direct service call and one real authenticated HTTP request.

| Tasks on page | Direct service SELECT | HTTP SELECT including authentication |
| --- | ---: | ---: |
| 0 | 2 | 4 |
| 1 | 5 | 7 |
| 20 | 62 | 64 |

At N=20, actual HTTP table counts were sessions=1, admins=1, COUNT subquery=1, requests=1, media_items=20, jobs=20, notification_outbox=20. This confirms **2+3N** service / **4+3N** authenticated HTTP query growth. Empty job/outbox tables do not avoid their 20 individual SELECTs. At the current visible-tab 3-second refresh rate a full task page alone issues about 21 SELECTs/second on average, in periodic bursts. This is measured query attribution, not measured CPU time or a predicted CPU reduction.

Reproduction: `backend/.venv/Scripts/python.exe .trellis/tasks/10-19-telegram-media-downloader/research/task_list_query_audit.py`. The script's temporary storage is removed on exit and no real environment credentials were supplied. A Starlette httpx deprecation warning appeared; requests and count assertions all succeeded.

### Small safe next interventions, in priority order

1. **Batch task-list associations**: fetch items/jobs/notifications for all page IDs once each and construct identical DTOs. Page query count becomes five, independent of N, without caching, altered polling or ownership semantics. Test 0/1/20 rows, mixed statuses/missing sizes, soft deletion, notification precedence and fixed SELECT budget. This is the clearest normal-user inefficiency, although not the cause of the no-browser blank-container sample.
2. **Reuse validated settings when the authoritative encrypted row/environment inputs are unchanged**: still read the live revision/values each time, key any parsed snapshot by complete persisted source plus effective environment (not revision alone); never share a mutable UserSettings/list/proxy instance with callers. Return independent copies. Serialize cache publication appropriately; no indefinite caching or bypassing persisted validation. Current ConfigBackupService may update settings too, so invalidation only in SettingsService.save is insufficient. An easier first step is same-tick reuse, provided final publication/claim checks retain their authoritative reads.
3. **Move no-token handling before irrelevant readiness work** in ensure_client, retaining transition pause handling, active-client-owner drain and stale-client cleanup. Useful for setup/blank samples, with less benefit after configuring a real Bot. It must not make a transition look connected or discard in-flight file owners.
4. **Coalesce same-operation reads/thread hops** where no await can interleave state changes. A synchronous read snapshot helper may combine read-only queries in one session/thread. Keep final transactional checks and locks for identity publication, claim, notification and switching. Do not cache applied heartbeat freshness; evaluate current wall time and retain the 15-second stale cutoff.

The best choice between (2) and (3) for the measured blank-container CPU depends on the separate profiler's function/SQL/thread attribution. Do not widen scheduler delays to claim a CPU win: confirmation/grouping/expiry and switching responsiveness have explicit 0.5-second contracts.

## Files Found

- `backend/app/workers/runtime.py`: all runtime background loops and dispatch barriers.
- `backend/app/services/settings.py`: repeated complete settings materialization and proxy status.
- `backend/app/services/local_api.py`: desired/applied transition and supervisor status reads.
- `backend/app/services/tasks.py`: freeze probe and per-row list DTO queries.
- `backend/app/main.py`: synchronous system-status endpoint and authentication.
- `backend/app/db/session.py`: sessions, connections and transactional write serialization.
- `backend/app/schemas/contracts.py`: validators rerun by settings reads.
- `backend/app/storage/files.py`: storage identity safety checks.
- `frontend/src/components/WorkspaceProvider.vue`, `frontend/src/views/TasksView.vue`, `frontend/src/composables/usePolling.ts`: user-page polling cadence.

## Related Specs

- `.trellis/spec/backend/idle-cpu-contracts.md`: retain scheduler latency, negative probes and actual authority checks.
- `.trellis/spec/backend/runtime-contracts.md`: settings revisions, Bot isolation and storage identity.
- `.trellis/spec/backend/managed-api-contracts.md`: publication lock ordering, applied state and stale supervision.
- `.trellis/spec/backend/request-safety-contracts.md`: durable current protection cannot be bypassed by optimization.

## External References

None used. This audit examines the checked-out project and prior local acceptance records only.

## Caveats / Not Found

- Static call/query counts establish unnecessary work, not CPU percentage attribution; no fresh CPU measurement is made here.
- No production files, containers, Telegram clients, credentials, runtime data or git state were modified.
- Research context JSONL was not present. Curated research context, task PRD/design/implementation and the four relevant specs were inspected; implement/check JSONL were intentionally not loaded for this research role.
- Docker Desktop bind-mounted SQLite/filesystem overhead can differ sharply from /tmp tmpfs isolation; the profiler should distinguish these environments.
