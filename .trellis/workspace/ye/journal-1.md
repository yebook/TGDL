# Journal - ye (Part 1)

> AI development session journal
> Started: 2026-10-19

---



## Session 1: Telegram downloader local implementation and review

**Date**: 2026-10-20
**Task**: Telegram downloader local implementation and review

### Summary

Implemented backend, WebUI and Docker/proxy configurations; local gates and browser integration checked, real Telegram/fnOS/TUN acceptance pending. No Git repository or commits.

### Main Changes

- Added durable media workflow, settings/auth API, responsive Chinese UI and deploy docs

### Git Commits

(No commits - planning session)

### Testing

- [OK] Frontend lint/typecheck/14 tests/build and production browser login/settings/mobile/logout passed

### Status

[OK] **Completed**

### Next Steps

- Complete final review evidence; run documented real Telegram greater-than20MB, Docker, proxy and fnOS gates before release


## Session 2: Final local quality gate

**Date**: 2026-10-20
**Task**: Final local quality gate

### Summary

Full-scope review completed: backend33, frontend14 and supervisor11 tests pass; production browser integration passes. Real Docker/Telegram/fnOS/TUN acceptance remains pending, task stays in_progress.

### Main Changes

- Fixed shutdown thread ownership, generation recovery, parent state projection, Bot identity isolation and proxy field bounds

### Git Commits

(No commits - planning session)

### Testing

- [OK] Backend ruff/mypy24files/pytest33; frontend lint/typecheck/unit14/build; supervisor11; Compose static parse; Alembic match

### Status

[OK] **Completed**

### Next Steps

- Execute V01-V08 using dedicated real deployment; do not mark release accepted before greater-than20MB and fnOS/proxy gates


## Session 3: Repeatable local acceptance complete

**Date**: 2026-10-20
**Task**: Repeatable local acceptance complete

### Summary

User selected local-only acceptance. Added 5 real-API browser E2E cases, 12 stdlib CLI tests, CI and code-backed acceptance spec. Fixed task search layout. All local gates pass; no real Telegram, NAS or Docker validation.

### Main Changes

- Added isolated temporary backend/browser fixture, service acceptance CLI, CI and test contracts
- Scoped select styling to settings form so task search remains visible

### Git Commits

(No commits - planning session)

### Testing

- [OK] Backend33, frontend14, proxy11, CLI12 and browser5 pass; CLI anonymous/authenticated integration against actual local backend pass

### Status

[OK] **Completed**

### Next Steps

- Real deployment acceptance remains deferred by user; run documented V01-V08 when requested


## Session 4: Local Docker WebUI started

**Date**: 2026-10-20
**Task**: Local Docker WebUI started

### Summary

Built app image and started loopback UI-only Docker stack; user deferred API credentials.

### Main Changes

- Pinned TypeScript ESLint family to aged 8.71.0 without policy exemptions; added apt retries.

### Git Commits

(No commits - planning session)

### Testing

- [OK] Frontend frozen install, lint, typecheck, 14 unit tests and production build passed; Docker app build, live/ready and browser initialization page passed.

### Status

[OK] **Completed**

### Next Steps

- User initializes admin; later fills local API credentials and starts full Bot API stack for real media validation.


## Session 5: Password policy 6 to 20 characters

**Date**: 2026-10-20
**Task**: Password policy 6 to 20 characters

### Summary

User requested inclusive 6-20 password length for setup and changes; updated backend, UI, requirements/specs and running Docker app.

### Git Commits

(No commits - planning session)

### Testing

- [OK] Backend 35 tests, Ruff and mypy passed; frontend 14 tests, lint/typecheck/build passed; independent review passed. Rebuilt/recreated Docker app; ready OK, live OpenAPI confirms min6/max20 for both schemas.

### Status

[OK] **Completed**

### Next Steps

- User initializes administrator and later configures API credentials for real Telegram testing.


## Session 6: Real isolated Docker persistence and restore acceptance

**Date**: 2026-10-20
**Task**: Real isolated Docker persistence and restore acceptance

### Summary

Deferred Telegram credentials; completed opt-in production-image Docker acceptance without touching user service.

### Main Changes

- Added isolated runner, 7 failure/safety tests, Docker acceptance guide and CI discovery; captured contracts and actual verification.

### Git Commits

(No commits - planning session)

### Testing

- [OK] 19 script unit tests and Ruff passed; independent review passed; final actual Docker run passed all 8 stages including recreation, stopped same-filesystem restore, storage identity and cleanup.

### Status

[OK] **Completed**

### Next Steps

- Real Telegram over20MB, Bot API full stack, proxy TUN and fnOS remain pending. User WebUI remains running.


## Session 7: Deployment documentation and Compose locations

**Date**: 2026-10-20
**Task**: Deployment documentation and Compose locations

### Summary

Clarified project-root Compose locations and source-build deployment for fnOS and local Windows.

### Main Changes

- Updated README, deployment guide, local-run guide and docs index with file table, source layout, env setup, SSH/Compose GUI steps, LAN binding and initialization.

### Git Commits

(No commits - planning session)

### Testing

- [OK] Both production Compose files config --quiet passed with .env.example; doc links and code fences checked. No lifecycle operations or secret reads.

### Status

[OK] **Completed**

### Next Steps

- User follows deployment guide when API credentials are available; real Telegram/proxy/fnOS gates remain pending.


## Session 8: Offline settings save and visible validation feedback

**Date**: 2026-10-20
**Task**: Offline settings save and visible validation feedback

### Summary

Separated offline Bot configuration persistence from manual validation; fixed content scrolling and sticky action feedback, updated running Docker WebUI.

### Main Changes

- Kept format/revision/encryption checks and runtime token/identity isolation; added backend regressions and 3 viewport real-API browser cases; updated docs/specs.

### Git Commits

(No commits - planning session)

### Testing

- [OK] Backend 41 tests Ruff mypy; frontend14 unit lint typecheck build passed. Original5 and new3 browser cases passed; screenshots reviewed. Isolated Docker8 stage smoke passed; final layout image rebuilt and user app readiness/setup pass.

### Status

[OK] **Completed**

### Next Steps

- Refresh WebUI after preserving unsaved input. Real Bot/API credentials, over20MB, fnOS and proxy validation remain pending.


## Session 9: Portable paths and flush settings action row

**Date**: 2026-10-20
**Task**: Portable paths and flush settings action row

### Summary

Replaced developer-specific deployment paths with relative defaults; settings actions now occupy a solid layout row at the content viewport bottom.

### Main Changes

- Updated docs env defaults and root runtime ignore rules; separated settings scrolling from docked actions without overlap or bottom gap.

### Git Commits

(No commits - planning session)

### Testing

- [OK] Compose static/doc links passed; frontend lint typecheck14unit build passed;8 browser cases passed across desktop and two mobile sizes; screenshots reviewed. Docker rebuilt/recreated and health/init passed.

### Status

[OK] **Completed**

### Next Steps

- Real Telegram credentials, over20MB, NAS and proxy gates pending.


