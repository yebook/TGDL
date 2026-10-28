# Research: API switching and Bot status projection

- Query: API mode switches sometimes leave abnormal Bot status even while real message delivery works; improve lifecycle and user-facing status.
- Scope: internal source audit, no live Telegram, credentials, runtime writes or container rebuild.
- Date: 2026-10-28

## Findings

### Confirmed persistent identity-loss path

- `backend/app/workers/runtime.py:234` publishes a verified client, signature, generation and username together.
- `runtime.py:289` handles a polling `AppError` by replacing `bot_status` with `username=""`, but retains the verified active client/id/signature.
- On the next iteration, `runtime.py:192` reuses that same client and sets only state/message. Successful polling at `runtime.py:285` also changes only state/message. Neither restores the username.
- `frontend/src/api/systemSummary.ts:56` requires both online state and username for Bot readiness. Thus a recovered, functioning client can indefinitely display unknown identity/readiness until replaced. This exactly fits the user's clarification that actual sending/receiving works while the interface is abnormal.
- Fix boundary: retain a separate verified identity snapshot bound to the exact published client/signature/generation; project or restore it only if those still match current authoritative settings and applied generation. Do not retain an old username across token replacement, or merely read the last `BotIdentity` row. No additional getMe or native restart is necessary.

### Existing durable switching sequence

- `backend/app/services/local_api.py:44` `schedule_settings`: offline save atomically schedules draining with operation ID, encrypted snapshot and target. Busy connection mutations reject; unrelated saves preserve snapshots.
- `local_api.py:367` `reconcile`: drains owners and consumer locks; cloud->local preflights identity, records logout intent, executes logout once, then starts child; local->cloud respects persistent cloud availability before stop/verify.
- Supervisor command acknowledgement/config revision/proxy/generation barriers precede identity verification. Verification success precedes committing applied mode. Unknown logout, protection and timeout recovery are durable and must remain unchanged.
- `local_api.py:512-531` verifies via temporary client, remembers identity, closes it, then commits phase idle/applied target. The runtime's published client is still absent and `bot_status` remains connecting; `poll_loop` subsequently creates/verifies a consumer client. This is a genuine short readiness gap, not evidence of a failed switch. UI must show applied API + connecting Bot during the gap. Avoid weakening final identity-publication checks merely to eliminate it.
- `runtime.py:391` (local reconcile terminal-phase branch) writes transition failure into Bot connection state every tick. It conflates API operation failure with Bot transport failure and erases identity. A display projection should prioritize lifecycle and distinguish these concerns rather than manufacturing transport evidence.

### Status response can mix observations

- `backend/app/main.py:374-398` reads settings, does disk/root/queue work, copies runtime.bot_status, separately calls local_api.status (which rereads settings/state) and proxy_status. Saves/reconcile/publication may interleave these steps. Result can combine current protection with previous Bot username, and a newer applied mode with older Bot status.
- Existing `connection_publication_lock` serializes settings save, local API update/action and final verified-client publication (`runtime.py:215-235`, managed API contract). A short synchronous snapshot helper can use it for settings/state/client identity coherence; do not hold across filesystem checks, network I/O or await. Bot transport-state mutation itself also needs consistent replacement/snapshot semantics if it is included in that contract.
- Reading saved token and comparing it to the currently published signature must suppress obsolete readiness/username immediately, even before runtime replaces an old client retained for file owners.
- Request protection is credential-keyed and can be overlaid independently, without mutating the verified identity or clearing real cooldown/auth pause.

### Frontend projection mismatch

- `frontend/src/api/systemSummary.ts:22-63` prioritizes switch phases and request protection, distinguishes target/applied mode and stale status. `frontend/src/components/LocalApiPanel.vue:28-33` instead maps raw Bot state with only safety precedence, so a draining switch can show a stale connected Bot in the panel while topbar says receiving paused.
- `frontend/src/api/localApi.ts:28-30` calls starting phase 'starting local API' even for target cloud, where the actual command is stop. Backend `MESSAGES.starting` similarly describes applying local service configuration; use target-aware neutral connection text.
- `LocalApiPanel.vue` always emphasizes local managed process state even when cloud is selected/applied and the child is intentionally stopped. Show the active API connection first, managed process state as secondary local detail; a cloud-mode stopped child is normal.
- `frontend/src/composables/usePolling.ts:15-41` already aborts and generation-checks superseded requests. Do not claim missing polling cancellation. `SettingsView.vue:55-58,146-147` mutation patches immediately refresh, superseding old poll responses, and saved-revision awaiting guards are already present. Main remaining inconsistency is backend response coherence and divergent projection functions.

