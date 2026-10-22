# Research: User-centered interface inventory

- Query: Remove the standalone system page, retain essential top-level status, simplify settings and separate debug details without losing user actions.
- Scope: internal, planning only; product code and live runtime untouched.
- Date: 2026-10-22

## Findings

### Files found and present patterns

| File / anchor | Responsibility and implication |
| --- | --- |
| `frontend/src/App.vue:13` | Hash pages are a small local union, not Vue Router. Remove the system import/page/render branch together; decide explicit behavior for obsolete `#/system` links. |
| `frontend/src/App.vue:38` | Guards navigation, hash/back navigation, logout and browser unload against lost settings. Preserve all guards when adding settings tabs or status shortcuts. |
| `frontend/src/App.vue:71` | Shared topbar currently contains breadcrumb and account controls. Authenticated shell is the natural owner of persistent status. |
| `frontend/src/views/SystemView.vue:10` | Owns independent 3-second system polling and all system-only user controls listed below. Removing page without relocating children loses recovery/log controls. |
| `frontend/src/views/SettingsView.vue:38` | Already polls the same system endpoint; avoid duplicated shell/settings requests by sharing one authenticated status owner. |
| `frontend/src/views/SettingsView.vue:43` | Proxy revision comparison prevents older diagnostics overwriting a newer saved revision; keep this on any shared state refactor. |
| `frontend/src/views/SettingsView.vue:63` | Settings assignment clears write-only input and creates dirty snapshot; background status must never call this assignment. |
| `frontend/src/views/SettingsView.vue:104` | Offline PATCH, revision conflict, field editability and transient feedback. Save is deliberately separate from candidate validation at line 122. |
| `frontend/src/components/LocalApiPanel.vue:24` | Explicit restart/retry/uncertain-logout confirmations, backend eligibility and stale/dirty guards. Detailed identifiers are optional via `detailed` at line 66. |
| `frontend/src/components/BotSafetyPanel.vue:15` | User-triggered saved-token retry with cooldown/auth pause guards; currently reachable only from SystemView. |
| `frontend/src/components/DiagnosticLogPanel.vue:24` | Explicit export, confirmed clear, mutually exclusive busy state, cancellation/unmount handling; not development-only functionality. |
| `frontend/src/components/SettingsBackup.vue:57` | Password-encrypted export, redacted preview, explicit restore; dirty guard, separate password and secret clearing. |
| `frontend/src/composables/usePolling.ts:5` | One request per poller, abort/late-generation guards, retained data on failure, slower hidden-document polling. |
| `frontend/src/style.css:128` | Settings has exactly one scroll row plus solid bottom action row in normal layout. Mobile settings retain viewport shell at line 278. |
| `docs/.vitepress/config.mts:25` | User and development pages currently share navigation/search; developer top navigation is prominent. |

### Complete relocation inventory for current System page

| State or action | Suggested user-facing destination |
| --- | --- |
| Bot username and online/connecting/unconfigured/disconnected/error | Compact top status, with settings connection shortcut. Local success must remain “本地 API 可用”, not proof of Telegram reception. |
| Request protection: ready/throttled/backoff/auth_paused, message and earliest retry | Persistent top exception indicator plus actionable connection section. Never hide in debug, auto-retry or clear server waits. |
| Explicit saved-token “重试 Bot 连接” | Connection maintenance, outside debug; preserve backend `can_retry`, stale and busy guards. |
| Active files, pending files, concurrency | Compact task/top summary; concurrency adjustment remains download preference. |
| Local API process state; desired vs actual mode; operation phase/message; cooldown deadline | Connection section and compact top activity/exception. Process running and Bot connection remain distinct. |
| Managed restart, eligible retry, logout-uncertainty acknowledgement | Connection maintenance outside debug; retain explicit confirmations and no repeat cloud logout. External mode has no process controls. |
| Local service heartbeat, generation and operation ID | Debug details; process phase itself is normal user information. |
| Download roots: label, availability, error, remaining/total space | Download/save section; top storage exception and optionally selected-root free space. Do not silently drop nonselected root availability or cache shortage detail. |
| Telegram cache remaining/total space | Storage details outside debug; capacity is necessary for large downloads. |
| Proxy status/message and desired/applied revisions | Status in connection/proxy section; numeric revisions debug-only. Saved choice never implies applied connection. |
| Recovery message and blocked job count | Top exception/task guidance; ordinary user-visible recovery, not debug. |
| App version | Small About/settings footer, easy to copy for support. |
| Log ready/error, message, bytes/file count, retention, cleanup timezone/time, last/next cleanup | Maintenance/log settings outside debug. File capacity/rotation internals may be debug details. |
| Export log and confirmed clear | Maintenance outside debug; keep exclusions and continuing-recording explanation. |
| Status poll failure, retained last result and manual refresh | Compact top stale/unavailable state and accessible refresh. Do not continue presenting retained success as fresh. Routine refresh timestamps need not be permanently verbose. |

### Settings inventory and classification

Recommended user sections (not a final approved layout): **连接 Telegram**, **下载与保存**, **提示与命名**, **维护与备份**, then **开发者选项**. A compact local section navigator can replace the long numbered wall; desktop may use spare width while mobile uses ordinary accessible tabs/links.

