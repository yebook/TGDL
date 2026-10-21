# Research: configurable diagnostic log retention and manual cleanup

- Query: Add configurable automatic cleanup and WebUI manual clear while preserving diagnostics safety and existing settings.
- Scope: internal; source, tests and project contracts only; no runtime data, secrets, database, Docker or Telegram access.
- Date: 2026-10-21

## Findings

### Files found and current code patterns

- `backend/app/services/diagnostics.py:95`: `DiagnosticLog` owns fixed `logs/events.jsonl` plus `.1`–`.3`, 10 MiB per file; `threading.RLock` serializes writes, flush, rotation and export. Current retention is size/count only, no age policy or clear method.
- `backend/app/services/diagnostics.py:132`: `_path()` defines exact names; `_check()` rejects symlinks/nonregular files/hardlinks and `_open()` checks file descriptor identity and `O_NOFOLLOW` where available. Reuse these boundaries; never enumerate arbitrary files for deletion.
- `backend/app/services/diagnostics.py:184`: repeated failures are held in `repeated`; `_flush()` at line 205 can write summaries later with a NEW timestamp. Deleting files alone would resurrect pre-clear history. `failures` at line 101 also carries recovery state across calls.
- `backend/app/services/diagnostics.py:223`: export reads at most four bounded files, reprojects strictly allowed fields, returns only 1000 events and can drop oversized/corrupt lines. Do NOT implement retention by round-tripping `export()`, which would erase valid recent history beyond the export window.
- `backend/app/schemas/contracts.py:36,87`: `UserSettings` and `SettingsPatch` define settings; `backend/app/services/settings.py:42` merges defaults with existing JSON values, while line 114 merges only submitted fields and line 148 persists with revision check. A new scalar setting needs no DB migration and must not rewrite unrelated old values.
- `backend/app/services/settings.py:65`: `TGD_*` overrides drive `sources`/`editable`. Add new numeric field to JSON numeric environment parsing (current list at line 70), validate before candidate save; environment-owned settings remain read-only.
- `backend/app/services/settings.py:36`: Bot client signature covers only token/mode/proxy, so retention changes should neither reconnect nor probe Telegram.
- `backend/app/workers/runtime.py:34,58,230`: sink creation, runtime loop registration and `tick_loop`. Existing tick can repeatedly skip work when managed API migration is paused. Cleanup must run independently of Telegram connection and migration state, including zero-traffic idle periods.
- `backend/app/main.py:317,336`: existing PATCH settings and GET `/system/logs`; synchronous export runs in worker pool. New disk-heavy clear endpoint should likewise use sync handler or `asyncio.to_thread` and never hold a DB transaction during log I/O.
- `backend/app/main.py:157`: auth validates admin session and CSRF for mutations; supplied Origin checked by middleware at line 123. A clear mutation should use the same dependency, not an unauthenticated filesystem utility.
- `backend/app/schemas/responses.py:159`, `frontend/src/api/types.ts:55`: current status fields are ready/error, sanitized message, max bytes, backup count. Extend matching backend/frontend DTOs for effective retention.
- `frontend/src/components/DiagnosticLogPanel.vue:9`: export uses existing request/session handling, busy/stale/unmount guards, transient ElementPlus messages. Natural place for clear button and retention summary.
- `frontend/src/views/SettingsView.vue:15,54,78`: reactive full settings DTO defaults; assign clones saved settings, payload emits changed editable values, 409 preserves edits. Add numeric retention control using same pattern; avoid an independent settings saver in system panel that can overwrite unsaved settings/revisions.
- `backend/tests/test_diagnostics.py`: strict projection, repetition, bounded concurrent rotation, error recovery, symlink safety, authenticated export and real transport/worker seams. `backend/tests/test_diagnostic_hardening.py` checks disk I/O after business commit. `frontend/e2e/diagnostic-logs.spec.ts` is existing isolated actual REST/browser seam.

### Suggested minimal contract (main session to finalize)

- `diagnostic_log_retention_days: int` in `UserSettings` and optional PATCH; user-confirmed default 7, proposed range 0..3650 (strict integer preferred; reject booleans/fractions). `0` disables AGE cleanup only, not the existing 40 MiB approximate total storage cap. Day means elapsed 24 hours from each event UTC timestamp, independent of naming timezone.
- Preserve explicit saved value including zero; only missing legacy value receives default. Optional environment override `TGD_DIAGNOSTIC_LOG_RETENTION_DAYS` should be numeric and read-only like other settings.
- Add `retention_days` to diagnostic status, reflecting SAVED effective policy, not unsaved form. Avoid promising exact 7-day storage: rotation can remove records sooner.
- Suggest `DELETE /api/v1/system/logs` with admin/CSRF/same-origin protection and no path/body parameters. Return normal diagnostic status (200) or `{cleared: true}`; choose one stable contract. Failure sanitized `DIAGNOSTIC_LOG_UNAVAILABLE`/503, never raw filesystem error.
- Manual action is all diagnostic history, not only age-expired records. WebUI confirmation should state irreversible diagnostic removal, downloads/settings/tasks unaffected; success transient. Disable export and clear together during either action, guard duplicate/late work and refresh status after success.

### Cleanup design and failure boundaries

