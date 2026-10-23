# Research: Local Bot API media cache cleanup

- Query: Safely release a downloaded local Bot API media file after durable final publication, without deleting API runtime data or leaving TDLib with a moved file.
- Scope: mixed; repository contracts plus the pinned official Bot API and TDLib sources already available in a local build image.
- Date: 2026-10-23

## Findings

### Source and version evidence

- `deploy/bot-api/Dockerfile:5` pins Bot API `e3e9dd8e5b3d7ab8537cd5a10dc31d5ffa8f82d1`; source submodule HEAD is TDLib `bc9c263e2bfee06aaab41e82db51a103376030bc`.
- Existing image `tgd-native-progress-build:latest` contains `/src`, `/src/td`, `/src/build` and the already compiled dependencies. Read-only image inspection confirmed its presence. A stopped, network-none temporary container was used solely for `docker cp` streams into memory; no process or Telegram request was started.
- `deploy/bot-api/native-progress.patch:6` routes a private method only to an already-existing local Client, avoiding creation or upstream request statistics. Its Client-side early dispatch bypasses normal Bot API processing; use this structure for a private cleanup method, but do not turn the read-only progress method itself into a mutation.
- `deploy/bot-api/ProgressCache.h:34` holds bounded ephemeral aliases and expires terminal entries after 60 seconds. It cannot be the durable cleanup authority across retries or child restarts.

### Official method distinction

There is no public Bot API `deleteFile` route in the pinned official `Client.cpp` method registry (`:406` registers `getfile`; no `deletefile` registration). TDLib has a different API: `td_api.tl:14030` explicitly documents `deleteFile file_id:int32 = Ok` as deletion from the TDLib file cache. This must be invoked inside the existing authenticated TDLib Client, not sent to cloud Bot API and not confused with deleting Telegram messages.

### Exact TDLib semantics

- `td_api.tl:11586` documents `getRemoteFile` as an offline method. Remote IDs persist across application restarts (`td_api.tl:295`); integer native file IDs must not be reused across generations.
- `Requests.cpp:2957` implements `getRemoteFile` by `from_persistent_id(remote_alias, type)` then `get_file_object`. It does not call `downloadFile` or contact Telegram.
- `FileManager.cpp:3994` decodes the persistent ID; `:4047` reconstructs a remote location and registers it. `:4103` gets a synchronous file node, which loads the file database (`:2696`), so this path can discover the current local cache location after a restart without downloading.
- `Requests.cpp:6524` dispatches `deleteFile` to FileManager without a user-only guard, so the authenticated Bot TDLib instance can use it.
- `FileManager.cpp:2915` loads the node, limits full-file deletion to TDLib's own file-type directory, clears the local file persistence, drops the local location, flushes the node, and asks FileLoadManager to unlink the old path. It does not delete authentication databases or the remote media identity.
- **Important:** `FileLoadManager.cpp:22` is `unlink(file_path).ignore(); promise.set_value(Unit());`. Therefore, **a successful TDLib delete callback alone is not proof of released disk space**. Verify the original path no longer exists; TDLib's now-empty path alone is insufficient. On an unlink failure TDLib can already have dropped its local reference, so keep the original path in a cleanup record for diagnosis/reconciliation.
- **Important:** FileManager.delete_file itself does not reject an active download. Application and native admission guards must prevent deleting a file that is still in use.

### Existing application durability and concurrency

- `backend/app/workers/download.py:341` owns the only acquisition `get_file`.
- `:359` validates the shared-cache source; `:361` copies it to a destination-side temporary file.
- `:365` onward records a ready journal, hashes/size checks, and publishes through `publish`.
- `:270` onward handles exclusive publication and commits StoredFile/job state. Cleanup must be journaled atomically with successful publication, or crash recovery must create its cleanup record from a recovered publication. Do not delete immediately after a copy before the durable publication transaction.
- `backend/app/storage/files.py:109` verifies copied size/hash and fsyncs; `:132` exclusively links to the final destination and fsyncs its parent. Retain this flow. Directly moving a TDLib file bypasses these guarantees, fails across mount boundaries, and leaves TDLib metadata stale.

### Minimal proposed native method

Use a distinct private local capability, for example `tgdDeleteCachedFile`, under the existing ClientManager/no-client/no-local guard. Require an existing authorized, nonclosing Client and current child generation. Input is a bounded Bot remote file alias plus expected cache path and, if available, expected remote unique ID/size. Do not accept an arbitrary path to unlink.

1. Validate expected generation and all input lengths; never echo token or paths into diagnostic errors.
2. Resolve alias through TDLib `getRemoteFile` (offline). Obtain the **current** native ID and location; do not reuse a persisted integer ID or ProgressCache alias map.
3. Reject active download/upload, any pending Bot API file download listeners, noncompleted/unowned paths, mismatched path/size/unique ID, closing Client or changed generation. If no cache exists, return an explicit already-absent outcome without reacquiring it.
4. Mark a bounded Client-side deletion-in-flight guard before sending `deleteFile`; reject new `getFile`/cleanup overlap until the callback and postcheck finish. A Client-wide guard is simpler and protects alternate aliases sharing a file. An application-only pause assumes there is no second consumer of the private Bot API; the native guard closes callback scheduling races.
5. Invoke TDLib `deleteFile(current_native_id)`.
6. In its result callback, validate the original path's absence through bounded filesystem stat/lstat; optionally fetch the offline file object again to verify local metadata. Return only a sanitized fixed outcome such as `deleted`, `already_absent`, `busy`, `path_changed`, `not_owned`, or `failed`. The postcheck does not itself unlink arbitrary paths.

