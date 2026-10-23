# Numeric product version

## 1. Scope / Trigger

Read when changing product/package/image/docs version or version displays. User approved numeric-only versions2026-10-22.

## 2. Signatures

Canonical `backend/app/version.json`: only `version`, numeric major.minor.patch (initial0.1.0). Backend status returns `version`; OpenAPI uses the same version. Do not add a stage field or display a development-stage label. Repository synchronization/check command is `python scripts/version.py --write|--check`.

## 3. Contracts

Use canonical metadata for backend and documentation builds. Ship it inside application image; do not depend on working directory, repository root, Git or environment overrides at runtime. UI reads server metadata, not a duplicated version constant. Python/package/Compose/image labels are validated against the canonical numeric value; do not replace third-party dependency versions. Documentation builds remain independent of app dependencies. If metadata affects Pages output, the docs-only workflow must trigger on its exact source path without adding app CI/deployment.

Use0.1.1 for fixes,0.2.0 for meaningful features,1.0.0 for first formal release; no alpha/beta/dev suffix. A numeric0.1.0 does not claim a formal release. Root CHANGELOG is the authoritative update record, with unreleased and honestly dated numeric entries. Site renders the same content. Do not invent historical tags/releases or automatically publish them.

Image distribution exception authorized 2026-10-23: a separate main-only manual
workflow may publish production images; Pages stays independent and NAS is not
automatically deployed. Publication is currently paused; shared Compose fixed
local source tags are owned version mirrors, with no remote env examples. Full-commit
and digest identities identify builds without adding English stage labels to the
numeric product version. Numeric image tags require deliberate publication and
must not silently overwrite existing releases. See build-distribution contracts.

## 4. Validation & Error Matrix

| Condition | Behavior |
| --- | --- |
| Invalid canonical metadata | Fail clearly before building/running with misleading version |
| Manifest/lock/Compose drift | Check fails, write updates only known owned version entries |
| Docker without Git/repo root | Runtime metadata and OpenAPI/status still agree |
| Docs update or metadata change | Independent docs build and same-source changelog are current |

## 5. Good / Base / Bad Cases

Good: update canonical numeric version, synchronize known manifests and validate before build. Base: initial0.1.0 displays without stage labels. Bad: bulk replace every0.1.0, rely on runtime Git, or maintain two manually copied changelogs.

## 6. Tests Required

Validate metadata format, synchronization drift and targeted writes, OpenAPI/status/version parity and production-shaped loading. Build/check documentation includes shared metadata and root changelog, and link to real site route. No new database schema or real Telegram calls are needed.

## 7. Wrong vs Correct

Wrong: frontend says0.1.0 while API hardcodes another number, or Pages misses version-only changes. Correct: canonical data, known mirror checks and exact docs workflow dependency paths.