1. Keep all sink operations under the existing RLock. Config application and periodic sweep must share it. No business lifecycle lock or DB transaction held over filesystem work.
2. Use exact `_path(0..backup_count)` allowlist only, no directory recursive deletion or generic glob. Reject unsafe logs directory, symlink/hardlink/nonregular targets before mutation. Never touch DB, master key, Telegram cache, downloads or unrelated `logs/*` files.
3. Manual clear must empty `repeated` and `failures` under lock, so later export/shutdown/connection recovery cannot materialize pre-clear summaries. Do not flush stale summaries before clearing. New concurrent events may legitimately appear immediately after completion; document this instead of promising an eternally empty export.
4. Automatic age cleanup should use each validated event timestamp, not file mtime: files mix old/new records and export may be out of timestamp order. Remove timestamps older than cutoff; keep equality at cutoff. Invalid timestamps/oversized/corrupt records can be discarded rather than retained forever. Parse bounded line chunks with the existing projection; do not re-emit raw JSON fields.
5. Bound per-sweep work by the four configured files and existing size policy, never unbounded `read_text()` on malicious oversized files. Prefer atomic replacement through a FIXED private temporary file verified with the same no-follow/identity requirements, removing it safely on failure. Never `open(...,'w')` an unchecked temp path, and never silently truncate unrelated files. Per-file atomic replacement avoids partial JSONL on crash; whole-collection atomicity is not required if explicit failure is returned.
6. In-memory dedupe state must expire with policy too: purge old pending summary/failure metadata or clear aggregate buffers at a sweep boundary so an old payload cannot be retimestamped into retained history. If preserving aggregates, track the original observation times and drop those older than cutoff. Manual clear can simply reset all buffers.
7. Sweep on startup, after committed retention changes and periodically while idle (suggest hourly bounded worker-thread task). A dedicated runtime maintenance loop avoids skipped cleanup when tick is paused. On cancellation ensure lifecycle joins the maintenance task; no leaked background process. Configuration save remains offline and durable even if cleanup fails; sink failure must be visible rather than rolling back saved settings or changing downloads.
8. Sink error currently clears on successful `_write`; ensure cleanup failure remains meaningfully visible until next successful sink validation. An age setting of 0 must skip age deletion but cannot disable safe-size rotation. Clamp scheduling against backward clock movements or use monotonic intervals while UTC wall clock determines cutoff.

### Focused validation recommendations

- Preserve recent records across active + all archives; remove only old records from mixed files; exact cutoff; disable 0; legacy default; environment ownership; stale revision; no Telegram probe on save.
- More than 1000 recent events survive sweep (export window is not retention capacity); corrupt/oversized inputs bounded and no sensitive fields copied to replacement.
- Pending repeated errors cannot return after manual clear/export/flush/shutdown; age-expired pending summary cannot be made new by a later timestamp.
- Concurrent emit/rotation/export/clear/sweep produces valid bounded JSONL; events after clear are legitimate; unrelated sentinel files untouched.
- POSIX symlink directory/active/archive/temp and hardlinks rejected; external sentinel contents intact; inaccessible/full disk returns sanitized error and durable task result unaffected.
- Idle sweep works with no token or network; paused migration does not inhibit cleanup; startup loads saved age policy before normal emission.
- Authenticated clear succeeds; no session 401, wrong/missing CSRF 403 and cross-origin rejection; never changes request safety state or causes Bot reconnection.
- Desktop/mobile actual REST browser: confirm/cancel clear, shared busy controls, transient visible messages, retention save/read/reload, stale status and unmount protection; use synthetic logs and fake Telegram only.

## Related specs and references

- `.trellis/spec/backend/diagnostic-log-contracts.md`: fixed private files, bounded work, strict safe projection, no business side effects.
- `.trellis/spec/backend/runtime-contracts.md:3`: one process owns data dir via instance.lock; worker-thread RLock is sufficient inside supported deployment, not a multi-host filesystem lock.
- `.trellis/spec/frontend/ui-contracts.md`: revision/editable handling, transient messages and diagnostic export on explicit action.
- External references: not required; no external API dependency introduced. Python standard library `os.open`, `os.replace`, `datetime`, `threading.RLock` are current project patterns.

## Caveats / Not Found

- User confirmed the default as 7 days. Main session favors range 0..3650 and DELETE /system/logs, with 0 disabling age cleanup only. These are implementation contracts, not existing behavior.
- No `.runtime`, credentials, user database or live services were inspected; no real history was accessed.
- Existing checks are protective against substituted files; full hostile concurrent parent-directory replacement requires dir-fd traversal on POSIX beyond current pattern. Keep proposed cleanup at least as strict as current sink and test fixed temp paths; avoid claiming comprehensive adversarial filesystem isolation.
- UI placement should follow main session decision. Default retention can delete old logs on upgrade; document policy clearly and retain the 0 opt-out without disabling bounded rotation.

## Final handoff contract (2026-10-21)

User confirmed default 7 days. Main session selected Settings field diagnostic_log_retention_days (0..3650, 0 disables only age pruning); DELETE /api/v1/system/logs protected by auth + CSRF and existing Origin middleware. Clear only fixed events files and reset repeated/failures caches; periodic age cleanup runs even without a Bot and uses each event timestamp rather than mtime. Existing size rotation remains enabled. Response shape and precise maintenance interval can follow implementation judgment; align backend schema, frontend DTO and docs.

