# Research: Remove unpublished-version compatibility

- Query: Inventory obsolete project compatibility paths and recommend clean first-release contracts without removing current runtime safety.
- Scope: internal
- Date: 2026-10-21

## Findings

### 1. Confirmed runtime compatibility to remove

| Files / pattern | Finding | Recommended current contract |
| --- | --- | --- |
| `backend/app/core/templates.py:4-8` | `LEGACY_CONFIRMATION_TEMPLATE` and `{expires_in}` seconds alias remain supported. | Only current default and `{expires_in_minutes}`. Reject old alias as an unknown variable; no silent template rewriting. |
| `backend/app/services/settings.py:35-53` | Constructor backfills absent mode as local, and rewrites an exactly matching old default template while bumping revision. | Create singleton with current cloud default; read absent optional configuration through current defaults; no old-template upgrade. |
| `backend/app/schemas/contracts.py:38` | Standalone `UserSettings` still defaults local although fresh settings singleton defaults cloud. | Cloud default consistently in schema, singleton, UI and saved-current-mode comparisons. Test fixtures needing local must select local explicitly. |
| `backend/app/services/tasks.py:128` | Supplies old seconds variable to render context. | Supply minute variable only. |
| `frontend/src/views/SettingsView.vue:219` | Visible old-template compatibility disclosure. | Delete disclosure entirely. Keep concise current supported-variable help. |
| `frontend/src/api/templates.ts:68` | Preview context still provides obsolete seconds variable. | Remove old context value with backend alias. |
| `backend/app/storage/naming.py:35` and `backend/app/services/tasks.py:167` | Missing planned filename on a pending card falls back to pre-snapshot naming. | Confirm only with nonempty frozen planned filename; reject invalid/incomplete card safely instead of inventing a new name. `planned_filename` must remain nullable while media is collecting. |
| `backend/app/services/settings.py:70-86` | Bare `TELEGRAM_API_ID/HASH` environment variables are editable fallback; response exposes `deployment_fallback`. | Application credentials come from WebUI database. Remove fallback loop and source label; explicitly decide whether TGD-prefixed ID/Hash remain forbidden or supported as ordinary documented overrides. Do not accidentally introduce a new override by deleting the skip alone. |
| `backend/app/services/local_api.py:46-48` | Connection change comparison falls back to bare API environment credentials; missing mode assumes local. | Compare canonical current defaults and database credentials. No bare-environment fallback. |
| `frontend/src/views/SettingsView.vue:166`, `frontend/src/components/SettingsSource.vue:5`, `backend/app/schemas/responses.py:33` | UI/API preserve `deployment_fallback` source. | Sources: `default`, `database`, `environment` only. Remove hint/tag/literal together. |
| `backend/app/services/local_api.py:34,125-135,185,205-206` | Legacy desired-local database starts with unknown applied mode and `LOCAL_API_ACTIVATION_REQUIRED`; restart denied until explicit activation. | Current initial state starts cloud/idle. Current settings save schedules managed local transition. Remove old install activation code/message/action restrictions. Keep interrupted logout handling. |
| `backend/app/services/local_api.py:334` | Comment describes normal desired/applied reconciliation as legacy installation handling. | Keep general reconciliation for durable transition recovery; remove obsolete rationale. |

### 2. Duration representation is a design choice, not inherently compatibility

`confirmation_timeout_seconds` is the existing API/storage field (`contracts.py:41,95`), converted to minutes by `SettingsView.vue:52-55` and used to compute epoch deadlines (`tasks.py:118`). Internal seconds and existing fractional-minute range 0.5–1440 are useful current behavior; they do not alone constitute an old-version branch.

The old-specific code is the preservation commentary/test for arbitrary historical values such as 31 seconds (`SettingsView.vue:51`, `frontend/e2e/acceptance.spec.ts:192`, frontend spec lines 14-24), and the separate template alias. Recommended minimum scope: keep current seconds DTO and minute UI, delete compatibility prose and replace legacy-preservation tests with current minute input/persistence/default tests. A wholesale rename to integer `confirmation_timeout_minutes` is possible but changes user-supported 0.5 minutes and touches schema/services, frontend types/defaults, test fixtures, `scripts/acceptance.py:166-169`, `scripts/docker_acceptance.py:136,178`, and deployment override contract. Make that choice explicitly rather than calling seconds itself legacy.