## Focused implementation proposal

1. Introduce runtime-owned verified identity metadata with signature/generation/client association. Restore same-client identity after transient errors; clear or hide it on replacement, token change, absent-client close and obsolete generation. Preserve failures as failures until actual recovery; retaining identity never means current transport is healthy.
2. Add authoritative Bot snapshot/projection using current saved connection signature, runtime client metadata and local lifecycle; bind protection to current token. Compose a coherent local API/Bot/proxy connection snapshot with a short lock, while keeping disk/log/queue inspections outside it. Avoid any status-triggered upstream call, side effect or new high-frequency queries.
3. Reuse a shared frontend Bot/API projection in topbar and connection panel. Show current/target API, ongoing operation, then transport/protection; keep transition failures and stale observations actionable and consistent.
4. Keep transition safety and pacing unchanged. Reusing the temporary verified migration client would require transactional publication/commit plus command-generation checks; defer that larger lifecycle change unless separately justified.

## Suggested scoped tests

- `backend/tests/test_request_safety_runtime.py` / `test_idle_runtime.py`: successful client -> one transient AppError -> same client healthy -> username/readiness restored without another identity request; true failure remains error; current token change immediately hides old identity; removal never restores it.
- `backend/tests/test_connection_recovery.py`, `test_local_api.py`, `test_cloud_reconnect.py`: verify pending transition cannot publish old client readiness; idle-after-target-verification but before runtime consumer is connecting; target client/generation publishes current identity; failed operation and protection remain distinct. Preserve existing drain, cooldown, uncertainty and retry tests.
- API status regression: settings signature mismatch never exposes old username; status snapshot cannot mix applied mode/proxy revision/current Bot identity across a controlled concurrent save/publication boundary.
- `frontend/tests/systemSummary.test.ts`, `localApi.test.ts`: same projections in panel/topbar for draining, cooldown, verification, failure, stale, ready, identity unavailable, protection wait; stopped unused local service remains normal for cloud; starting wording target-aware.
- `frontend/e2e/managed-api.spec.ts`: scoped offline synthetic switching presentation if existing harness supports it, no real Bot probes. Existing shared polling generation tests should remain unchanged unless new mutation handling is introduced.

## Files Found / Related Specs

- `backend/app/workers/runtime.py`: client verification/publication, polling recovery and mutable Bot status.
- `backend/app/services/local_api.py`: durable lifecycle, supervisor commands, status and recovery.
- `backend/app/main.py`: authenticated system status composition and settings/action endpoints.
- `frontend/src/api/systemSummary.ts`, `api/localApi.ts`: independent presentation functions.
- `frontend/src/components/TopStatus.vue`, `components/LocalApiPanel.vue`, `views/SettingsView.vue`: user-facing status and action flow.
- `.trellis/spec/backend/managed-api-contracts.md`: atomic identity publication, barriers, target/applied distinction and bounded recovery.
- `.trellis/spec/backend/cloud-api-contracts.md`: saved mode not connectivity; local cache not upstream health.
- `.trellis/spec/backend/request-safety-contracts.md`: credential-bound protection, no status probes/reset.
- `.trellis/spec/backend/runtime-contracts.md`: current credential ownership and Bot isolation.
- `.trellis/spec/backend/idle-cpu-contracts.md`: preserve authoritative reads and bounded work.
- `.trellis/spec/frontend/workspace-experience-contracts.md`: coherent current identity, shared poller, stale guards and mutation revision handling.

## External References

No external dependency or protocol change proposed; existing pinned aiogram/native Telegram behavior and established contracts suffice for this source-level issue.

## Caveats / Not Found

No production logs or credentials were inspected; the persistent identity-loss path is source-proven, but not claimed as the only cause of every user-observed status. No live switch was attempted. No code edited. Task implement/check manifests intentionally not read because this research role is isolated. Existing uncommitted CPU/deployment changes remain untouched.
