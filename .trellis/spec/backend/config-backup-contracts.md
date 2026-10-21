# Configuration backup and restore

## 1. Scope / Trigger

Read when editing portable settings backup, restore endpoints or WebUI flows.
This feature exports configuration only, not a database or full-instance backup.

## 2. Signatures

- `POST /api/v1/settings/backup/export {password}` returns `{backup,excluded_fields}`.
- `POST /api/v1/settings/backup/preview {password,backup}` returns revision,
  created_at, included_fields, skipped_fields, warnings, configured flags and
  non-sensitive summary (mode/root/concurrency/allowed-user count).
- `POST /api/v1/settings/backup/restore {password,backup,revision}` returns the
  ordinary redacted SettingsResponse.
- Strict envelope: format `tgdl-settings`, version 1, kdf `scrypt-16384-8-1`,
  standard-base64 salt/nonce/ciphertext. No additional fields or supplied KDF costs.

## 3. Contracts

All endpoints require session, same-origin checks and CSRF. Password6..128 Unicode
characters, independent of administrator credentials, never stored. AES-256-GCM,
random16-byte salt and12-byte nonce, fixed authenticated additional data;
scrypt N16384/r8/p1 derives32 bytes. Nonblocking per-service semaphore bounds KDF
concurrency to one. Request stream cap128KiB; decoded payload cap64KiB.

Encrypted payload contains created_at, complete current UserSettings fields minus
explicit environment exclusions, and excluded_fields. Export excludes environment
values including secrets. Decrypt validates exact field coverage, strict settings
and timestamp even for fields the target environment will override. Preview never
contacts Telegram or writes settings. Report source exclusions and target
environment overrides as skipped; preserve target current values for those fields.

Restore uses candidate/save under runtime.connection_lock, revision guard and
normal API transition scheduling/log policy maintenance. Empty imported secrets
mean clear, not ordinary form blank-preservation. Target root ID and mounted
directory must exist. Never restore administrator/session, jobs/history, files,
database/master key, diagnostics, API cache, cooldown or runtime state. Do not
mark restored local settings as verified/applied.

WebUI exports saved configuration, explains exclusions, previews with no decrypted
secrets and requires explicit overwrite confirmation. Preserve409 edits. Clear
passwords on dialog close/unmount, revoke download URLs; no localStorage.

## 4. Validation & Error Matrix

| Condition | Result |
| --- | --- |
| Missing auth / bad CSRF or Origin |401 /403 |
| Oversize request |413 BACKUP_TOO_LARGE |
| Wrong password/corrupt payload |422 INVALID_BACKUP; no partial write |
| Invalid envelope/password/schema |422; no raw input echo |
| Concurrent expensive KDF |429 BACKUP_BUSY |
| Target root unavailable |422; preserve settings |
| Revision changed after preview |409; re-read and preview |

## 5. Good / Base / Bad Cases

Good: export with secrets, move to a fresh initialized instance, preview and
restore editable settings while preserving target environment and administrator.
Base: encrypted roundtrip of current saved settings offline. Bad: exporting a
database/key file or resetting Telegram protection while restoring configuration.

## 6. Tests Required

Encryption randomness and roundtrip, wrong password/tampering/version/bounds,
auth/origin/CSRF, bounded KDF, exact empty-secret clears, revision atomicity,
source/target environment exclusions, missing roots, task/media/protection
preservation. Cross-instance portability must restore secrets under a different
target master key while preserving its administrator, session and master key.
Browser desktop/mobile export/download/upload/preview/confirm,
dirty form guard, password cleanup and current form refresh after restore.

## 7. Wrong vs Correct

Wrong: restore via direct Settings SQL writes or display decrypted Token in preview.
Correct: validate the complete payload then use normal transactional save and
redacted summaries. A settings backup cannot substitute for full stopped backup.