## Session 10: Stable task page during background polling

**Date**: 2026-10-20
**Task**: Stable task page during background polling

### Summary

Fixed empty task view disappearing each automatic refresh; retained existing results and stable footer feedback.

### Main Changes

- Only initial requests show skeleton; background loading/errors preserve empty/list DOM. No polling/scroll restoration workaround.

### Git Commits

(No commits - planning session)

### Testing

- [OK] Frontend lint typecheck14unit build passed.10 browser cases ok including desktop/mobile3 automatic cycles with one network failure; panel/scroll geometry stable. Docker image rebuilt and local readiness/setup passed.

### Status

[OK] **Completed**

### Next Steps

- Real Telegram, over20MB, fnOS and proxy gates remain pending.


## Session 11: WebUI cloud and local Bot API switching

**Date**: 2026-10-20
**Task**: WebUI cloud and local Bot API switching

### Summary

Implemented and deployed unified Bot API mode switching; retained local large-file release gates.

### Main Changes

- Cloud streaming with 20 MB caps, proxy, cancellation and task-owned recovery; migration 0002.
- Single settings mode selector; app-only default Compose and optional local-api profile; portable docs updated.

### Git Commits

(No commits - planning session)

### Testing

- [OK] Backend 69 full tests and 28 final cloud checks; Ruff/mypy24; frontend14unit/11E2E; scripts19 pass.
- [OK] Production image built, isolated Docker8stages passed, final module image hash matched and current WebUI health passed.

### Status

[OK] **Completed**

### Next Steps

- User selects cloud mode and validates Bot; real Telegram, local over20MB, proxy routing and fnOS still pending.


## Session 12: Readable Bot confirmation file sizes

**Date**: 2026-10-20
**Task**: Readable Bot confirmation file sizes

### Summary

Bot confirmation sizes now match WebUI B/KB/MB/GB/TB formatting; deployed locally.

### Main Changes

- Use 1024-based units with one decimal above bytes; preserve unknown/zero and numeric API byte counts.

### Git Commits

(No commits - planning session)

### Testing

- [OK] Backend70 tests, Ruff/mypy24, independent focused3 tests passed; Docker rebuild/service health passed.

### Status

[OK] **Completed**

### Next Steps

- User can forward fresh media to see updated confirmation; real Telegram/NAS release gates remain pending.


## Session 13: Configurable Bot confirmation and filename templates

**Date**: 2026-10-20
**Task**: Configurable Bot confirmation and filename templates

### Summary

Implemented and locally deployed plain-text templates with previews and confirmation-time filename snapshots.

### Main Changes

- Add confirmation_template/filename_template; whitelist variables, preserve extensions, safe lengths/collisions, migration0003 planned filenames.
- WebUI editors/help/example previews/reset; same template and UTF16 budget rules; docs/templates.md and specs updated.

### Git Commits

(No commits - planning session)

### Testing

- [OK] Backend90/full20focused, frontend38unit/12E2E, independent review, Ruff/mypy26/ESLint/vue-tsc/build passed.
- [OK] Production image and isolated Docker8stage acceptance passed; current service updated and anonymous health/initialized checks pass.

### Status

[OK] **Completed**

### Next Steps

- User configures templates and validates fresh media; real Telegram/NAS/proxy/over20MB release gates still pending.


## Session 14: Clearer settings help and presentation

**Date**: 2026-10-20
**Task**: Clearer settings help and presentation

### Summary

Polished settings copy and visual hierarchy; verified and deployed to local WebUI.

### Main Changes

- Source badges and visible constraints, keyboard-accessible secondary details, responsive template editor/preview, correct proxy fallback messages.
- Preserved single scroll and solid bottom controls; documented UTF-8 edit and visual checks.

### Git Commits

(No commits - planning session)

### Testing

- [OK] Frontend lint/type/build,38unit and12E2E pass; independent review and desktop/mobile screenshots checked.
- [OK] Docker image build and service recreation pass; anonymous live/ready/initialized check pass.

### Status

[OK] **Completed**

### Next Steps

- User reviews refreshed settings; real Telegram/NAS/proxy and large-file release gates remain pending.


## Session 15: Confirmation timeout in minutes

**Date**: 2026-10-20
**Task**: Confirmation timeout in minutes

### Summary

Default confirmation timeout is now 30 minutes; settings and default Bot text use minutes while machine contracts retain seconds.

### Main Changes

- Preserved stored durations, legacy template seconds and issued cards; upgraded only exact old default templates with revision increment.
- Fixed native numeric step validation blocking settings submission; documented explicit form validation contract.

### Git Commits

(No commits - planning session)

### Testing

- [OK] Backend 98, frontend unit 42, browser 13 and static checks passed; independent template checks 28 backend and 28 frontend passed.
- [OK] Rebuilt final Docker image, recreated current WebUI, and passed anonymous live/ready/setup checks; authenticated API skipped without credentials.

### Status

[OK] **Completed**

### Next Steps

- Keep real Telegram, local API files over 20 MB, proxy and fnOS acceptance gates pending.


## Session 16: Friendly Bot feedback and daily archives

**Date**: 2026-10-20
**Task**: Friendly Bot feedback and daily archives

### Summary

Added visible Chinese confirm/cancel callback feedback and daily archive folders; preserved old task paths.

### Main Changes

- Covered expired, repeated, rejected and in-progress button clicks without duplicate jobs or false cancellation; old queued cancel directs WebUI.
- New video/image/group destinations use YYYY-MM-DD in archive timezone. Existing frozen monthly paths and retries stay unchanged; docs and preview example updated.
- Recorded user preference for regression scope based on impact in backend/frontend specs.

### Git Commits

(No commits - planning session)

### Testing

- [OK] Targeted backend 32 and frontend template 28 passed; scoped Ruff/ESLint and backend/frontend types passed. Independent review 12 targeted cases and Ruff/mypy26 passed.
- [OK] Final Docker build and current app recreation passed; anonymous live/ready/setup passed, authenticated API skipped without credentials.

### Status

[OK] **Completed**

### Next Steps

- Real Telegram alerts/downloads, local over20MB, proxy and fnOS remain separate unverified release gates.


## Session 17: Date-first archives and caption names

**Date**: 2026-10-20
**Task**: Date-first archives and caption names

### Summary

New downloads use date-first folders and caption-derived shared Bot/API task names.

### Main Changes

- Singles save directly under YYYY-MM-DD; groups use a bounded safe summary plus short ID below that date. File templates remain independent.
- Migration0004 adds nullable caption_snippet String80. Group captions select by message order; old titles, paths, outbox and retry snapshots stay fixed.
- Task list adds short IDs for legacy duplicate titles; previews and portable docs/specs updated.

### Git Commits

(No commits - planning session)

### Testing

- [OK] Targeted backend70 and frontend template28 passed, scoped Ruff/ESLint/mypy26/vue-tsc passed. Independent review7 targeted cases passed without product changes.
- [OK] Final Docker build, current service recreation and anonymous live/ready/setup passed. Authenticated API skipped without credentials; no full browser rerun.

