# Download root identity without output markers

## 1. Scope / Trigger

Read when editing `Storage`, root availability, publication/recovery checks or
backup acceptance. Keep persistent control metadata out of the media directory.

## 2. Signatures

`Storage(root, data_dir, minimum_free, root_id="default")` owns
`initialize()` and `check(required=0, *, check_space=True)`. Identity metadata
belongs to `data_dir/storage-identity-<root-id-hash>.json`, not the download root.
No API, database schema or deployment variable is added.

## 3. Contracts

Enroll only an existing nonsymlink directory. Persist its filesystem device and
directory inode, and validate both before storage operations. Do not create
`.tgd-root`, recreate a missing root, choose another directory or automatically
re-enroll a changed root. Private metadata remains in the application data root.
Real in-progress media temporary files are still required for atomic publication.

There is no unpublished-format compatibility path. An old or malformed identity
must fail closed with actionable guidance. Only after stopping the app and
confirming the intended mount may an operator remove the exact affected identity
JSON to enroll it again. Preserve the database, keys, media and unrelated roots.
An old `.tgd-root` is no longer used; leave unknown files untouched rather than
automatically deleting them.

## 4. Validation & Error Matrix

| Condition | Behavior |
| --- | --- |
| Existing correct root | Check identity and normal free-space threshold |
| Missing root or root symlink | Storage unavailable; never recreate |
| Device or directory inode changed | Block storage; no automatic reset |
| Old/malformed identity | Actionable storage error; no raw parsing exception |
| Low space, `check_space=True` | Preserve existing download pause behavior |
| `check_space=False` | Skip only free-space threshold; still validate identity |

## 5. Good / Base / Bad Cases

Good: initialization writes only application metadata; media creation leaves the
registered directory identity unchanged. Base: repeated checks and app restarts
reuse that identity. Bad: an empty replacement directory on the same filesystem
is silently accepted, or deleting an old marker becomes a prerequisite for writes.

## 6. Tests Required

Assert artifact-free enrollment, restart stability, same-device replacement,
missing/symlink roots, malformed metadata and fail-closed cache cleanup. Backup
acceptance checks copied identity metadata rather than nonexistent marker bytes;
a copied disposable download directory requires explicit verified re-enrollment
because its inode differs. Directory identity depends on
filesystem semantics; do not claim untested network filesystem guarantees.

## 7. Wrong vs Correct

Wrong: store a token in downloads or regenerate metadata after every mismatch.
Correct: store device/inode under application data and require a deliberate,
verified re-enrollment after an actual root change.
