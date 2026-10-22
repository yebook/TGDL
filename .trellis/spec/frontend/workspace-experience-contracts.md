# User workspace and settings experience

## 1. Scope / Trigger

Read for authenticated shell navigation/status, settings sections/debug presentation, or moving former system-page controls. Approved user plan2026-10-22; preserve all backend eligibility and safety rules.

## 2. Signatures

The app exposes tasks/settings only. One authenticated shared `GET /api/v1/system/status` polling owner supplies top summary and settings panels. Numeric version comes from this status without stage labels. A default-off per-login debug boolean controls presentation only. Settings retain a single form/revision/dirty state across sections.

`SystemStatus.storage.selected_root_id` is the saved download-root ID, used to project the exact selected root. Do not assume roots[0] is selected or display another root's free space as the destination. Unknown/unavailable selected root remains explicit; all root and cache faults stay discoverable outside debug.

## 3. Contracts

- Remove SystemView and navigation references; no unpublished old-route compatibility shim. Unknown hashes use existing default navigation behavior.
- Summary shows precise cloud/local Bot availability, active/pending work and selected-root free space. Always surface meaningful request waits/auth pause, recovery blocks, process transition/failure, root/cache failures and stale status. Cached local identity is not proof of Telegram upstream reception. An unused local service while cloud mode works is normal.
- Status and account/password/logout controls share the topmost workspace bar. Do not repeat page titles in this bar or the task/settings page body; retain section headings and task/form actions. Display free capacity without the storage label, still resolved from selected_root_id. Numeric product version and changelog link live at the sidebar bottom-left, with a compact accessible mobile placement.
- The top bar also shows proxy application state from the same status response: disabled means direct connection; active means proxy configuration applied, not verified Telegram connectivity. Pending/applying/failure/unavailable stay distinct, stale data is explicitly historical, and proxy addresses or credentials are not displayed there. Rendering this indicator never triggers a network probe.
- Present current applied API mode and its observed service state separately from Bot identity and request state. During a transition identify the target without claiming it is already applied. Display only the current response's Bot username (or an explicit unknown/unconfigured placeholder); do not retain an old username across token changes. Local process running/cached identity never implies upstream Telegram health. Cloud/local unknown, failure, protection pause and stale states must remain explicit without additional probes.
- For draining, use required `local_api.drain_reason`: downloads shows download waiting; connection or unknown/null shows preparation, never infer from queued counts or translated message strings. Keep the full server explanation in service details. Use coherent neutral text, subtle status markers and a muted fine-line refresh control instead of isolated bright icon styling.
- External local API process management being unavailable is not an upstream connection failure: project managed-process failures only when the management component is enabled. Actual Bot connectivity and transitions remain visible independently.
- One status poller preserves last good data on background failure, explicitly marks stale and disables stale actions. Logout/unmount aborts it and clears the session debug toggle. Mutations patch current status without allowing older poll revisions to undo saved state. Never fetch/clear logs or invoke recovery merely to render status.
- Automatic polling never animates or resizes the refresh control. Use an accessible icon button with a stable target; only explicit manual refresh shows progress, with repeated clicks guarded. Preserve the shared poller's cancellation/generation behavior and reduced-motion preference.
- Four settings sections: connection, downloading/storage, templates/naming, maintenance/backup. API restart/retry, Bot retry, log policy/usage/export/clear, storage/cache details and encrypted backup are available without debug. Existing backend flags, confirmations, busy/stale/dirty guards and uncertain-logout acknowledgement remain authoritative.
- During connection transitions (including cloud proxy reconnection), lock only connection-related inputs with an explicit applying-state reason; ordinary download/template/log configuration remains saveable. Cover the post-PATCH/pre-status interval so repeated connection edits cannot slip through. Preserve draft input and revision-conflict behavior. Failed/blocked operations allow appropriate correction/retry; uncertain logout still needs explicit recovery. Stale status must not reopen connection inputs or imply completion.
- A post-save pending revision is released only by a fresh accepted status response at or beyond that saved revision; optimistic proxy patches and superseded refresh promises cannot unlock it. Disabled proxy is not proof that disconnect/reconnect is finished. While locked, omit draft connection fields (including secret-clear flags) from ordinary saves and restore those drafts afterwards. Keep cloud-only reconnect failure retry/validation eligibility aligned with backend guards, without granting local-process controls.
- Bound shared status GET to 10 seconds, settings reads/writes and API service actions to 15 seconds, and manual validation to 30 seconds. Release local loading/busy in finally; these deadlines never override a durable busy phase. Include response-body reading in the request deadline, abort on unmount, and do not automatically replay writes.
- Unknown PATCH outcome blocks additional connection writes until explicit resynchronization reads current settings and fresh status. Preserve locally edited fields and secret/clear drafts; use new server values for unedited fields and warn users to review remaining drafts. Resynchronization must not silently reload/discard the form. Failed API retries remain reachable with dirty input after confirmation that only saved configuration is used; retain stale/backend eligibility guards and uncertain-logout acknowledgement. Local revision conflicts still block writes/candidate validation; saved-configuration recovery is not blocked solely by a conflicting local draft, and resync remains available.
- Debug reveals metadata (revision, source detail, process/operation identifiers, heartbeat), never saved secrets, authorization or implicit runtime knobs. Environment locks still explain read-only fields outside debug. Section/debug switches do not mutate user settings or lose entered values.
- Retain offline-save/manual-unsaved-candidate-validation split, write-only secrets, conflict handling, unload/navigation guards, one scroll region and an opaque bottom action row. Ordinary action feedback is transient and visible without scrolling. Fixed bars cannot cover inputs/focus.
- Use concise Chinese and disclose decision-critical limits; avoid decorative English headings and repeated guidance. Responsive summary/section controls cannot horizontally overflow; keep keyboard focus and reduced-motion behavior. State text/icons complement color.
- At 390×600, compact the brand/navigation, summary and section selector enough for usable form content; passing overflow assertions alone is insufficient. Preserve 44px interactive targets. Local API panel unmount aborts pending requests, closes confirmations and prevents late events/toasts after logout or navigation.