### 3. Migration history can be squashed before the first release

Current chain:

- `backend/migrations/versions/0001_initial.py`: original schema.
- `0002_acquisition_mode.py`: local server-default backfill for existing jobs.
- `0003_planned_filename.py`: nullable snapshot column.
- `0004_caption_snippet.py`: nullable caption column.
- `0005_task_deleted_at.py`: soft-deletion timestamp.
- `0006_local_api_runtime.py`: runtime transition table.
- `0007_request_safety.py`: request protection table.

All resulting fields/tables remain current product requirements. Their intermediate upgrade/downgrade paths are unpublished-version history. Clean option: retain Alembic startup framework (`backend/app/main.py:59-63`) but replace chain with a new initial baseline containing the current schema. Do not import mutable ORM metadata into a migration; define the baseline schema explicitly. Verify fresh empty database -> head and `compare_metadata(...) == []`, plus repeat startup idempotence. Existing DBs stamped 0007 cannot use the new chain without explicit offline reset; do not silently reset a production database on startup.

Old-revision migration tests to replace with current-baseline schema tests:

- `backend/tests/test_cloud_api.py:306` downgrades to 0001 then tests existing-job backfill.
- `backend/tests/test_templates.py:200` downgrades to 0002 then checks null planned names.
- `backend/tests/test_archive_naming.py:107` downgrades to 0003 for caption upgrade.
- `backend/tests/test_task_deletion.py:202` downgrades to 0004 for soft-deletion upgrade.

Current job acquisition mode is real transport ownership, not legacy baggage. Keep it and its crash-recovery tests. Its ORM/server default currently local (`backend/app/db/models.py:122`); worker claim persists actual transport (`backend/app/workers/download.py:174`). Consider consistent cloud default or explicit ownership at creation; do not remove the field.

### 4. Test changes / necessary retained assertions

- `backend/tests/test_templates.py:26,41`: remove old-template upgrade and environment old-alias preservation tests; add old alias rejection, current defaults and new-template roundtrip.
- `test_templates.py:58`: preserve issued-card deadline and text snapshot assertion, replace mixed old/new alias fixture with minute-only template.
- `test_templates.py:137-174`: preserve freeze-time names across settings changes, confirmation idempotence and retry invariance; remove simulated pre-0003 null-name fallback and expect safe rejection for missing snapshot separately.
- `backend/tests/test_cloud_api.py:218`: split fresh cloud default test from busy-download/recovery guard; remove absent-mode→local expectation.
- `backend/tests/test_local_api.py:439`: remove bare-env fallback assertion, retain unrelated-save-does-not-restart coverage.
- `backend/tests/test_bot_feedback.py:154`: replace manually injected monthly `videos/2026-10` path fixture with current day-first archive; retain frozen path invariance after confirmation/retry.
- `backend/tests/test_archive_naming.py:123`: replace `旧标题 #abcdef01` fixture/name with a current arbitrary frozen title. Retain full-ID budget, multi-status and immutable stored consent assertions.
- `backend/tests/test_local_api.py:91` `test_external_compatibility` only checks managed=false status. This is a current deployment capability, not proven old-version support. Rename to external API status if external administration remains supported; do not delete all managed=false runtime guards merely because of this test name.
- `backend/tools/e2e_app.py:17` clears bare API env from isolated test process: harmless environment isolation, can simplify after environment contract removed.

### 5. Documentation/spec inventory

