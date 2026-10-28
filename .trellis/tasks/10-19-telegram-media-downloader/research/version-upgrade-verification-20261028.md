# Numeric patch release and upgrade guide verification

Recorded: 2026-10-28. Scope: product metadata and user-facing upgrade instructions.

- Canonical version is `0.1.1`; `python scripts/version.py --write` synchronized
  Python manifest/lock, frontend manifest, Compose local image tags, image labels
  and native verification Dockerfile defaults. The tool now also owns the five
  versioned local image references in the deployment guide; its drift fixture
  includes that guide and preserves third-party dependency versions.
- Review follow-up also synchronized the three active image-tag examples in
  `docs/development.md` and `docs/docker-acceptance.md`, and included both files
  in version-tool ownership and drift fixtures (12 owned mirror files total).
  Historical rollback examples remain `0.1.0`. Version check, all 9 version
  tests and targeted Ruff passed again after this change; final documentation
  rebuild is owned by the main session alongside its changelog edits.
- `python scripts/version.py --check`: passed (`0.1.1`).
- `backend/.venv/Scripts/python.exe -m pytest -q backend/tests/test_version.py`:
  9 passed. Existing Starlette/httpx deprecation warning remains unrelated.
  Direct project interpreter used after `uv` could not open its external cache
  under the sandbox. No dependency change was required.
- Targeted Ruff on `scripts/version.py` and `backend/tests/test_version.py`:
  passed.
- Independent docs package `pnpm docs:build`: passed, checking 23 Markdown files,
  101 relative links, and 18 generated public pages/anchors/output exclusions.
- Additional read-only check extracted documented `.env` and Compose blocks:
  environment key/value set equals `.env.example`; parsed Compose JSON equals
  repository Compose JSON using the example environment and project directory.
  No Docker daemon/container operation was used.
- All 26 published-documentation URLs in root README resolve to generated pages
  and decoded anchors, including `operations.html#upgrade`.

The guide records stopped-state full backup, actual running image preservation,
Git/ZIP source updates, original environment/mount/project retention, quiet
configuration validation, cached source rebuild, and version/data/connection
checks. It distinguishes encrypted settings export from full data/key/identity
backup and links the one-time marker-free storage enrollment procedure.
Rollback uses matching old source, image pair and stopped backup rather than
claiming arbitrary database downgrade support.

No current container update, live Telegram check, real-host upgrade/rollback
exercise, commit or push was performed by this subtask. Documentation validation
does not establish those runtime acceptance results.
