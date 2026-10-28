# Application idle CPU optimization verification (2026-10-28)

## Scope and method

CPU only. Preserve earlier uncommitted source-deployment and scheduler changes.
Use the existing `idle_profile_harness.py` and identical isolated Docker flags:
network none, read-only filesystem, UID/GID 1000:1000, capabilities dropped,
64 MiB temporary filesystem, two-second warmup and 30-second measurement.
No production data, browser, HTTP server, real Telegram credentials or native
Telegram connection is included. Fake-connected mode exercises all actual app
loops with synthetic long polling and supervisor acknowledgements.

Fresh baseline image: `b1b30a37690397aadb25ad6dd732e286cb2de553bd7198d038e6c16b4eb5043d`.
Only unrelated `pgvector-db` was running before verification; leave it untouched.

## Matched samples

| Build / case | Wall seconds | User CPU seconds | System CPU seconds | Total CPU seconds | One-core CPU | SELECTs |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Baseline, fake connected local | 30.001193 | 0.507963 | 0.111296 | 0.619259 | 2.064% | 484 |
| Baseline, blank settings | 30.001339 | 0.426795 | 0.111590 | 0.538385 | 1.795% | 443 |
| Updated, fake connected local | 30.000579 | 0.483000 | 0.083761 | 0.566761 | 1.889% | 486 |
| Updated, blank settings | 30.001580 | 0.321007 | 0.134628 | 0.455635 | 1.519% | 450 |

Samples are sequential and process-wide Linux `getrusage` counters include all
worker threads. SELECT instrumentation adds overhead in both builds. These
figures cannot be equated with the user's NAS or previous Windows-bind-backed
running-container CPU. Real authenticated Telegram acceptance remains separate.

## Updated results

Full production Dockerfile build passed, including final non-owner permission
validation. Final image and actual running app ID:
`febf248824ff04e4d9e6d69fb00c469890932bcebf0689eb9cb379dd1e4909b1`.
No native rebuild occurred; API image stayed `a3e6e5026c5d`.

In these single matched samples, fake-connected full-runtime CPU decreased about
8.5%, blank-settings CPU about 15.4%. Settings worker CPU decreased from 0.104288
to 0.054265 seconds and local-state worker CPU from 0.113057 to 0.059550 seconds
in the connected samples; unchanged probe costs varied upward. Fresh row queries
remain intentionally uncached, so total SELECT count does not fall. Different
tick counts at the sample boundaries explain small SELECT count differences.
Do not claim that a roughly halved hot-method cost halves whole-app CPU.

An attempted second baseline run after rebuilding could no longer resolve the
old untagged image ID locally; it failed before starting a container. No second
paired sample is claimed. Future measurements should tag the baseline before
replacing its original tag and use `--pull never` for all baseline runs.

Started the existing isolated `tgd-idle-cpu` deployment using its dedicated data
and port 18083, with `up -d --no-build --pull never`; init exited 0 and live/ready
both returned 200. No Bot credentials were added and no real Telegram request was
made. A separate 30-second cgroup sample after health checks measured app
0.525950 CPU seconds / 30.287 wall seconds = 1.7366% of one core, and waiting API
supervisor 0.025598 / 30.473 = 0.0840%. The earlier 2.6721% app observation was
from another day: comparison is contextual, not a matched same-session result.
Raw cgroup output remains ignored under `.runtime/idle-cpu-docker/`.

Running source SHA-256 values match the working tree:

| Service source | SHA-256 |
| --- | --- |
| settings.py | `7a5164650ab89c6f2acfa0421de350a63437d0e233927d09d7f6ec26b4c72085` |
| local_api.py | `de7c5e015f95b1408308138ff25a7bbae6749bbd8b2b7db5662cc33f7095a459` |
| tasks.py | `e2e1520fa7d6d11ce3f5538dbc5e551f6f5892b7367370c87ebc78c1bb6796ce` |

## Functional verification and limits

- Implementer: 149 affected regressions passed, full backend Ruff passed, mypy
  passed for 36 app files. Scope includes settings backup/restore, cloud/local
  transitions, request safety, runtime recovery, idle scheduling and task deletion.
- Independent reviewer: no production defect found; added rollback/current-state
  and task filter/page association isolation tests. Final efficiency file 9 passed,
  Ruff and mypy36 passed. No production edits after image build.
- Task page: 20-row authenticated query count 64 -> 7, empty page stays 4;
  DTO parity and filter/search/order/pagination/deleted/off-page isolation pass.
- Configuration cache observes fresh persisted values on every read, includes
  effective environment in the key, returns isolated mutable copies and covers
  invalid overrides/concurrent saves/restore. Operational state is not cached.
- Scheduler/UI cadence, native code, memory policy and schema are unchanged.
  Topbar storage/log inspection and wider background-probe consolidation remain
  future work requiring their own boundary design. No live NAS or authenticated
  download CPU guarantee is made.
