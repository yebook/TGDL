# Local media cache cleanup

Read for local cache lifecycle, publication/recovery, native cleanup or runtime dispatch changes.

## Contract

The API cache is TDLib-owned. Never move/unlink its media from the app, make its app mount writable, or delete its runtime/authentication data. Copy/hash/fsync/publish through the existing journal first; final media are never removed by cache cleanup.

Persist pending cleanup atomically with local job success, including journal recovery. Capture the API source identity before publication. Cleanup failure cannot downgrade success, consume a download retry or repeat getFile. Cloud downloads do not enqueue local cleanup. Deleting a task record does not discard pending cache work.

An absolute API source path can contain the Bot Token in its directory name.
Encrypt it with the existing deployment `Secrets` key before journal/queue
persistence, carry ciphertext through recovery, and decrypt only for the private
local cleanup call. Do not store plaintext paths/tokens or add a plaintext fallback.

Run cleanup only for the currently verified Bot and fresh acknowledged local child generation, with no connection transition or local readers/uncertain owners. Pause new local dispatch while draining; use bounded cleanup batches/backoff so unsupported methods and network errors do not permanently lock downloads. Before cleanup revalidate the final stored file and storage identity; missing, changed or inaccessible final output retains the only cache copy.

Housekeeping uses storage identity validation without the download free-space
admission threshold; low space must not prevent releasing a verified cache.
Hash large final files outside the connection lock while retaining the claim
barrier; recheck final fingerprint, generation, client and owners before mutation.
Open final hash inputs with no-follow/nonblocking flags and verify the opened
descriptor is a regular file; pathname stat alone cannot prevent a FIFO replacement
from blocking cleanup and shutdown forever. Retain normal hash behavior for other
callers and share the streaming hash implementation rather than duplicating it.

The private native POST `tgdDeleteFileCache` accepts `file_id`, `expected_path`, `expected_size`, `generation`; result is `{generation,status:deleted|absent}`. It bypasses Telegram request pacing because it is local offline housekeeping; never modify request-safety state. Bound HTTP timeout/body, forbid redirects/environment proxy inheritance, validate the exact generation/result, and sanitize diagnostics.

Require an existing authorized local Client before dispatch, exact child generation and bounded arguments. Offline getRemoteFile resolves a persistent alias across restarts; do not create a Client, download, log in, log out or invoke network methods. Compare expected cache identity/path/size; reject active download/upload/listeners and nondeletable files. Hold per-native-file exclusion through deleteFile and its completion so late/time-out cleanup cannot race a new getFile. TDLib deleteFile's successful callback alone is insufficient: verify the original expected cache path is absent. Never accept arbitrary path deletion.

## Verification

Use temporary DB/files/fake transports for successful publication, interrupted recovery, cloud exclusion, failed/cancelled copies, changed/missing final files, task deletion, duplicate aliases, busy readers, stale generation, malformed responses/timeouts and durable independent retry. Verify the actual pinned native patch compiles and absent-client routes remain offline. No real Telegram credentials, user cache sweeps or production container changes in automated tests. Record authenticated TDLib/file-removal real-environment acceptance separately.