### Status

[OK] **Completed**

### Next Steps

- Keep real Telegram, local over20MB, proxy and fnOS acceptance pending; continue impact-based regression.


## Session 18: Task record management and transient settings feedback

**Date**: 2026-10-20
**Task**: Task record management and transient settings feedback
**Branch**: `main`

### Summary

Simplified task list; added current-page selection and safe single/batch record deletion; settings now uses ordinary transient messages.

### Main Changes

- Migration0005 soft deletes terminal records atomically; preserves media/dedup/recovery metadata and rejects active tasks. Hidden API/Bot records cannot restart via old callbacks or late worker/notification results.
- List selection survives polling, resets filters/pages, handles cancellation/failure and last-page deletion. Settings keeps unsaved/revision protection with ElMessage instead of bottom resident results.
- Added deletion contracts and learned isolated real-API pagination fixtures, independent destructive-case records and settled screenshot evidence.

### Git Commits

(No commits - planning session)

### Testing

- [OK] Backend focused55, independent deletion22, frontend unit42 and10 distinct affected browser scenarios passed; scoped Ruff/mypy26/ESLint/vue-tsc and production build passed.
- [OK] Main/implement/reviewer viewed stable desktop/mobile screenshots. Final Docker build/recreate and anonymous live/ready/setup passed; no authenticated API credentials or user task deletions.

### Status

[OK] **Completed**

### Next Steps

- Keep real Telegram/local over20MB/proxy/fnOS release gates pending; use impact-based focused regression for subsequent work.


## Session 19: Document local Bot API migration and application credentials

**Date**: 2026-10-20
**Task**: Document local Bot API migration and application credentials
**Branch**: `main`

### Summary

Added API ID/Hash application and local API migration guide; synchronized deployment and Windows steps with official logout ordering.

### Main Changes

- Documented cloud logout, offline mode save, preserved mounts, TUN distinction and credential troubleshooting; updated migration spec.

### Git Commits

(No commits - planning session)

### Testing

- [OK] Docs-only independent review passed 44 relative links/anchors, UTF-8/fences/portable paths and embedded Python AST/Ruff/mypy; no Telegram requests, secret reads or service changes.

### Status

[OK] **Completed**

### Next Steps

- Obtain application credentials when Telegram permits, then follow documented migration and real Telegram over20MB/fnOS acceptance.


## Session 20: Separate task titles and full identifiers

**Date**: 2026-10-20
**Task**: Separate task titles and full identifiers
**Branch**: `main`

### Summary

Removed automatic short ID from new titles; show full task ID in list secondary text and separate Bot lines.

### Main Changes

- Unified system ID line outside custom confirmation templates with UTF16 budgets; preserved frozen history and group folder suffixes; synchronized specs/docs.

### Git Commits

(No commits - planning session)

### Testing

- [OK] Focused backend53/frontend28, independent backend41/frontend28 and affected static checks passed; isolated desktop/mobile case and reviewed screenshots passed.
- [OK] Final Docker build/update and anonymous live/ready/initialized checks passed. Browser CLI cleanup hung after successful case and its isolated process was stopped. No real Telegram or user task operations.

### Status

[OK] **Completed**

### Next Steps

- Verify new Bot messages with real Telegram; retain pending large-file/proxy/fnOS gates.


## Session 21: WebUI managed local Bot API

**Date**: 2026-10-20
**Task**: WebUI managed local Bot API
**Branch**: `main`

### Summary

Implemented application credentials, durable API transitions, observable supervisor status and manual restart/retry; updated local stack without Bot migration.

### Main Changes

- Added encrypted API credentials, process supervisor, transition barriers and settings/system controls; preserved existing data.
- Updated deployment and API acquisition docs; Windows private control directory uses a Linux data volume.

### Git Commits

(No commits - planning session)

### Testing

- [OK] 91 independent targeted checks; 28 Linux non-root checks; frontend static/build and 5 unit plus 5+1 browser scenarios passed.
- [OK] App and official Bot API 10.3 images built; isolated no-credential heartbeat smoke and local shared-status/live/ready/setup checks passed.

### Status

[OK] **Completed**

### Next Steps

- User obtains API credentials, initiates WebUI switch and validates actual Telegram >20 MB, proxy and fnOS behavior; task remains in progress.


## Session 22: Fix initial GitHub CI regression assertions

**Date**: 2026-10-20
**Task**: Fix initial GitHub CI regression assertions
**Branch**: `main`

### Summary

Matched user-provided backend and browser failure logs; corrected same-title deletion identity assertions and overlapping browser fixture search without production changes.

### Main Changes

- Deletion regression asserts deleted and surviving full IDs; browser query scopes to E2E mixed group; updated executable test contracts and verification notes.

### Git Commits

(No commits - planning session)

### Testing

- [OK] Locked sync, Ruff, mypy27; backend172pass3WindowsPOSIXskip; frontend45unit+static/build; affected4browser; deletion22/network11/scripts19 passed.

### Status

[OK] **Completed**

### Next Steps

- Push repair and confirm GitHub Linux CI; Docker daemon currently stopped, no production or Telegram operations performed.


## Session 23: Persistent Telegram request safety

**Date**: 2026-10-20
**Task**: Persistent Telegram request safety
**Branch**: `main`

### Summary

Added shared cloud/local request pacing, persistent cooldown/auth pause/backoff, protected manual recovery and WebUI status; isolated mock acceptance, no real Telegram or user deployment.

### Main Changes

- Shared persisted guard for all Telegram clients and cloud file HTTP; preserve retry budgets, acquisition ownership and migration ambiguity.
- WebUI reason/deadline/manual recovery; documentation and independent boundary fixes; CI adds two isolated browser scenarios.

### Git Commits

(No commits - planning session)

### Testing

- [OK] Frontend lint/typecheck/build and all 47 unit tests passed; two real REST browser scenarios and desktop/mobile screenshot review passed.
- [OK] Changed Markdown UTF-8/fences/portable paths and 44 relative links/anchors passed. Final backend suite recorded in docs/verification.md.

### Status

[OK] **Completed**

### Next Steps

- Real Telegram, greater-than-20MB, proxy and fnOS acceptance remain pending; Docker was not started or updated.


## Session 24: Incident timeline and persistent diagnostic logs

**Date**: 2026-10-20
**Task**: Incident timeline and persistent diagnostic logs
**Branch**: `main`

### Summary

Recovered limited 17:00-18:30 task evidence; first Bot disconnect unavailable after container recreation. Added bounded sanitized persistent logs, admin export and WebUI action, plus proxy error backoff correction.

### Main Changes

- Read-only incident review: 17:01 cloud size rejection, 17:06 cloud success, 17:10 task later cancelled; no first authentication/rate-limit error available.
- Persistent 10MiB JSONL with three archives, strict event projection and bounded authenticated export; status and explicit WebUI JSONL download.

### Git Commits

(No commits - planning session)

### Testing

