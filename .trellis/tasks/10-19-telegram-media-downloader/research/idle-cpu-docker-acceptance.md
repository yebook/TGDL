# Docker idle CPU acceptance

Recorded: 2026-10-27. User explicitly started Docker and requested updating local containers for verification. This supplements the offline measurement evidence in [idle-cpu-measurements.md](idle-cpu-measurements.md).

## Isolation and source provenance

- No TGDL containers existed when acceptance began; the unrelated `pgvector-db` container was left untouched.
- A new `tgd-idle-cpu` Compose project uses only `.runtime/idle-cpu-docker/{data,downloads,bot-api}` and its own `tgd-idle-cpu_api-control` named volume. Its WebUI is available at `http://localhost:18083`.
- No existing `.runtime/local-docker` settings, production keys, Bot Token, Telegram API credentials, user media, account sessions or proxy credentials were loaded. Configuration remains blank. No real Telegram request or native Bot API child was started.
- Old images were preserved as `tgd-app-idle-baseline:20261027` and `tgd-bot-api-idle-baseline:20261027`. These are local tags only.
- Baseline application image: `f525909ed49ff8533c6c08c70b2dd0e9b399e36745326adf59f4428e27a0e8ea`.
- Baseline API image: `c4206995d87d34a1300291a27c1937fba27849180fa4401e762ef06a4e4f4d54`.
- Final application image: `b1b30a37690397aadb25ad6dd732e286cb2de553bd7198d038e6c16b4eb5043d`.
- Final API/init image: `a3e6e5026c5d6b93912bf00534eadd2f93a445e790fb9d4e46b58f9c7196e62b`.

Both source builds passed their final non-owner permission checks. Native extraction and identity-verification layers were cached; there was no C++/TDLib compilation. The app-only final rebuild included the reviewer-added empty-notification optimization.

After building, Compose initially retained running containers with old image IDs. Acceptance therefore explicitly used `up -d --no-build --pull never --force-recreate`, then inspected the actual running image IDs before any updated measurement. The final images above are the IDs actually used by the containers, rather than only the current local tag targets.

Final packaged code was checked byte-for-byte against the working tree:

| File | SHA-256 |
| --- | --- |
| `backend/app/workers/runtime.py` | `3be3388c74300fe54ca40b45c176e79d8d8ca427e45cb7a9a54b4d1bea91eae9` |
| `backend/app/services/tasks.py` | `7ffb684832750edff18c11752a889085e64da050eb6fc524a922253cda576e6f` |
| `deploy/bot-api/supervisor.py` | `604c9ac92d8e4fec965423bd9ea5c9b4f594d254e75cab12285f7aa086c1c40d` |

Unchanged native binary SHA-256: `b24c49244e72673e8b0a37956eb842d0e1675b0522e6fdba71c8ee7cb5c635d2`. Native input identity: `8fd7c58e79d1e19e3d36ad93df5d3251683e9667d25dc2ebae94df3a16202d55`.

## Running-container CPU sample

Each sample reads the individual container's cgroup v2 `cpu.stat usage_usec`, waits 30 seconds, then reads it again. Percentage is CPU seconds divided by observed wall seconds, multiplied by 100 (one core = 100%). Docker exec/counter-reading overhead, host scheduling and short sample noise remain. Both samples use the same fresh unconfigured state, no browser polling and the same mounts. Health checks ran before the interval.

| Container / version | CPU seconds | Approx. wall seconds | CPU (% of one core) |
| --- | ---: | ---: | ---: |
| App baseline | 0.889664 | 30.363 | 2.9301% |
| App updated | 0.808743 | 30.266 | 2.6721% |
| API supervisor baseline | 0.056968 | 30.761 | 0.1852% |
| API supervisor updated | 0.026417 | 30.418 | 0.0868% |

The unconfigured application improvement is modest (about 9% relative in this sample). The API number measures its Python supervisor waiting for credentials, not a connected TDLib process. It cannot be substituted for the user's previously reported connected-local-API CPU value.

## Packaged Linux fake-local scheduler sample

A separate `docker run --rm --network none --read-only --user 1000:1000` container runs the **same** 30-second offline harness against the old/new packaged application respectively. This is never `docker exec` inside the idle containers above, so benchmark CPU is not added to their cgroup samples. The harness uses temporary SQLite/storage under a 64 MiB `/tmp` tmpfs, no HTTP TestClient/dev dependencies, and `create_app(config).router.lifespan_context(app)` with background consumers disabled. It configures a synthetic verified local client, same-generation marker and a synthetic supervisor acknowledgement refreshed every five seconds; only the scheduler loop runs. No TelegramClient is instantiated and the container has no network.

| Version | Elapsed seconds | CPU seconds | Ticks | SELECT | BEGIN IMMEDIATE | Generation writes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Baseline | 30.000911 | 0.846871 | 59 | 1121 | 118 | 59 |
| Updated | 30.001169 | 0.486873 | 59 | 354 | 0 | 0 |

This controlled scheduling sample used about 42.5% fewer CPU seconds, with SELECTs 19 -> 6 per tick, idle write reservations 2 -> 0, and unchanged-generation writes 1 -> 0. It demonstrates reduced scheduler work; it is not the CPU percentage of an authenticated Telegram download service or a promise about another NAS. Notification-loop savings are not included because that loop does not run in this scheduler-specific harness.

## Acceptance outcome and remaining limits

- `tgd-idle-cpu-init-volumes-1`: exited successfully, code 0.
- App and API supervisor: running as the configured UID/GID 1000:1000.
- `GET /health/live` and `/health/ready`: HTTP 200.
- Eight one-second supervisor observations showed unchanged heartbeats followed by the next heartbeat after 5.0099 seconds, state `waiting_credentials`. No tight write loop remained.
- Existing native artifact and source identity stayed unchanged; current Python source was packaged exactly, including `notification_due`.
- Updated app/API containers remain running for local WebUI verification. Unrelated containers and old deployment data are unchanged.

Actual authenticated local-API idle CPU, Telegram responses, real acquisition/progress and workload-dependent NAS behavior require a later user environment sample. Memory optimization was explicitly out of scope. Build/measurement logs and helper scripts are ignored under `.runtime/idle-cpu-docker/`; no runtime secrets or database contents are included here.

Deployment commands used (relative to the repository root):

```sh
docker compose --env-file .runtime/idle-cpu-docker/.env -p tgd-idle-cpu up -d --no-build --pull never
docker compose --env-file .runtime/idle-cpu-docker/.env -p tgd-idle-cpu up -d --build
# Final reviewer addition required app-only rebuild; no repeated native build.
docker compose --env-file .runtime/idle-cpu-docker/.env -p tgd-idle-cpu build app
docker compose --env-file .runtime/idle-cpu-docker/.env -p tgd-idle-cpu up -d --no-build --pull never --force-recreate
```
