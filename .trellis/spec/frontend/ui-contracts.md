# UI and API contracts

## 1. Scope / Trigger
Read before changing frontend/src views, components, api or composables. Use the approved media-group terminology. Media playback/preview, media-file deletion, Web confirmation and message-link downloading are outside scope; finished task-record deletion and plain-text template examples are supported.

## 2. Signatures
src/api/client.ts centralizes request<T> and ApiError. src/api/types.ts is the manually maintained DTO source paired with backend routes, not generated OpenAPI. usePolling owns refresh, abort and stale-response generation guards. App shell owns sessions/hash navigation; views own temporary forms.

## 3. Contracts
API base /api/v1, same-origin credentials, X-CSRF-Token for mutations. Never persist credentials in localStorage or display existing secrets. Settings revision, editable, sources, configured and proxy_status are distinct. Blank secrets preserve values, explicit clear flags erase. Keep entered values on409; end session on401. An older diagnostic poll cannot replace a newer desired_revision after PATCH.

Administrator setup and password change validate and display the same inclusive 6–20 character range as the API. Reject lengths 5 and 21 before submission; allow 6 and 20. Do not apply the new creation limits to existing-password login. Existing passwords and rejected input must never appear in diagnostics.

Confirmation duration controls use minutes and show the new 30-minute default.
The DTO still carries integer `confirmation_timeout_seconds`; convert explicitly
without rounding existing values on load or unrelated saves. Preserve old
sub-minute and non-minute values (including 31 seconds), environment read-only
rules, existing saved durations and previously issued cards. Default template
examples use `{expires_in_minutes}`; retain `{expires_in}` seconds as a clearly
marked legacy option rather than silently changing custom-template meaning.

The settings form uses `novalidate`: native step validation with a 0.5-minute
minimum and a one-minute step can block valid integer minutes and legacy values.
Keep explicit frontend and API range validation. Browser tests must submit the
form and verify the persisted seconds, including unrelated saves with 31 seconds.

Saving settings does not require online Bot validation. Keep local ID/format checks and explicit manual validation; manual validation must use current form values without saving them. Show save and validation outcomes as ordinary short-lived ElementPlus messages, including errors and warnings; distinguish saving from successful connectivity validation. Do not reserve a permanent bottom feedback area or retain success/validation alerts after an operation. Preserve unsaved input after errors, with actionable revision-conflict guidance, blocked conflicting save and a visible reload action. Messages are plain text, accessible, visible on desktop/mobile without scrolling and never expose secrets. Use one predictable content scroll area; controls must not hide fields or focused elements.

Settings exposes `bot_api_mode: 'cloud' | 'local'` in the same form. Cloud explains
the 20 MB per-file cap and no API ID/Hash requirement; local explains its separately
deployed service and official migration prerequisites. Preserve the existing
bottom action row and offline-save/manual-validation split. On `API_MODE_BUSY`
(409), keep unsaved input and explain that downloading/recovery must finish first.
Do not imply that selecting a mode starts Docker or verifies connectivity. New
data defaults cloud; legacy data stays local until the user changes it.

Settings `confirmation_template` and `filename_template` follow the variable,
validation, default and snapshot contracts in
[template-contracts.md](../backend/template-contracts.md). Show labeled example
previews as plain text, provide variable help and explicit restore-default actions.
Use the existing configuration source/editability and unsaved-input protection;
restore defaults changes the form and still requires saving. Do not interpret
template or preview text as HTML, and do not imply that previews contact Telegram.

The settings page uses a dedicated scroll row and a solid action row in normal layout, flush to the bottom of its viewport. Do not render a floating rounded card above a gap that exposes scrolling content underneath. The action row contains actions and concise unsaved/conflict status, not resident operation-result alerts. It occupies space and must not overlap the last input or keyboard focus. Keep the save button associated with the settings form when outside the scroll row. Verify bottom-edge geometry and field visibility at desktop and short mobile viewports.

Settings help follows an information hierarchy: short field guidance, visible
decision-critical limits, unobtrusive configuration-source badges, and keyboard
accessible expandable deployment details. Avoid repeating long source labels or
save/validation explanations beside every field. Environment-fixed fields must
remain visibly read-only, including numeric limits. Current errors, cloud file
limits and secret-preservation rules cannot depend on opening a disclosure.
Template edit/preview panels may sit side by side on wide screens and stack on
mobile; variable help stays compact and readable with text at least 12px.
Saved proxy state remains distinct from candidate input, with failed state shown
as an error and detailed revisions still available. Preserve accurate offline
save/manual candidate-validation language when shortening feedback.
Local TUN proxy revisions describe network configuration and can differ from the
global settings revision; unrelated settings changes must preserve active status.

Keep source and Chinese UI copy in UTF-8. On Windows, prefer the patch tool for
non-ASCII source edits; do not send Chinese through a PowerShell-to-Python stdin
pipeline with an unverified console encoding. Lint can pass corrupted `??` text,
so verify the actual source and rendered screenshots before shipping copy edits.

Unknown progress stays unknown. Never invent percentages or demo tasks. Task can_cancel/can_retry are backend decisions; notification failure is separate from media failure. Vite must preserve Host with changeOrigin:false for backend Origin validation. Production assets have no external font/CDN dependency.