- `docs/templates.md:30,41`: delete old variable and auto-upgrade explanation; document only current minute variable/range and current card snapshot behavior.
- `.trellis/spec/backend/template-contracts.md:13,28-33,75-77,107`: delete legacy migration/alias/fallback tests; retain snapshot invariants.
- `.trellis/spec/backend/cloud-api-contracts.md:10,23-24,82`: remove old external/manual migration wording if obsolete, fresh/legacy default split and legacy checks; align consistent cloud default.
- `.trellis/spec/backend/managed-api-contracts.md:28-34`: delete editable env fallback and first-install activation policy. Existing managed=false support must be assessed as current design, not removed by keyword.
- `.trellis/spec/frontend/ui-contracts.md:14-24,35`: remove old-seconds/alias and legacy-local language; keep current units/source/read-only/unsaved-input semantics.
- `.trellis/spec/backend/runtime-contracts.md:6,13`: update migration numbers if squashed.
- `docs/project-requirements.md:330` explicitly promises older longer-password login. `backend/app/schemas/contracts.py:122-130` accepts login 1–256 while creation is 6–20; `frontend/src/views/AuthView.vue` checks 6–20 only setup. Standard unrestricted credential submission is normal login semantics, but the explicit backward-compatibility promise should go. If enforcing a universal 6–20 login contract, update frontend/API tests intentionally.
- `docs/development.md:7`, `docs/project-requirements.md:38`, `.trellis/spec/guides/index.md:85-91`: older preservation policy conflicts with latest user authorization. Replace with permission to explicitly clean incompatible unpublished test state, preserving independent media/security concerns. Do not rewrite historical session journals or archived findings as if past decisions never occurred.
- `CHANGELOG.md:16` first-release no-compatibility promise is current policy; keep it.

### 6. False positives / required current safety

Do NOT remove these as compatibility:

- `backend/app/bot/ingest.py:62`: Telegram itself exposes animation as document for backwards compatibility; rejecting duplicate representation remains correct upstream protocol behavior. Rephrase comment to explain duplicate media if eliminating confusing terminology.
- `deploy/bot-api/supervisor.py:170`: passes `TELEGRAM_API_ID/HASH` to the fixed official API child. This is the current secret handoff, distinct from reading those variables as an application settings fallback; keep it and supervisor tests.
- Safe filename/title/extension fallbacks in `storage/files.py`, `storage/naming.py`: handle ordinary incoming media with absent names, not old project versions.
- Persisted caption/title/destination/planned-name/job-path snapshots: prove the current user-confirmed destination and protect against unrelated configuration edits, retries and application crashes.
- `logging_out` -> durable `logout_uncertain` on app restart, successful logout preservation, server cooldowns, RequestSafety, phase/claim barriers and recovery-blocked acquisitions: current correctness and account-risk controls.
- Network fail-closed/no-direct-fallback conditions: security/current feature, not compatibility.
- `getattr(os, 'O_NOFOLLOW', ...)`, POSIX/Windows checks: platform support, not historical-version compatibility.
- Dependency `character-entities-legacy` in docs lockfile and third-party sing-box configuration compatibility: upstream package concepts, not project historical support.
- Public documentation paths describing matching/compatible mounts or UID ownership: current Docker mechanics.

### 7. Incompatible runtime data cleanup boundaries

No runtime inspection/mutation was performed. Latest user authorizes cleaning incompatible old data, but an automatic blanket deletion is not a sensible startup contract. Keep cleanup an explicit local deployment maintenance action.

If migration chain is reset, old test application database must be removed/recreated offline with a configuration preservation plan. Config-only backup explicitly does not carry RequestSafety or LocalApiRuntime; restoring it into a fresh DB must not fabricate verified local status or replay an uncertain cloud logout. For a currently connected local Bot, blindly deleting runtime/safety and restoring local settings can trigger a new cloud preflight/logout. Arrange known-state transition or preserve those independent current safety records in private maintenance handling. Media downloads must not be deleted merely because configuration/database changed. Existing stopped full backup can be used for rollback, but normal user-facing settings backups must remain settings-only.

## Related specs

- `.trellis/spec/backend/template-contracts.md`
- `.trellis/spec/backend/cloud-api-contracts.md`
- `.trellis/spec/backend/managed-api-contracts.md`
- `.trellis/spec/backend/request-safety-contracts.md`
- `.trellis/spec/backend/runtime-contracts.md`
- `.trellis/spec/frontend/ui-contracts.md`
- `.trellis/spec/guides/index.md`

## External references

None needed for this internal inventory. The official child environment names were confirmed from this repository's current supervisor and its tests, not inferred from public documentation.

## Caveats / Not Found

- Active task has no `research.jsonl`; research-only context loaded from supplied hook and project/task artifacts. Implementation/check manifests deliberately not loaded.
- No real runtime paths, media-cache directories, credentials, bot probes or Git operations were inspected/executed.
- There is no evidence that the user's TUN distinction can be solved merely by removing legacy branches; proxy architecture is a separate investigation.
- This is a removal map, not an implementation or runtime cleanup record.
