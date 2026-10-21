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