Render task titles from the API without independently deriving caption names.
Show the full request identifier in the task list's secondary text, wrapping on
narrow viewports without hiding characters. New titles contain no automatically
appended request ID; do not strip apparent suffixes from user text or rewrite old
frozen titles or issued Bot messages. New title and
destination examples follow date-first archives: `YYYY-MM-DD/<file>` for singles,
`YYYY-MM-DD/<safe-summary>_<short-id>/<file>` for groups. Caption-derived text is
plain text; preserve the existing task polling/layout behavior.

Task pages keep concise total/filter/search/status/progress controls; omit repeated
Bot onboarding banners and routine last-refreshed/loading text. Preserve necessary
initial/background error visibility and stable automatic polling. Task record
removal follows [task-deletion-contracts.md](../backend/task-deletion-contracts.md):
backend `can_delete` determines checkbox/delete availability, current-page select
all, explicit confirmation that downloaded files are kept, no nested interactive
controls, selection preserved through polling and cleared on filter/page changes.
Do not replace backend decisions with a private frontend status allowlist.

Task polling must preserve the last successful result, including an empty page,
through background loading and failures. Only initial loading without data uses
a skeleton. Do not condition an existing empty view on `!loading`: that removes
the view every polling cycle and changes scroll geometry. Background failure
uses a compact existing status/error area rather than inserting a new tall alert.
Keep automatic polling and filter/page state; do not restore scrollTop or add an
arbitrary panel height to hide a rendering defect. Browser regression checks must
observe that the empty-state node remains mounted and scroll/panel geometry is
stable over at least two automatic refreshes on desktop and mobile.

## 4. Validation & Error Matrix
| Condition | UI behavior |
| --- | --- |
| 401 | Login |
| 409 settings | Explain conflict; retain edits |
| Invalid user IDs | Identify invalid input before request |
| Poll failure | Error without late overwrite |
| Unsaved navigation | Discard confirmation |
| Saved proxy without ACK | Pending/failure/redeploy status, not active |

## 5. Good / Base / Bad Cases
Good: PATCH followed by an older poll preserves the saved revision. Base: empty tasks explain forwarding media to Bot. Bad: unmounted poll overwrites a new view, or blank input erases an existing secret.

## 6. Tests Required
From frontend: pnpm lint; pnpm typecheck; affected unit/browser cases and production build when relevant. Select regression scope per index.md; expand only when shared infrastructure, failures, unresolved concerns or release gates justify it. Unit coverage includes CSRF/auth errors, ID parsing, unknown progress, aborted/late responses. The Playwright suite uses actual backend APIs with isolated synthetic task records for mutations, persistence and two-session conflicts. See ../backend/acceptance-contracts.md for isolation and CLI contracts. Synthetic tasks do not prove real Telegram or NAS behavior.

## 7. Wrong vs Correct

Managed local API UI follows ../backend/managed-api-contracts.md. Render process
state, actual Bot connectivity and desired/applied mode separately. Show ongoing
phase/wait/cooldown/error as persistent service status, while save/action results
remain transient messages. Poll status independently without resetting form,
scroll or conflicts. Hash input is write-only, blank-preserving with explicit
clear. Disable unavailable/concurrent controls; process restart is not container
restart. Logout uncertainty needs a distinct explanatory acknowledgement, never
an implicit ordinary retry. External mode honestly reports no process control.

Bot request protection follows ../backend/request-safety-contracts.md. Show
server cooldown, network backoff and credential pause as distinct Chinese
states with retry deadlines. Use the saved-token explicit retry endpoint only
on user action; do not auto-click or clear waits. Disable concurrent, stale or
ineligible actions, use transient action feedback, and keep status polling from
overwriting unsaved settings. Process running is separate from Bot connectivity
and protection; never label the feature as guaranteed protection from bans.

Diagnostic export follows ../backend/diagnostic-log-contracts.md. Show log
availability in system diagnostics; export on explicit action using existing
session handling, busy/stale/unmount protection and a released object URL.
Do not poll event content, navigate to arbitrary paths or send logs externally.
Empty/truncated/error feedback is transient. Exported content is server-filtered
JSONL; it cannot establish a historical ban reason when evidence is absent.

Diagnostic retention uses the ordinary settings form/revision: default 7 days,
integer range 0–3650; zero disables age cleanup while capacity rotation remains.
Show applied policy and cleanup timestamps in system diagnostics. Manual clear
requires explicit confirmation explaining that only diagnostic logs are removed,
and that recording continues. Export/clear are mutually busy and use existing
session, stale and unmount guards; cancellation sends no DELETE. Operation
feedback is transient; status polling must not reset unsaved settings.

Wrong: present desired proxy settings as applied or discard unsaved settings on409. Correct: render supervisor acknowledgement, compare revisions before applying diagnostics, and retain user input on conflicts.

Dependency build convention: keep pnpm's default release-age policy enabled in local and Docker installs. If a freshly published pinned dependency fails that policy, select an eligible stable version and regenerate the lock with policy enforced; do not add release-age exemptions. Verify manifest/importer/transitive family alignment, frozen install and the normal frontend checks. Any future workspace policy file must be copied before Docker's install step.