- [OK] Windows backend 250 passed, 6 POSIX skips; Linux isolated nonroot network-none diagnostics 36 passed. Ruff and mypy 29 files passed.
- [OK] Frontend lint/typecheck/build, 49 unit and 2 mock UI contract browser cases passed; desktop/mobile screenshots independently reviewed.

### Status

[OK] **Completed**

### Next Steps

- Running user containers were not updated; deploy logging before further Bot testing. Real Telegram, proxy and fnOS gates remain pending.


## Session 25: Remove GitHub Actions workflows

**Date**: 2026-10-21
**Task**: Remove GitHub Actions workflows
**Branch**: `main`

### Summary

Removed the only GitHub Actions workflow at user request; local test scripts and application behavior are preserved.

### Main Changes

- Deleted .github/workflows/checks.yml; confirmed no remaining workflow files.

### Git Commits

(No commits - planning session)

### Testing

- [OK] Scoped diff and whitespace checks only; no application regression needed for CI configuration removal.

### Status

[OK] **Completed**

### Next Steps

- Continue local checks when changing features; prior real Telegram and fnOS acceptance remains pending.


## Session 26: Update local application containers

**Date**: 2026-10-21
**Task**: Update local application containers
**Branch**: `main`

### Summary

Rebuilt and deployed application from current source using existing Compose and mounts; preserved data, admin state and download inventory.

### Main Changes

- Backed up stopped data and tagged previous app image; recreated app only and started original API supervisor container.

### Git Commits

(No commits - planning session)

### Testing

- [OK] Production image build, live/ready, setup state, migration 0007, unchanged mounts/download inventory and persistent log creation passed.
- [OK] Independent anonymous verification passed: logs require authentication; production assets contain log export and manual Bot recovery.

### Status

[OK] **Completed**

### Next Steps

- Inspect Bot network error in system diagnostics; deployment health does not certify real Telegram media, proxy or fnOS acceptance.


## Session 27: Diagnostic retention and public documentation

**Date**: 2026-10-21
**Task**: Diagnostic retention and public documentation
**Branch**: `main`

### Summary

Implemented seven-day configurable diagnostic retention and confirmed manual clearing, MIT licensed public guides and independent static documentation site. Deployment and Pages publication intentionally pending.

### Main Changes

- Retention 0-3650 days, protected clear endpoint, safe event-time maintenance and WebUI policy/actions.
- Independent review fixed unrelated settings causing TUN proxy reload; network revisions now follow proxy content.

### Git Commits

(Changes remain in the working tree; this increment has not been committed.)

### Testing

- [OK] Backend scoped 82 passed / 8 POSIX skips; isolated Linux logs 57 passed; Ruff and mypy passed.
- [OK] Frontend lint/type/build, 2 unit and 6 desktop/mobile browser cases passed using synthetic data.
- [OK] Documentation production build: 20 Markdown files, 87 relative links, 15 public pages with anchors/base/exclusion/.nojekyll checks; desktop local search and mobile navigation/overflow checks passed. Read-only preview explicitly binds loopback; HTTP boundary smoke checks passed.

### Status

[OK] **Completed**

### Next Steps

- Preview available at http://127.0.0.1:18769/TGDL/ using docs:preview. Retain parent task in progress for real Telegram, fnOS and proxy acceptance. Changes uncommitted; no running deployment changes or Pages publication.


## Session 28: Docs-only Pages automation and remote privacy review

**Date**: 2026-10-21
**Task**: Docs-only Pages automation and remote privacy review
**Branch**: `main`

### Summary

Prepared path-scoped docs-only Pages workflow; audited remote history before potential repository-public action. No push, publication, visibility changes or history rewriting.

### Main Changes

- Docs-only main/manual workflow with independent frozen docs build, static-only artifact and minimal Pages/OIDC permissions.

### Git Commits

(No commits - planning session)

### Testing

- [OK] Docs build passed: 20 Markdown, 89 relative links, 15 public pages; independent YAML trigger/permissions/artifact review passed.

### Status

[OK] **Completed**

### Next Steps

- Review confirmed external commit-email exposure and internal development/incident notes before making repository public; any history rewrite needs explicit authorization.


## Session 29: Publish documentation to GitHub Pages

**Date**: 2026-10-21
**Task**: Publish documentation to GitHub Pages
**Branch**: `main`

### Summary

User accepted existing email/internal notes. Committed prior log feature separately and published documentation-only Pages workflow; GitHub build and deploy succeeded and live site verified.

### Main Changes

- Pinned official Actions to verified SHAs, Node24 then fixed pnpm11.23, immutable docs install, docs-only artifact and serialized Pages deployment.

### Git Commits

| Hash | Message |
|------|---------|
| `76ea00a` | (see git log) |
| `59b4718` | (see git log) |
| `85fdb40` | (see git log) |

### Testing

- [OK] Local docs build20 Markdown/89 links/15 pages and independent YAML review passed; final source audit293 files found no new real credentials.
- [OK] GitHub run39539596903 build/deploy succeeded; live home/operations/JS HTTP200, desktop Chinese search and mobile navigation/nooverflow verified.

### Status

[OK] **Completed**

### Next Steps

- https://yebook.github.io/TGDL/ is live. Future main docs changes auto-publish. Parent task remains open for real Telegram/fnOS/proxy acceptance; no app/NAS container update.


## Session 30: Update local Docker application after docs publication

**Date**: 2026-10-21
**Task**: Update local Docker application after docs publication
**Branch**: `main`

### Summary

Rebuilt and updated only local app container; stopped backup and independent read-only deployment acceptance passed. Supervisor and media preserved.

### Main Changes

- App inspected image502e1b147880 to f14161d7c848; rollback image and stopped backup .runtime/local-docker/backups/before-update-20261021T035213Z retained.

### Git Commits

| Hash | Message |
|------|---------|
| `b66889f` | (see git log) |

### Testing

- [OK] HTTP live/ready200, initializedtrue, migration0007, effective/default retention7 and production clear UI/status DTO fields verified.
- [OK] Independent mount/media hash preservation and backup database integrity/foreign-key checks passed; no actual log clear or Telegram probe.

### Status

[OK] **Completed**

### Next Steps

- WebUI http://127.0.0.1:8000 now serves current app. Parent task remains in progress for real Telegram/fnOS/proxy acceptance.


## Session 31: Organize open-source README homepage

**Date**: 2026-10-21
**Task**: Organize open-source README homepage
**Branch**: `main`

### Summary

Reorganized root README around first-time users: purpose, live docs, workflow/features, API modes, source deployment and contribution/license. No application or container changes.

### Git Commits

| Hash | Message |
|------|---------|
| `d745378` | (see git log) |

### Testing

- [OK] Documentation link check passed 20 Markdown files/97 relative links; independent source-based editorial review and diff check passed. No application regression needed.

### Status

[OK] **Completed**

### Next Steps

- Keep parent task open for real Telegram/fnOS/proxy acceptance; README changes use the existing docs-only Pages workflow.