## 4. Validation & Error Matrix

| Condition | Required behavior |
| --- | --- |
| Status stale/fails | Retain data labelled stale, expose retry, disable unsafe status actions |
| Debug off | Hide internal metadata; retain failures/recovery/logs/backup and read-only explanation |
| Change section/debug while editing | Preserve values, dirty state and validation messages |
| Save409 or validation failure | Preserve form, give actionable transient feedback |
| Save response lost or status request hangs | Bounded local wait; preserve drafts, offer explicit resync; no automatic write replay or unsafe phase unlock |
| Logout/session expires | Abort shared poll, clear debug, retain existing login semantics |
| Local cached request success | Local API available label; no invented Telegram-online guarantee |

## 5. Good / Base / Bad Cases

Good: user sees a connection problem at top and opens connection settings to recover without developer mode. Base: normal task list and compact current status. Bad: hiding blocked recovery behind debug, clearing secrets on section hide, or starting independent settings polling.

## 6. Tests Required

Unit tests cover status summary priority/staleness/cloud-unused-local and shared helpers. Desktop/mobile browser cases use actual isolated APIs: no system link, persistent status on both pages, section/debug/refresh input preservation, independent save/validation, conflict/navigation, moved recovery/protection/log/backup actions, stable task polling and reachable final fields/save controls. Do not invent success responses or use live credentials.

Failure coverage must include hanging fetch/body and cancellation, failed status followed by resync, committed PATCH with lost response, preserved ordinary/secret drafts, and failed retry with dirty input using saved configuration only.

## 7. Wrong vs Correct

Wrong: removing a system page deletes safety retry/log export, or toggling debug changes network behavior. Correct: relocate user actions to settings; debug changes visible metadata only.
