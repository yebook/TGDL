# API switch and connection status verification (2026-10-28)

## Problem and implementation

User confirmed that actual Bot messaging works while the UI still appears
abnormal. Source audit identified a permanent identity-loss path: transient
polling AppError cleared the display username, while same-client reuse and later
success restored only state/message. There was also a premature-online path on
client reuse, independent panel/topbar priorities and separately composed status
observations. See `api-switch-status-audit-20261028.md`.

- Keep verified identity bound to the exact published client/signature/generation.
  Reuse permits retries but does not itself clear genuine transport errors.
  Actual successful polling restores readiness and current verified username.
- Ignore late replaced-client/config/operation/generation results. Keep current
  unpublished candidate failures visible without retaining obsolete errors.
- Capture current settings, durable API state and supervisor observation once
  under the short publication lock, and reuse them for API/Bot/proxy projections.
  Disk/log inspections remain outside; no status-driven upstream requests.
- Separate durable operation errors from Bot transport evidence. Target identity
  verification followed by a still-connecting consumer is a normal short gap.
- Shared `connectionSummary` drives topbar and connection panel, with target-aware
  start/stop phases. Routine phases remain progress rather than fault-list alarms.
  Genuine protection, failed/blocked/uncertain operations and stale observations
  remain visible; an unused local process does not alarm in cloud mode.

No native/schema/polling cadence/protection policy or real Telegram change.
Previous uncommitted CPU/source-only-deployment edits are preserved.

## Checks

- Implementer: 99 affected backend tests passed across connection status,
  local API, bounded recovery, request protection, cloud reconnect and idle reads.
  Final supplemental connection-status file initially 6 passed, including actual
  fake poll AppError->same-client recovery without another getMe, managed retries,
  token/late-client isolation, candidate error and local command/generation/auth.
- Backend Ruff and mypy36 passed. Frontend 90 unit cases, lint/typecheck/build
  passed; reviewer then verified the normal-progress issue-list correction with
  focused helper tests. Reviewer owns the final HTTP/browser check report.
- Independent review found no remaining production defect after correcting the
  normal-phase fault list. Authenticated HTTP status/identity/API/proxy regression
  added: final connection-status file7 passed, Ruff and mypy36 passed. Frontend
  shared-helper20 passed, ESLint/typecheck/build passed.
- Desktop1440x900 and mobile390x600 browser display assertions both passed using
  actual isolated login/settings and an explicitly intercepted status matrix;
  this is presentation coverage, not a real lifecycle/Telegram acceptance. The
  first Windows uv-managed webServer teardown timed out after assertions and
  caused runner exit1; owned test processes were cleaned. A narrow manual-server
  rerun then passed cleanly: backend `.venv/Scripts/python.exe -m tools.e2e_app`
  launched separately, temporary Playwright config with webServer undefined,
  `PLAYWRIGHT_CHANNEL=msedge node @playwright/test/cli.js test --config playwright.review.config.ts -g 'connection presentation stays coherent'`
  completed 2 passed in4.9seconds, exit0. Owned server stopped and temporary
  config removed. No runner/product changes were needed.
- Documentation final build: 23 Markdown files, 99 links, 18 generated pages;
  public anchors and output exclusions passed.
- `git diff --check` passed; platform line-ending/global-ignore warnings are
  unrelated to the implementation.

## Local Docker verification

Baseline app tag preserved as `tgd-app-source:before-api-status-20261028`.
Full production app Dockerfile build passed its final UID1000 code/asset/tool
permission guard. No API/native rebuild occurred.

Final app image:
`002118c5adc2899e58ff9e14ef8dfc19a1b785d54989ee77c80ea10d7eef5c08`.
Packaged source hashes match the working tree:

| File | SHA-256 |
| --- | --- |
| app/workers/runtime.py | `3750db2c308280e7075111b6f9629fe65cc9ab28ef0ca8c93ebfd1ef4647ff1c` |
| app/services/local_api.py | `875a3d4ca116333c62f502385a2b03b07af8b7272dfbac349814e9993255732c` |
| app/services/settings.py | `0e4bb4f40f86941ea7882047c19f6737ee3585d903058631ac6d71bcb5db1ef8` |
| app/main.py | `e11c03b4e41533e8ef4664bfd285b149f4385ac383f2f6a83688eabce551e383` |

Updated only the existing isolated `tgd-idle-cpu` app using its dedicated env/data:

```sh
docker compose --env-file .runtime/idle-cpu-docker/.env -p tgd-idle-cpu up -d --no-build --no-deps --pull never app
```

App runs at localhost18083 with the exact final image. Live and ready return200.
API container, user settings/test data and unrelated services remain preserved.
No real Bot token, credentials, Telegram probe, logout or API switch was used.
This verifies packaged startup, not authenticated NAS/Telegram switching.