## Session 32: Recover cloud to local API switch with host TUN

**Date**: 2026-10-21
**Task**: Recover cloud to local API switch with host TUN
**Branch**: `main`

### Summary

Diagnosed successful logout followed by local getMe network failure. User enabled host TUN; one guarded retry completed applied-local identity validation without replaying logout. Added safe connection failure messages, scoped regressions, specs and troubleshooting docs. Review and checks pass; working containers unchanged; large-file/fnOS/bundled proxy acceptance remains pending.

### Git Commits

| Hash | Message |
|------|---------|
| `21ea948` | (see git log) |

### Status

[OK] **Completed**


## Session 33: Daily log maintenance and unified proxy deployment

**Date**: 2026-10-21
**Task**: Daily log maintenance and unified proxy deployment
**Branch**: `main`

### Summary

Implemented configurable daily08:00 log cleanup and safe fixed-file usage display; unified Compose with private-volume init and WebUI direct/proxy/API routing. Independent review fixed stale network revisions and root IPC/link safety. Scoped backend/frontend/browser/Linux and docs checks passed. Updated public guides and first-release compatibility policy. Existing runtime containers/credentials unchanged; real bundled TUN/fnOS/large-file acceptance remains pending, parent task stays active.

### Git Commits

| Hash | Message |
|------|---------|
| `9c2e74c` | (see git log) |

### Status

[OK] **Completed**


## Session 34: Update local containers to unified deployment

**Date**: 2026-10-21
**Task**: Update local containers to unified deployment
**Branch**: `main`

### Summary

Built app/network images, completed stopped DB/control/full-cache backups and recreated app/API with unified network/init topology. Independent acceptance verified mounts/container media hashes, unchanged DB/admin/jobs/pgvector, health200 and deployed daily08:00/7day log features. Local Bot getMe succeeded after update, no logout replay. Rollback retained under before-unified-20261021T070659Z. No product/credential changes; real bundled-proxy/fnOS/large-file gates remain open.

### Git Commits

| Hash | Message |
|------|---------|
| `62cd5ff` | (see git log) |

### Status

[OK] **Completed**


## Session 35: Encrypted backups, native proxy and media workflow verification

**Date**: 2026-10-21
**Task**: Encrypted backups, native proxy and media workflow verification
**Branch**: `main`

### Summary

Consolidated first-release configuration backup, native proxy, grouping, validation and honest progress documentation; completed scoped reviews, documentation build and credential audit. Local download percentage remains a discussion-only design using TDLib snapshots. Parent task remains active for device acceptance.

### Git Commits

| Hash | Message |
|------|---------|
| `46fa1ca` | (see git log) |

### Testing

- [OK] Docs build: 21 Markdown inputs, 93 links, 16 public pages; staged whitespace and redacted credential checks passed.

### Status

[OK] **Completed**

### Next Steps

- Discuss the proposed local read-only progress bridge before implementation; complete separate fnOS and large-file acceptance.


## Session 36: Local API progress implementation and deployment

**Date**: 2026-10-22
**Task**: Local API progress implementation and deployment
**Branch**: `main`

### Summary

Implemented native read-only TDLib progress, guarded backend observations and user-facing measured progress; fixed same-command child restart generation handling. Native compile/network isolation, scoped regressions, desktop/mobile browser and final deployed source/data/health checks passed. Retained complete stopped backups and rollback images. User authorized publication before UX overhaul; UX remains planning-only, parent release acceptance remains open.

### Git Commits

| Hash | Message |
|------|---------|
| `2c5aa92` | (see git log) |

### Status

[OK] **Completed**

## 2026-10-23: Clean local media caches after safe publication

Implemented durable local cache housekeeping through a private offline TDLib endpoint, preserving API runtime/authentication data and read-only app cache mounts. Publication and recovery atomically enqueue encrypted source identities; final hash/root/fingerprint checks, draining and native per-file gates protect concurrent readers, late deletion and retries. Cleanup errors do not redownload successful media. No historical sweep, live container update, user-data change, commit or push performed.

Independent review fixed low-space cleanup admission and final-path FIFO blocking; source paths are encrypted with the existing key because they can contain Bot Tokens. Native pinned compile, shared policy fixture, no-client/no-egress and existing progress verifier passed. Backend broad run422 passed/11 skipped/one stale fake-client failure; fixed its mode and scoped recovery passed. Final encryption/schema/recovery39 and further focused7 cases passed, Ruff/mypy36 passed. Actual isolated Linux regular/FIFO/stat-to-open-race verification exited0. Docs23 Markdown/97 links/18 pages passed. Authenticated TDLib deletion/space reclamation remains real-environment acceptance; current development baseline schema requires explicit handling during any later deployment update.

### Authorized publication and local container update

The user subsequently authorized commit, push and current-container update. Feature commit `cff7a83` was pushed to `origin/main`. Production images were rebuilt from verified pinned native sources and canonical production stages; app image `7a477cdb907a`, Bot API image `c11e55ddfadb`.

An isolated baseline conversion preserved all existing table rows by named columns and proved the previous production ORM could read the fresh additive schema. After a graceful idle stop, complete app/API/control/config backups were verified at `.runtime/local-docker/backups/before-cache-cleanup-20261023T020855Z/`. Only the two TGDL services were recreated. Post-start checks passed for live/ready (200), original settings/admins/business IDs, media/master key, mounts, private control key, protection/mode/logout state and fresh API heartbeat/generation acknowledgement. Post-start failures never automatically stop new owners or restore stale state. Private deployment helpers and backups remain ignored. No real Telegram probe, historical-cache sweep or authenticated deletion/space-reclamation acceptance was performed.

## 2026-10-23: Allow explicitly configured root UID/GID

User requested removal of the UID/GID-zero restriction. The initializer accepts integer identities in 0..2147483647, and entrypoint/supervisor no longer reject UID 0. Default identity remains 1000:1000; private-control ownership/mode checks, read-only roots, dropped capabilities and no-new-privileges remain intact. README, deployment examples and current executable specs describe zero and mixed identities. The change does not alter the running deployment or existing directory ownership, and is not automatically committed/pushed.

Independent review passed. Scoped tests: 34 passed, 2 POSIX-only skips on Windows. Network-disabled Linux checks passed real initialization and entrypoint/heartbeat/private-file/shutdown behavior for 0:0, 0:1000, 1000:0 and 1000:1000 under existing container restrictions. Ruff and Linux-platform mypy passed for 3 files; documentation checks passed 23 Markdown files, 97 links and 18 pages. No Telegram probe was made.

## 2026-10-23: Build cache and production image distribution

User authorized implementing the researched build optimization, including a
separate manual main-only GHCR image workflow. Root Compose is image-only;
`compose.build.yaml` retains explicit source builds. Numeric default 0.1.0
references remain pending first publication, with an honest source fallback.
The initializer shares the API reference. Input-keyed manifests support reuse
and interrupted-publication retries; guarded aliases reject conflicting numeric
or revision tags. Actual-image no-secret acceptance precedes aliases and the
digest-pinned deployment bundle. Pages and current running containers remain
unchanged; no images or Git commits were published in this increment.

