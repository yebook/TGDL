# UX and numeric-version delivery — 2026-10-22

## Proposed work commit

`feat: simplify workspace and add numeric versioning`

One coherent increment: authenticated workspace and four-section settings, session-only debug presentation, user-oriented documentation, numeric version source and changelog, related specifications and acceptance evidence. No unrelated dirty files identified. No runtime data, credentials, screenshots, temporary scripts or generated build output belongs in the commit.

The exact tracked/new file set is listed below after final checks. Commit authorization remains pending; the user subsequently authorized local deployment separately. No release tags, GitHub Release or pushing were authorized for this increment.

## Subsequent user instruction and deployment

User requested numeric version only, without stage labels, and updating the local application container. Stage metadata/API/UI/docs were removed; focused backend/frontend/browser/docs checks passed. Application image `7f048ed29ba4` was deployed with stopped backup at `.runtime/local-docker/backups/before-ux-version-20261022T042158Z/`. Existing API container was preserved. Current verification and independent-review status are recorded in implement.md; earlier image checks below describe pre-deployment validation, not the final running image.

## Verification

- Frontend: lint/type/build and 65 unit tests passed.
- Backend: Ruff/Mypy, nine version tests and targeted management/diagnostic/log suites passed (71 passed, eight platform skips).
- Documentation: build, 23 Markdown sources, 107 relative links, 18 generated-page checks, desktop/mobile navigation and search passed.
- Independent review fixed pending LocalApiPanel confirmations/requests surviving navigation; two browser regressions passed.
- Production app test image `ac1d00c56ccd3c4f7c5ad543d3e43d57bbf36b223c67b21a7626c3d11195ae4e`: eight isolated Docker checks passed. Offline container verified packaged version/stage/OpenAPI parity. Native image OCI version verified.
- Canonical version sync passed. Actual application containers and published documentation were not updated.
- Final browser supplement is recorded in implement.md after completion. Real Telegram large-file/proxy/fnOS acceptance remains a parent-task release gate.

## Exact proposed file set

- .github/workflows/docs-pages.yml
- .trellis/spec/backend/index.md
- .trellis/spec/frontend/documentation-contracts.md
- .trellis/spec/frontend/index.md
- .trellis/spec/frontend/ui-contracts.md
- .trellis/tasks/10-19-telegram-media-downloader/check.jsonl
- .trellis/tasks/10-19-telegram-media-downloader/design.md
- .trellis/tasks/10-19-telegram-media-downloader/implement.jsonl
- .trellis/tasks/10-19-telegram-media-downloader/implement.md
- .trellis/tasks/10-19-telegram-media-downloader/prd.md
- .trellis/tasks/10-19-telegram-media-downloader/task.json
- CHANGELOG.md
- Dockerfile
- README.md
- backend/app/main.py
- backend/app/schemas/responses.py
- deploy/bot-api/Dockerfile
- deploy/bot-api/Dockerfile.verify-progress
- docs/.vitepress/config.mts
- docs/README.md
- docs/acceptance.md
- docs/configuration-backup.md
- docs/deployment.md
- docs/development.md
- docs/faq.md
- docs/index.md
- docs/operations.md
- docs/pages.md
- docs/project.md
- docs/quick-start.md
- docs/telegram-api.md
- docs/templates.md
- docs/usage.md
- frontend/e2e/acceptance.spec.ts
- frontend/e2e/diagnostic-logs.spec.ts
- frontend/e2e/managed-api.spec.ts
- frontend/e2e/proxy-network-ui.spec.ts
- frontend/e2e/request-safety.spec.ts
- frontend/src/App.vue
- frontend/src/api/types.ts
- frontend/src/components/BotSafetyPanel.vue
- frontend/src/components/DiagnosticLogPanel.vue
- frontend/src/components/LocalApiPanel.vue
- frontend/src/components/SettingsBackup.vue
- frontend/src/components/SettingsSource.vue
- frontend/src/style.css
- frontend/src/views/AuthView.vue
- frontend/src/views/SettingsView.vue
- frontend/src/views/SystemView.vue
- scripts/docker_acceptance.py
- .trellis/spec/backend/version-contracts.md
- .trellis/spec/frontend/workspace-experience-contracts.md
- .trellis/tasks/10-19-telegram-media-downloader/research/ux-delivery.md
- backend/app/version.json
- backend/app/version.py
- backend/tests/test_version.py
- docs/changelog.md
- docs/settings.md
- frontend/src/api/systemSummary.ts
- frontend/src/components/TopStatus.vue
- frontend/src/components/WorkspaceProvider.vue
- frontend/src/composables/useSystemStatus.ts
- frontend/tests/systemSummary.test.ts
- scripts/version.py

Unrecognized dirty paths: none.