This does **not** require a new acquisition lease protocol if the app pauses local claims, drains all running local acquisition/saving owners, and serializes cleanup before resuming claims. The current managed-service generation can serve as the process guard. Durable cleanup rows use the Bot identity and remote alias, expected source path and successful-publication evidence; on restart they resolve the alias afresh using the current verified child. Never blindly reuse old generation/native IDs.

### Recovery and edge cases

- Simultaneous same-file jobs: drain **all** local owners, not just the completed job; one alias can represent a shared cached file. Prefer cleanup while idle before claiming new local work. Pending queued jobs can reacquire later, or normal final-file dedup can avoid reacquisition.
- Cancellation: do not clean a source before the copy thread is joined; uncertain acquisition must retain its existing protection and slot. Normal completed publication remains successful if cleanup temporarily fails; expose pending/retry state instead of falsely claiming the cache is released.
- Cleanup timeout/transport loss: the native mutation may still be in flight. Do not resume claims merely because the HTTP timeout elapsed; retain an uncertainty barrier until the operation is reconciled or the managed child is safely replaced. Bound cooperative work and surface a manual recovery action rather than an endless silent lock.
- App crash: queue publication evidence durably, process pending rows after restart, resolve alias offline, compare current location, and accept already-absent. If it now refers to a different path/content, do not delete the replacement.
- Re-download/new generation: new current cache identity must be checked. A generation change invalidates an in-flight response; a durable row can be retried under the new generation only after fresh offline resolution and normal ownership checks.
- TDLib unlink failure: retain exact old source location in the private cleanup row. Retrying TDLib may return already-absent metadata while orphan bytes remain. Report this condition; do not infer released space from empty local metadata or silently clear pending state.
- Different filesystem: temporary duplicate space is still necessary during the verified copy. The intended user outcome is no retained duplicate after successful publication and confirmed cleanup, not zero transient duplicate bytes.
- Unmanaged stock Bot API lacks the private mutation. Detect capability, report cleanup unsupported, and never unlink behind its back or send the private method to Telegram cloud.

### Focused validation and resource reuse

Use the existing pinned build stage/cache and `cmake --build /src/build --parallel 2` for an incremental native compile. Production Dockerfile already layers proxy compilation before the progress patch, so add the cleanup patch after those to reuse expensive TDLib compilation. Existing isolated verifier patterns are `deploy/bot-api/Dockerfile.verify-progress`, `test-progress-cache.cpp`, and `verify-native-progress.py`; `--network none` plus strace proves no outbound connects and no Client creation for unsupported/missing-client cleanup requests. No real token or production media is needed.

Native tests should cover malformed/oversize inputs, missing Client, mismatched generation/path, same file under multiple aliases, listener/active/uploading rejection, new getFile during deletion, successful unlink, ignored-unlink failure, and absent/no-op metadata. Worker tests should cover durable queue creation with publication, crashes before/after delete, sharing, cancellation, retry, timeout uncertainty, claim pause/resume and progress/safety independence. Synthetic restart tests must actually change native generation and preserve only durable remote alias state.

## Related specs

- `.trellis/spec/backend/local-progress-contracts.md`: generation proof, private transport limits, no extra acquisition, observations separate from task success.
- `.trellis/spec/backend/request-safety-contracts.md`: local private operations must not masquerade as Telegram success or clear flood/account protection.
- `.trellis/spec/backend/runtime-contracts.md`: destination publication, shared cache mounts and task ownership.
- `.trellis/spec/backend/managed-api-contracts.md`: generation, process replacement and uncertainty protection.

## External references

- https://github.com/tdlib/telegram-bot-api/tree/e3e9dd8e5b3d7ab8537cd5a10dc31d5ffa8f82d1
- https://github.com/tdlib/td/tree/bc9c263e2bfee06aaab41e82db51a103376030bc
- https://core.telegram.org/tdlib/docs/classtd_1_1td__api_1_1delete_file.html
- https://core.telegram.org/tdlib/docs/classtd_1_1td__api_1_1get_remote_file.html

## Caveats / Not Found

- Raw GitHub fetch was unavailable during research; exact pinned source was read from the existing local build image instead. External documentation URLs above are references, not newly fetched evidence.
- No native compile, deployed container change, Telegram request or production media read was performed by this research task.
- A synthetic no-client route test cannot prove real authenticated deletion or released disk blocks. That needs a controlled fixture or a separately authorized real-media acceptance case.
- An open descriptor held by another process or filesystem snapshots/reflinks can retain disk blocks even after a directory entry disappears; application owner draining and avoiding cache open handles remain necessary.