- Everyday/onboarding: Bot Token, allowed user IDs, cloud/local selection, conditional local API ID/Hash, proxy enable/protocol/address/port/authentication, download location, confirmation minutes, concurrency, save/validate. These are necessary to make the app work and must remain available with debug off.
- Customization/advanced but user-facing: timezone, retry count, confirmation/file templates and plain-text examples, reset defaults, variable help, local service controls, storage details. Use expandable help/default presets rather than a debug gate.
- Maintenance: encrypted backup/restore, log occupancy/export/clear, retention days and daily cleanup time. Keep discoverable without debug.
- Debug-only candidates: numeric settings revision in saved-state label; proxy desired/applied revision numbers; local process instance/operation IDs and precise heartbeat details; verbose configuration-source/deployment metadata; rotation implementation limits. Environment-locked fields must still show that they cannot be edited even when source metadata is hidden.
- There is currently no true developer-tuning form field (poll interval, internal URL, raw TDLib settings). Do not invent dangerous settings to fill a debug panel. Developer switch may simply reveal diagnostic metadata; it must not enable requests/log verbosity or change runtime behavior implicitly.

### Layout/copy and behavioral requirements

1. Persistent top status should be compact, with concise connection/queue/storage information and clear problem priority. Do not cram multiple full diagnostic cards into the topbar. Mobile needs wrapping/overflow-safe summary and accessible details without obscuring account controls/content.
2. Always show meaningful errors, safety waits, unavailable storage, failed transitions, logout uncertainty and recovery blockers regardless of debug state. Expected unused local service in cloud mode should not look like a global fault.
3. Remove references to the deleted page in `SettingsView.vue:50` and log help near line 265, tests and public guides. Use destination-specific action text such as connection settings/log management rather than “上方/下方/系统状态页” where layout can change.
4. Keep cloud 20 MB limit visible at mode selection; local requires API ID/Hash; no-host-system-proxy inheritance wording; same explicit proxy for both modes. Copy must not promise upstream message reception from local API identity/poll success.
5. Preserve input across tabs, debug toggles, poll responses and failed validation/save. Hiding a field is not clearing it. Pending edits must remain reflected by the shell guard. Showing metadata should not create dirty settings.
6. Keep one settings scroll area and bottom solid save row, no floating transparent gap. Do not put operation-result messages permanently at the bottom. Save and candidate connection validation retain different labels/results and blockers.
7. Polling retains geometry, task selection, filters/pages, empty views and detail progress. New shared status must abort on logout/unmount, avoid stale response overwrite and never reset form or jump scroll.
8. All strings including captions/templates/errors remain plain text and secrets remain write-only. Debug is presentation, never authorization; do not reveal saved Token/Hash/password or browser-persist credentials.

### Regression inventory

- `frontend/e2e/acceptance.spec.ts:155`: current system-link expectation must become navigation/top-status coverage.
- `frontend/e2e/acceptance.spec.ts:193`: desktop/mobile empty polling geometry; preserve.
- `frontend/e2e/acceptance.spec.ts:274`: two-session conflict and retained inputs.
- `frontend/e2e/acceptance.spec.ts:329`: offline mode switch; line 365 templates; line 435 short-mobile save-row/message visibility; line 505 selection/polling/deletion.
- `frontend/e2e/managed-api.spec.ts:47`: current system-page restarts, uncertain logout, cooldown and runtime details need relocation assertions; debug metadata assertion only after enabling debug.
- `frontend/e2e/request-safety.spec.ts:4`: currently system-page-only protection/retry; retain always-visible protection and wait guards.
- `frontend/e2e/diagnostic-logs.spec.ts:5`: export/clear/retention move to maintenance without requiring debug.
- `frontend/e2e/proxy-network-ui.spec.ts:4`: acknowledgements and live polling must preserve edits.
- Unit helpers `frontend/tests/localApi.test.ts`, `botSafety.test.ts`, `polling.test.ts`, `diagnosticLog.test.ts` cover existing contracts; add focused classification/summary cases rather than rewriting broad tests.

### Documentation direction

User-first navigation: quick start → deployment on Docker/飞牛 → first Bot download → configuration for large files/network → tasks/save structure → backup/maintenance → troubleshooting. Move contributor tooling, isolated acceptance and Pages authoring to a separate collapsed developer group. Update UI walkthroughs only to actual implemented controls; preserve portable paths and accurate release/real-device validation boundaries. Existing VitePress build, link/output exclusion, case-sensitive `/TGDL/`, mobile and search checks apply. No deployment workflow changes required for editorial restructuring.

### Related specs / external references

- Read `.trellis/workflow.md`, `.trellis/spec/frontend/index.md`, `ui-contracts.md`, `documentation-contracts.md`.
- Related contracts linked by UI spec: managed API, request safety, diagnostic log, backup, template, task deletion and local progress.
- No external research needed for this internal inventory. Existing site stack is VitePress 1.x per documentation spec; no dependency changes proposed.

## Caveats / Not Found

- Planning stopped on user steering to commit/push current completed work. This file is a future UX planning artifact only; no source/spec/test/runtime changes made.
- No browser/screenshots or live configuration read; recommendations come from source inspection, not a claim of tested new UX.
- Debug persistence scope (session-only or nonsecret local preference) and exact topbar/section geometry remain design decisions. Default off and no implicit runtime mutation are recommended.
- Do not treat this inventory as approval to remove statuses/actions or to implement prior to the main session's planning gate.