Source builds use explicit copy-mode pnpm/uv mounts, architecture/upstream-scoped
ccache and validated TGD_BUILD_JOBS (1..64, default 2). Native incremental patch
layers and production boundaries remain. Canonical app image built successfully;
Dockerfile checks passed for app/API. Clean offline reinstalls reused 212 pnpm
packages with zero downloads and installed 45 Python packages. Real minimal
C/C++ CMake clean rebuild across layers yielded two direct compiler-cache hits;
five invalid concurrency inputs were rejected. Full TDLib cold build was not
rerun, so no complete native-build or numerical speedup claim is made.

Scoped version/distribution tests: 22 passed; Ruff/version/quiet Compose checks
passed. Independent actual app production-image acceptance passed all eight
checks including persistence and disposable backup/restore. Docs checked 23
Markdown files, 104 links and 18 pages; full commented env/YAML examples matched
the implementation. Hosted publication, anonymous GHCR pulling and arm64 remain
unexecuted. First publication must confirm package visibility and anonymous
digest pulls before claiming public image availability. User buildlog is kept
untouched and is not included in this work.

### Independent native artifacts and freshness gates

Following user direction, split native Bot API/TDLib into independently reusable
precompiled bases. Shared identity code pins both upstream revisions and hashes
native patches, headers, build definitions and toolchain inputs. Actual checkout
revisions are checked before compiling; native packaging records provenance and
runtime packaging rejects missing or mismatched manifests even for manually
selected bases. Script/product-version changes do not trigger native rebuilds.
Source and published native paths explicitly support linux/amd64 only.

Final review passed: 49 distribution/version tests, Ruff and three-script mypy.
Real network-disabled BuildKit fixtures passed matching metadata and rejected
missing metadata and a changed CacheCleanup.h, without compiler/toolchain/source
stages. These fixtures used a previously verified binary and synthetic current
metadata, not a new full native compile. Documentation build passed 23 Markdown
files, 104 links and 18 pages; env/YAML examples and version metadata match.
User authorized commit/push; publication workflow remains manual and has not
been triggered. Public GHCR availability and authenticated media acceptance are
not claimed. Running deployment and user buildlog remain untouched.

## 2026-10-23: NAS runtime script permission repair

User reported Python Errno 13 opening the app entrypoint and API supervisor.
Docker COPY preserved restrictive Linux checkout modes. Normalize application
code/dependencies and built assets in producer stages, preserving executable
tools and avoiding a duplicate final payload layer; explicitly set runtime
startup script COPY modes. Mounted data and private control permissions remain
unchanged. Added offline image permission verifier and troubleshooting guidance.

Independent review passed: 54 distribution/identity tests, Ruff, mypy (36 files),
version/diff checks and docs build (23 Markdown, 104 links, 18 pages).
Restrictive Linux tar contexts (0600 files/0700 dirs) and deliberately restricted
real app dependencies/assets passed ten offline read-only disposable checks:
app/API times 1000:1000, 12345:23456, 1000:0, 0:1000 and 0:0. Checks read image
content, import app/dependencies and API scripts, and inspect executable tools.
Both negative controls reproduced the exact reported Errno 13 paths. Cached
production dependencies/native fixtures avoided network installs and native
compilation; native input identity is unchanged. Evidence remains private under
`.runtime/permission-check/`. No live deployment, Telegram, data or user buildlog
was changed; this repair has not been committed or pushed.

### Docker Hub publication preparation

User subsequently authorized including the permission repair in a commit/push
and completing the first production image publication workflow. They configured
DOCKERHUB_USERNAME/TOKEN repository secrets and chose to click Run workflow
personally; this workstation has no GitHub Actions login. User confirmed the
three public Docker Hub repositories and read/write token scope are ready.
GHCR builds/cache stay authenticated; Skopeo mirrors complete indexes and
attestations to Docker Hub without compiling twice. Both registries are checked
for immutable-tag conflicts before expensive builds. Real-image permission
checks/offline smoke and anonymous Docker Hub manifest checks precede aliases.
Deployment bundle defaults to Docker Hub with images.ghcr.env as an alternate;
private GHCR packages do not block Docker Hub deployment.

Implementation scoped distribution/version tests: 65 passed; independent final
distribution/version/runtime-ID tests: 79 passed. Ruff and mypy (36 files)
passed. Documentation build, env/YAML synchronization, version and diff
checks passed. Remote workflow execution, credentials/scope verification and
actual registry pushes are pending the user's manual trigger; no successful
publication or public numeric tags are claimed. No runtime deployment changed.

## 2026-10-23: Checked-in native bundle and hosted verifier repair

User requested a feasible-size native binary in Git so downloaded source avoids
TDLib compilation. Added linux/amd64 bundle (15,474,996 bytes, 14.76 MiB) with
binary (45,551,408 bytes, 43.44 MiB), licenses and provenance, ordinary Git files.
Default source/publication uses bundled-native; explicit native target and
bundle_native.py preserve full compile/regeneration. Manifest validates native
source identity, archive/member checksums, safe paths and actual binary checksum.
Publisher separately binds concrete archive/manifest bytes and validates local
bundle even on registry hits. LF attributes preserve identities across checkouts.

Original builder source matched both upstream pins, all three patches and two
headers. Its old /out export differed from current compiled output; used a cached
up-to-date target build and fresh install, verified byte equality and archived
the actual current binary b24c49244e72673e8b0a37956eb842d0e1675b0522e6fdba71c8ee7cb5c635d2.
Full source recipe now compares compiled and installed binaries before metadata.
Release/GCC flags had no test/bypass definitions. No full cold compile was run.

Actual default Docker build passed without source/Git/CMake/compiler stages;
chosen binary passed offline proxy (five scenarios), progress and cleanup
checks plus five UID/GID checks. Regeneration CLI succeeded deterministically.
Combined scoped tests 96 passed, one Windows symlink skip covered in Linux;
Ruff and mypy (36 files) passed. Docs build checked 23 Markdown, 104 links and
18 pages; env/YAML examples and version metadata matched. Evidence is private
under .runtime/bundled-native; no running deployment or Telegram was changed.

During this increment user supplied hosted publication failure: mounted verifier
could not import app.main. Earlier stdin-only local execution masked Python's
different sys.path for an absolute mounted script. Verifier now resolves the real
app directory and rejects unexpected module locations. Two external-script tests
passed (combined 98 passing scoped tests, one Windows skip). Exact hosted Docker
invocation with mounted /verify-permissions.py, no PYTHONPATH, read-only and no
network reproduced the old import failure and passed the fixed script under
1000:1000 and 12345:23456. Evidence is .runtime/image-import-check/evidence.json.
Production application layout/startup remains unchanged. Hosted workflow must be
rerun from the new commit, not rerun an old revision's job.


## Session 37: Simplify image publication to app and API

**Date**: 2026-10-23
**Task**: Simplify image publication to app and API
**Branch**: `main`

