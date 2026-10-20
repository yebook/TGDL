# TGD WebUI

Chinese, responsive Vue 3 / TypeScript / Element Plus administration interface.
Use Node 24 (`.node-version`) and pnpm 11 (`packageManager`); dependency versions and lockfile are pinned.

```sh
pnpm install --frozen-lockfile
pnpm dev
pnpm lint
pnpm typecheck
pnpm test:unit
pnpm build
```

Vite listens on `127.0.0.1:5173` and forwards `/api` and `/health` to the backend at `127.0.0.1:8000`.
The production `dist/` directory is served by the backend at the same origin.
There are no external font, image, telemetry, or CDN requests.

## API and state contracts

- `src/api/types.ts` is currently **hand maintained** against the backend REST DTOs. It is not generated from OpenAPI. The backend owns statuses and projections; views never calculate a parent task's result from its children. Update these DTOs alongside backend response changes. OpenAPI generation should replace the manual source once the complete backend response schemas are available.
- `src/api/client.ts` is the only HTTP boundary. Sessions use cookies and in-memory CSRF tokens. Mutation bodies carry secrets; secrets are never stored in browser storage or URL parameters. A 401 on an established session sends the UI back to login.
- `usePolling` aborts superseded requests and guards results by generation. Background tabs poll less often; leaving a page aborts requests and removes timers. Errors preserve the last successful result with a visible stale-data warning.
- Settings use a revision for optimistic concurrency. A 409 preserves user input and requires explicit reload. Blank secret fields retain server values; clearing is explicit. Unsaved settings guard navigation and page unload.
- Proxy status distinguishes saved/desired configuration from the version acknowledged by the network component. Older diagnostic responses cannot overwrite the saved revision. New downloads use the configured concurrency and confirmation timeout; old cards retain their original expiry.
- Download confirmation happens only in Telegram. The interface has no media upload, playback, Web confirmation, or media deletion feature.

## Verification

Vitest covers CSRF/cookie request handling, login versus session expiry, structured server errors, aborts, unknown-size progress, lossless user-ID parsing, delayed stale responses, retained data after errors, and polling disposal.
Browser checks against the real backend are coordinated separately from these unit tests; real Bot downloads and NAS/proxy compatibility require the deployment acceptance checks.

### Repeatable local browser acceptance

From the repository root, first run `uv sync --locked --project backend`. From `frontend`, run:

```sh
pnpm exec playwright install chromium
pnpm test:e2e
```

The command builds production assets, starts the real backend on loopback port 8765, runs five browser cases, and stops its own server. Port 8765 must be free; an existing server is never reused. The test-only `backend/tools/e2e_app.py` creates a fresh temporary database and storage, discards inherited `TGD_*` environment variables, seeds clearly named synthetic tasks, disables background Telegram workers, and removes its own temporary directory on normal shutdown. Forced process-tree termination (including Windows test teardown) may leave a tgd-e2e-* directory in the operating-system temporary directory; it contains synthetic test data only. It accepts no production data path and adds no fixture API to the application. Test credentials are exclusively for this disposable loopback fixture. No Telegram connection or real proxy is used.

Coverage: login/logout, real search/filter/detail, queued cancellation, failed-file-only retry with history retained, settings persistence, two-session revision conflicts retaining edits, and desktop/mobile overflow. REST responses are not mocked. On a machine with Chrome already installed, set `PLAYWRIGHT_CHANNEL=chrome` to use it instead of downloading Chromium. Failure traces/screenshots under `test-results/` are local test artifacts and must not be committed.
