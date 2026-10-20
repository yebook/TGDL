# Local acceptance contracts

## 1. Scope / Trigger

Read before editing `scripts/acceptance.py`, its tests, `backend/tools/e2e_app.py`,
`frontend/e2e/`, Playwright configuration or the checks workflow. These tools
verify local/API behavior; they cannot satisfy real Telegram, NAS or TUN gates.

Also read the isolated Docker runner contract below before editing
`scripts/docker_acceptance.py` or its tests.

## 2. Signatures

- `python scripts/acceptance.py [--base-url ORIGIN] [--timeout SECONDS] [--login] [--json]`
- `python -m unittest discover -s scripts/tests -p test_acceptance.py -v`
- `pnpm --dir frontend test:e2e` builds assets and runs Playwright.
- Playwright launches `uv run --directory ../backend python -m tools.e2e_app`
  from `frontend/`, with `reuseExistingServer: false` on loopback port 8765.

## 3. Contracts

- CLI uses Python 3.10+ standard library. Base URL is a root HTTP(S) origin without
  credentials, query or fragment. Per-request timeout defaults to 10 seconds,
  maximum 300. Response bodies are bounded to 2 MiB; redirects are not followed.
- Optional credentials use `TGD_ACCEPTANCE_USERNAME`, `TGD_ACCEPTANCE_PASSWORD`
  or a terminal prompt. Never place passwords in command arguments. Only
  login/logout mutate state; media, settings and setup must remain unchanged.
- JSON schema_version is 1. Emit allowlisted statuses, numbers and booleans,
  fixed error codes and unverified experiment names. Never echo response bodies,
  task names, filesystem paths, URLs, credentials, cookies or CSRF tokens.
- API success is distinct from Bot configured or downloads usable. Anonymous
  mode explicitly skips authenticated endpoints. No HTTP proxy is inherited.
- E2E fixture creates fresh TemporaryDirectory storage, clears inherited TGD_*
  environment, uses run_background=False and binds only 127.0.0.1. It accepts no
  deployment data directory, connects to no Telegram service and adds no
  production fixture endpoint. Synthetic task data is not real media evidence.
- Fixed fixture credentials exist only in this disposable test process. The
  fixture is excluded from the Docker build context. Production never runs it.
- E2E tests call real production APIs, not intercepted/mocked REST responses.
  Keep E2E files inside lint/typecheck scope; reports and traces remain ignored.
- Pagination/delete browser checks seed query-scoped disposable fixture records
  and use real list/delete APIs; do not synthesize successful page or deletion
  responses. Seed extra pagination rows older than the original action fixtures,
  so those existing cancel/retry tasks remain on the first page. Account for total
  page-size changes explicitly and check affected existing selectors. Give new
  destructive browser cases their own query-scoped records; do not reuse fixtures
  whose states earlier cancel/retry cases mutate. Screenshot evidence waits for
  checked states, settled dialogs and final layout rather than transition frames.
- Polling timing/failure regressions may delay and continue a real request or
  abort one request to simulate network loss. Do not synthesize successful REST
  bodies. Assert retained nodes/layout/scroll while the real request is pending,
  after failure, and after the next successful polling cycle.

## 4. Validation & Error Matrix

| Condition | Behavior |
| --- | --- |
| Selected API checks pass | CLI exit 0; unverified gates remain listed |
| HTTP/schema/timeout check fails | CLI exit 1; fixed safe error code |
| Invalid URL/timeout or missing login input | CLI exit 2; no credential-bearing request |
| No credentials requested | Authenticated API marked skipped |
| Redirect | Reject; never forward credentials |
| Port 8765 already occupied | E2E fails; never reuse unknown server |

## 5. Good / Base / Bad Cases

- Good: two real browser sessions save competing settings revisions; the loser
  keeps its form input and the database retains the winning version.
- Base: anonymous health checks succeed while management checks are skipped.
- Bad: a test reuses production SQLite storage, or a health response is presented
  as proof of a real video download. Both violate this contract.

## 6. Tests Required

CLI tests use local stub HTTP servers to assert exit behavior, bounded/truncated
responses, cleanup, timeout, redirect refusal and absence of sensitive output.
Browser tests assert login/logout, search/filter/detail, cancelled task persistence,
retry of only failed items, retained attempt history, settings persistence/conflict
and desktop/mobile layout. Run the project lint, typecheck and existing test gates
after changes; CI downloads Chromium, local runs may use PLAYWRIGHT_CHANNEL=chrome.

## 7. Wrong vs Correct

Wrong: set reuseExistingServer=true to avoid startup errors, or point fixtures at
TGD_DATA_DIR inherited from a shell.

Correct: refuse occupied ports, create disposable roots, and verify mutations
through the same API used by the WebUI while preserving production isolation.

## Isolated production-image Docker acceptance

### 1. Scope / Trigger

Use this opt-in runner to verify production app image behavior without Telegram
credentials. Unlike the read-only service checker, it mutates disposable data.

### 2. Signatures

`python scripts/docker_acceptance.py --run [--image tgd-app:0.1.0]`.
`python -m unittest discover -s scripts/tests -p test_docker_acceptance.py -v`.

### 3. Contracts

Use Python 3.10+ stdlib, local existing image only, production entrypoint,
UID/GID 1000:1000, read-only root, dropped capabilities, no privilege escalation.
Create random owned container names, temporary roots and dynamic loopback port.
Never read deployment `.env`, credentials or user data. Bot Token remains empty;
no Bot API container or Telegram calls. Generate disposable credentials internally.
Output schema_version=1, fixed check statuses/error codes and unverified gates;
never print API/Docker response bodies, credentials or paths. Stop before copying
SQLite and keys together; preserve storage identity and marker bytes unchanged.
Restore uses the same filesystem and container paths, not a different-device
migration. Keep cleanup restricted to exact owned resources; if container removal
fails, do not remove data it may still be using.

### 4. Validation & Error Matrix

| Condition | Result |
| --- | --- |
| Missing `--run` or invalid CLI | Exit 2; no containers created |
| Missing Docker/image or failed assertion | Exit 1; safe code, no fake pass |
| Successful real checks and cleanup | Exit 0; real-media/NAS gates remain unverified |
| Container cleanup failure | Exit 1; retain potentially mounted data |

### 5. Good / Base / Bad Cases

Good: save non-Bot settings, recreate, restore stopped backup and verify login,
revision, storage identity and sentinel bytes. Base: initialized disposable app
has no configured Bot. Bad: test touches `.runtime/local-docker`, edits identity
to force restoration success, or uses a synthetic backend fixture in the image.

### 6. Tests Required

Unit checks cover explicit opt-in, missing Docker, error redaction, exact resource
ownership and preservation after cleanup failure. Actual Docker run must verify
UI assets, initialization/password boundaries, CSRF settings save, session
invalidation, recreation persistence, stopped backup/restore and final cleanup.
Run Ruff on scripts. Repeat actual run when lifecycle/assertions change.

### 7. Wrong vs Correct

Wrong: batch-delete names by prefix or treat a copied sentinel as a downloaded
video. Correct: remove only this run's exact random names and explicitly report
real Telegram, >20MB, fnOS, proxy routing and cross-device restore as unverified.