### Summary

Removed independent native registry publication; final Bot API uses the verified bundled artifact. Documentation and build contracts updated.

### Main Changes

- Publish and mirror only app/API; preserve native input and artifact fingerprints and immutable alias safety.

### Git Commits

| Hash | Message |
|------|---------|
| `4678435` | (see git log) |

### Testing

- [OK] 72 scoped tests, Ruff, mypy, version check and diff check passed; docs build validated 23 Markdown files, 104 links and 18 pages.

### Status

[OK] **Completed**

### Next Steps

- User manually runs the image workflow on main; no hosted publication, remote repository deletion or local container update performed.


## Session 38: Unify source and image Compose deployment

**Date**: 2026-10-23
**Task**: Unify source and image Compose deployment
**Branch**: `main`

### Summary

One shared compose.yaml supports local source builds and digest-pinned image deployment; removed source override Compose and synchronized guides.

### Main Changes

- Source up --build uses local tags and default never policy; release env files select paired image digests and missing policy.

### Git Commits

| Hash | Message |
|------|---------|
| `HEAD` | (see git log) |

### Testing

- [OK] Independent 88 passed, 1 Windows symlink skip; Ruff, script mypy, version and diff checks passed. Real Compose config/build-plan and source-free bundle config passed; docs 23 files/103 links/18 pages and YAML/env parity passed.

### Status

[OK] **Completed**

### Next Steps

- Actual build/startup unexecuted; Docker image-only dry-run stalled and was cancelled. No current containers or data changed. Parent implementation task remains active.


## Session 39: Fix final runtime script permissions and diagnose stale initializer

**Date**: 2026-10-23
**Task**: Fix final runtime script permissions and diagnose stale initializer
**Branch**: `main`

### Summary

Explicit final-stage chmod protects app/API scripts from restrictive copied modes. User nonroot-ID exception confirms deployed API initializer is outdated.

### Main Changes

- Normalize only image-owned code, check API parent traversal, retain mounted state and native input identity. Update source rebuild guidance.

### Git Commits

| Hash | Message |
|------|---------|
| `8ecea7b` | (see git log) |

### Testing

- [OK] Independent 88 passed and 1 Windows symlink skip; implement Docker4 passed with actual UID1000 Errno13 before chmod and verifier success afterward; private0600 preserved. Ruff, mypy36, version, diff and docs build passed.

### Status

[OK] **Completed**

### Next Steps

- Commit remains local until requested push. User must update actual source and rebuild/recreate containers; no user deployment or native compilation performed.


## Session 40: Source-only deployment and full local Compose verification

**Date**: 2026-10-23
**Task**: Source-only deployment and full local Compose verification
**Branch**: `main`

### Summary

Blocked remote image deployment/publication; fixed local source tags build by default, build-time non-owner guards, and resolved duplicate API exporter race found during real source startup.

### Main Changes

- Only bot-api builds shared API/init artifact. Fixed source tags ignore remote env overrides; bundled native fixed. Public docs and version tooling synchronized.

### Git Commits

| Hash | Message |
|------|---------|
| `e323a80` | (see git log) |

### Testing

- [OK] Independent90 passed3 skipped, actual Docker permission4 passed, Compose3 passed, Ruff/mypy36/version/diff/docs23files99links18pages and YAML/env parity passed. Full default up, stale-tag rebuild, UID1000/0 init0 and live/ready200, explicit no-build recreation succeeded; no TDLib compile or Telegram credentials.

### Status

[OK] **Completed**

### Next Steps

- Primary isolated tgd-source-acceptance remains at localhost18083 with fresh .runtime/source-only-acceptance storage and waiting_credentials. Extra root stack removed. Commit local pending requested push; user NAS result still needs current source build.

## 2026-10-27: Idle CPU optimization increment

- User prioritized CPU only after reporting local mode app207MB/2.67%, bot-api35MB/1.85%. Preserved 0.5-second scheduling and existing request/transition/ownership protection.
- Added due-work probes for group/expiry and notifications, single indexed active/pending aggregate, empty-dispatch checks, stable-generation write suppression and 5-second stable supervisor heartbeat with immediate state changes. No SDK/native/memory/schema changes.
- Implement targeted groups74passed2skipped and88passed (overlap); independent review final notification/safety/pipeline/recovery/deletion scope94passed, Ruff full scope and mypy36passed. Native checked-in bundle source/binary verification1passed; docs23Markdown/99links/18pages, env/Compose parity, numeric version0.1.0 and diff checks passed.
- Docker was initially stopped; user started it and authorized local update. No TGDL containers existed. Created fresh isolated tgd-idle-cpu at localhost18083 without old credentials/data, preserving unrelated pgvector and rollback image tags. Appb1b30a376903/APIa3e6e5026c5d, initExited0, live/ready200 independently confirmed; runtime SHA3be3388c74300fe54ca40b45c176e79d8d8ca427e45cb7a9a54b4d1bea91eae9 matches final notification optimization. TDLib/bundle unchanged, source builds reused native.
- Matched unconfigured 30-second cgroup sample: app2.9301%->2.6721%, supervisor0.1852%->0.0868%. Separate fake-local Linux scheduler30seconds/59ticks: CPU0.846871->0.486873seconds, SELECT1121->354(19->6/tick), BEGIN118->0, generation59->0. Separate containers prevented benchmark contamination. No actual Telegram connection/large-file acceptance or guaranteed NAS CPU saving claimed.
- Docs/spec/reproduction and Docker acceptance evidence retained under active task; parent remains in_progress. Source work (including prior remote-image cleanup) remains uncommitted/unpushed. Current fresh app stays running; no user config/production data restored or cleared.

## 2026-10-28: Remaining app CPU attribution and optimization

- Profiled full actual loops in offline isolated Linux containers: blank443 SELECT/30s; fake connected484. Repeated ORM/config materialization dominated raw SQLite execution; task page20rows used64 authenticated SELECTs. Reports/reproduction tools retained in task research.
- User authorized implementation. Added fresh Core settings/runtime singleton reads, one-entry validated settings cache keyed by fresh revision+encrypted values+effective environment, deep-copy isolation and batched page associations64->7SELECT. No operational-state cache, timing/native/memory/schema changes. Preserve all earlier uncommitted work.
- Implementer149 affected regressions passed incl backup/restore/API transitions/deletion/safety/recovery; independent review added rollback/fresh-state and paginated association isolation checks, efficiency9passed; Ruff and mypy36passed. Updated idle CPU executable spec and changelog.
- Production app Docker build passed final UID1000 permission verification. Final imagefebf248824ff and source hashes confirmed in running isolated tgd-idle-cpu at localhost18083; existing APIa3e6e5026c5d reused, init0/live-ready200. No real Telegram credentials or requests; unrelated pgvector preserved.
- Matched offline30s before/after: fake-connected CPU0.619259->0.566761seconds (~8.5%); blank0.538385->0.455635 (~15.4%). Hot configuration/state read CPU approximately halved; total saving modest, fresh SELECT count intentionally retained. Separate blank running-container cgroup sample app1.7366%, waiting supervisor0.0840%; previous day's2.6721% is not a matched baseline. Second baseline repeat unavailable after old untagged image was replaced; recorded limitation, no repeat result claimed.
- Wider probe consolidation and topbar disk/log work deferred. Parent remains in_progress; no commit/push requested or performed. Updated isolated stack remains running with its existing test data.

