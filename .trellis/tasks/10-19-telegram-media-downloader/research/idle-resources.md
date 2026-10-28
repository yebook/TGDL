# Research: idle memory and resource baseline

- Query: Why does an idle deployment use noticeable memory and CPU, and which changes are safe without native recompilation?
- Scope: internal source audit and credential-free Windows import measurement
- Date: 2026-10-27

## Findings

### Observed deployment and measurement limits

The main session relayed user-observed local-mode statistics: app approximately 207 MB / 2.67% CPU, bot-api approximately 35 MB / 1.85% CPU. These measurements were not independently reproduced by this researcher. Docker is unavailable in this environment, so there is no Linux RSS/PSS, allocator, native thread count, or real idle-before/after evidence. Do not claim a native leak or measured container saving.

A fresh backend virtual-environment Python process imported modules sequentially without application startup, configuration loading, Telegram requests, or touching data. Windows `GetProcessMemoryInfo` returned:

| Last imported module | Working set MiB | Private commit MiB | Loaded modules |
| --- | ---: | ---: | ---: |
| Python baseline | 15.18 | 7.86 | 73 |
| fastapi | 40.68 | 28.78 | 399 |
| sqlalchemy | 56.23 | 40.41 | 486 |
| alembic | 66.45 | 51.23 | 627 |
| cryptography.fernet | 68.40 | 51.36 | 650 |
| argon2 | 68.56 | 51.39 | 660 |
| aiogram | 203.72 | 185.56 | 1533 |
| app.main | 218.14 | 199.47 | 1696 |

Order is cumulative; deltas are not independent standalone library costs. Windows working set is not Docker memory accounting. Nevertheless, the approximately 135 MiB increment on aiogram import and roughly 218 MiB application-import baseline explain why a no-task app can remain around 200 MB without a media leak.

### Files found and code patterns

- `backend/app/bot/client.py:13`: eagerly imports aiogram Bot/session/exceptions/methods/types. Installed `aiogram/__init__.py` imports its method/type families and dispatcher; installed `aiogram/types/__init__.py` eagerly imports many model classes. Importing a narrower aiogram submodule still executes the package initializer. Merely changing import spelling will not avoid the baseline.
- `backend/app/workers/runtime.py:6`: aiogram exceptions are also imported outside the Telegram client, as are exceptions in request protection paths. Lazy-loading a single client import would not solve baseline and would only benefit an unconfigured instance if all eager dependencies were refactored.
- `backend/app/bot/client.py:161`: `getUpdates` already uses 25-second Telegram long polling. Preserve it instead of making frequent upstream polling calls.
- `backend/app/bot/client.py:248`: cloud files stream in 64 KiB blocks. `backend/app/storage/files.py:107` and `:132` hash/copy in 1 MiB blocks; media are not deliberately loaded whole into memory.
- `backend/app/services/download_progress.py:76`: Python progress observations have capacity 16 and a four-second projection TTL; `:107` clears entries on ownership completion.
- `backend/app/workers/download.py:426`: job cleanup clears progress observations; `:427` removes per-job progress timing entries in `finally`.
- `deploy/bot-api/ProgressCache.h:32`: native progress cache is bounded to 128 files and 1024 aliases, each alias at most 4096 bytes; completed entries expire after 60 seconds (`:50`) and prune on subsequent reads/binds. Not an unbounded idle media cache.
- `backend/app/services/diagnostics.py:124` and `:209`: diagnostic failure/dedup dictionaries are bounded to 256 keys; export uses `deque(maxlen=1000)`. Persistent log file capacity is disk usage, not a permanently loaded log buffer.
- `backend/app/db/session.py:11`: normal SQLite SQLAlchemy pooling and WAL, no explicit giant memory cache. Pool/cache trimming should be measured under Linux before changing concurrency behavior.
- `backend/app/main.py:79` and `:221`: login attempts retain one dictionary key per source IP with per-key expired events removed only when that IP retries. This is a small potential long-lived growth source on a publicly exposed instance, not an explanation for the personal idle baseline. A bounded expiring limiter would be a separate hardening improvement.
- `backend/app/core/security.py:10`: Argon2 default hasher allocations occur during authentication rather than a persistent media queue. Do not weaken password hashing to reduce memory.
- `backend/app/services/config_backup.py:33`: backup scrypt derivation already permits only one concurrent KDF; n=16384,r=8,p=1 at `:39` implies about 16 MiB temporary KDF memory, not baseline idle memory.
- `deploy/bot-api/supervisor.py:30`: fixed native command has no application-specified thread/cache/allocator flags. Native internals are the pinned compiled binary and linked TDLib, not editable Python settings.
- `deploy/bot-api/supervisor.py:241`: stop desired state terminates the native child. Main/runtime must continue using the existing managed mode-switch handshake rather than stopping a local process merely because download tasks are empty: Telegram intake still needs local API while configured local.

### Recommendations

1. Prioritize repeated idle database reads, control-file serialization/fsync, credential decryption, short sleeps, and repeated unchanged state publication. These can explain unnecessary idle CPU and allocator churn without reducing actual Telegram intake responsiveness. The implement agent owns this investigation/change.
2. Be candid that this scoped work cannot promise a large memory reduction: the observed app memory is consistent with the SDK/framework import baseline. Keep the native bundle and source fingerprint untouched.
3. A lightweight aiohttp Bot API client could remove much of aiogram's generated model/dispatcher baseline in a later dedicated task. It must reproduce error mapping, proxy semantics, TLS, streaming bounds, method payloads, callback markup, cancellation, local acquisition uncertainty, request protection, and tests. Do not perform this broad replacement during an idle-loop optimization.
4. Do not add periodic forced GC, malloc trimming, blanket cache deletion, low memory limits, or native restarts as unmeasured fixes. They can cause pauses, OOM or interrupt ownership/recovery. Native arena caps or TDLib cache/thread settings require Linux measurements and supported options before adoption.
5. For later validation, sample app and API separately after warmup; compare fixed-duration CPU deltas, RSS/PSS, process/thread counts and cgroup memory.stat `anon`, `file`, `inactive_file`. Docker CLI/GUI may account for reclaimable filesystem page cache differently. Distinguish reclaimable cache from growing anonymous memory; do not infer a leak from one memory figure.

## Related specs

- `.trellis/spec/backend/runtime-contracts.md`: single-owner runtime, acquisition uncertainty, durable ordering, managed API transitions.
- `.trellis/spec/backend/local-progress-contracts.md`: bounded ephemeral observations, no change to ownership or durable task progress.
- `.trellis/spec/backend/build-distribution-contracts.md`: checked-in native bundle and strict source identity; modifying native inputs requires rebuilding provenance.

## External references

No network research performed. Measurements use the repository's existing Python 3.12 virtual environment and installed aiogram package. Windows `GetProcessMemoryInfo` working-set/private-commit counters are process-local only; no cross-platform equality is implied.

## Caveats / Not Found

- No Docker/live Telegram or real application data were accessed.
- Native Linux RSS, allocator arena count and TDLib worker/cache defaults could not be verified from a running process; no native tuning claim is made.
- No user-supplied numeric comparison before/after optimization yet exists.
- Only this research report was written; code, native bundle, specs, configuration and prior remote-image cleanup remain owned by other agents.