## 2026-10-28: API switching and coherent Bot status

- User requested smoother API switching/status and clarified actual Bot messages work while UI is abnormal. Source research proved polling AppError cleared username permanently while retained-client reuse only reset state/message; reuse also prematurely advertised recovery. Other findings: separate status observations, mismatched panel/topbar priorities and local-start wording for cloud stop.
- Implementer added exact client/signature/generation verified identity, retry eligibility distinct from recovery, successful-poll identity restoration, guarded late results/current candidate errors and coherent settings/API/Bot/proxy snapshot. Durable transition failures no longer overwrite transport state each tick. All DB/lock-bearing result helpers run through worker threads. No added upstream probes/restarts or native/schema/timing/protection changes.
- Frontend shares connectionSummary between topbar/panel, target-aware phases, current vs desired modes and cloud-unused-local neutrality. Review corrected normal progress appearing in fault list; retained genuine failure/protection/uncertainty/stale warnings. Docs include switch-stage explanation; executable managed/frontend specs and changelog updated.
- Backend related99passed, final connection_status7passed including authenticated HTTP projection/token isolation, Ruff/mypy36passed. Frontend90initial unit tests and final helper20passed, lint/typecheck/buildpassed. Desktop/mobile presentation2passed; initial uv-managed Windows test-server teardown hung, cleaned owned processes and reran same2 with direct manual Python server and temporary no-webServer config: exit0/4.9s; server/config cleaned. UI display fixtures explicitly do not certify Telegram connectivity. Docs23md99links18pages passed.
- Full production app image002118c5adc2 built and packaged latest runtime SHA3750db2c308280e7075111b6f9629fe65cc9ab28ef0ca8c93ebfd1ef4647ff1c/source hashes verified. Preserved before-api-status-20261028 rollback tag. Updated only existing isolated tgd-idle-cpu app at localhost18083, kept API/data/unrelated services. live/ready200. No real Bot credentials, remote logout/switch or Telegram probe used.
- Research and final evidence in api-switch-status-audit-20261028.md / api-switch-status-verification-20261028.md under active task research. No commit/push requested or performed; parent remains in_progress for authenticated real-device acceptance. Previous pending source-only/CPU work preserved.

## 2026-10-28: Readable file durations

- Formatted per-file download elapsed time and video duration with shared Chinese hours/minutes/seconds helper; labels distinguish video length from download elapsed. API values and timing behavior unchanged.
- Frontend lint, production build with type checking and 108 unit tests passed. Independent narrow review found no issues; diff check passed. No Docker update or commit/push requested; parent task remains in_progress.
## 2026-10-28: Clean media output, caption filenames and patch version

- User requested no persistent output marker, original-name/caption/generated filename priority with shared indexed group captions, patch increment and upgrade operations. Replaced download marker with strict private device/inode metadata, retaining mount/replacement checks; old unpublished identity fails with deliberate scoped re-enrollment instructions, no data or marker auto-deletion.
- Card-time filename planning shares normalized caption in message order; preserve originals, extensions, templates, UTF8 limits, frozen plans and collision safety. Independent review caught explicit index truncation on long titles; fixed byte budgeting. Acceptance helper now proves a copied directory is rejected before exact disposable re-enrollment.
- Version0.1.1 canonical plus all owned current build/docs examples synchronized. Added source Git/ZIP upgrade, stopped full backup, rollback-image preservation, rebuild/version/real-connectivity checks and precise storage-identity reset. README and changelog point to published upgrade anchor; no tag/release claimed.
- Affected backend78passed/1Windows symlink privilege skip; version9passed; acceptance CLI7passed plus6embedded helper syntax checks; Ruff/mypy36 passed. Final frontend lint/build/typecheck passed; final docs build23Markdown/102links/18pages passed. Prior duration unit108passed retained without rerun. Independent review has no outstanding code findings.
- No real Docker upgrade/rollback or Telegram validation, container update, user-data cleanup, commit or push performed. Parent task remains in_progress.
## 2026-10-28: Friendly Bot message presentation

- Added sparse emoji and readable Chinese to default confirmation, welcome/help/status, queue/result messages, buttons and existing callbacks (including expiration/repeat states). Shared Bot-only status labels and fixed actionable failure hints replace enum/raw error text; task title/path/state/protocol/notification pacing and full separate ID line remain unchanged.
- Backend/frontend default confirmation and documentation examples agree. Saved custom templates and queued messages are preserved; existing users explicitly restore default and save to adopt the new card style.
- Focused backend98cases covered (97passed initially; corrected one new fixture and reran its case successfully). Frontend template28passed; Ruff, mypy37, frontend lint/typecheck/build passed. Independent reviewer added2non-BMP overflow tests at750/3900UTF16 units, both passed and no outstanding findings. Docs final23Markdown/102links/18pages passed; diff check passed.
- No Docker update, real Telegram run, commit/push or new version increment for this pending0.1.1 change. Previous pending work preserved; parent remains in_progress.
## 2026-10-28: Prominent Telegram account risk documentation

- User requested conspicuous ban-risk notices. Added GitHub README warning and VitePress entry-page warning/danger blocks before setup; FAQ account-risk anchor is the canonical explanation. States possible Bot and associated owner account restriction/bans, no cloud/local/proxy/pacing immunity or incident-specific causal diagnosis, and brief practical precautions/official support.
- Documentation-only scope; no runtime/version/deployment changes. Updated PRD, documentation spec and changelog. Standalone docs build23Markdown/106links/18pages passed; independent review verified generated warning blocks, links and stable FAQ anchor, no findings. Application tests are unnecessary for Markdown-only edits.
- Prior authorized work committed/pushed as cb6e108 before this increment. Current risk-doc edits remain uncommitted/unpushed; no container update or Telegram action. Parent task remains in_progress.
## 2026-10-28: Dedicated version upgrade menu

- Moved upgrade/rollback and storage-enrollment instructions from operations to docs/upgrade.md with standalone top-level version-upgrade sidebar item, preserving upgrade and storage-identity-011 anchors. Operations retains maintenance plus concise upgrade pointer; README/changelog/deployment/acceptance targets now use dedicated route, moved backup reference points back to operations.
- Pending risk warnings preserved. PRD and documentation contract updated; version0.1.1/runtime unchanged. Docs build24Markdown/108relative links/19pages and28published README/changelog URL+anchor checks passed; diff check passed. Independent review verified moved-body parity, generated navigation/anchors, updated links and preserved risk callouts; no findings.
- Current documentation changes remain uncommitted/unpushed; no container/deployment action. Parent task remains in_progress.
