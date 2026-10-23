# Build optimization implementation plan

User authorized implementation on 2026-10-23, including a separate manual image
publication workflow. This expands the earlier docs-only automation scope only
for manually triggered image production; no live publication/deployment is part
of this increment.

## Shape

- Root Compose becomes image-only with configurable GHCR app/API references.
  API and initializer share the same reference. Defaults mirror numeric product
  version; registry artifacts are unavailable until first successful publication.
- `compose.build.yaml` provides explicit local source builds and local tags.
  Keep production topology, mounts, private control volume and restrictions.
- `workflow_dispatch` on trusted main builds linux/amd64 production images.
  Immutable revision tags, guarded intentional numeric tags and digest-pinned
  deployment artifacts avoid mutable-release ambiguity. Workflow is independent
  of Pages and never deploys to NAS. Unchanged native inputs reuse build cache.
- BuildKit pnpm/uv/ccache caches use explicit paths and stable target identity.
  Native late-patch incremental boundaries remain; compile parallelism is
  configurable with default 2 and validated positive bounded integer input.
- Document image deployment, honest first-publication prerequisite, source
  fallback, configuration-only recreation, app-only updates and offline loading.

## Ownership and validation

Distribution worker owns Compose/env/manual workflow/version tooling and focused
tests; cache worker owns Dockerfiles/build checks; docs worker owns public docs
and unreleased changelog. Main owns active specs/task/journal and final review.

Verify default/source/custom-reference Compose quietly, version checks, focused
tooling tests, Docker build syntax and cache-install behavior. Reuse pinned native
build evidence for runtime-only production checks; distinguish unbuilt native
changes and unexecuted hosted workflows from actual passing tests. Independent
review checks workflow trust/permissions, digest pair and repeat-publication
behavior, cache safety, source entrypoints, docs synchronization and preservation
of existing UID0 changes. No user-data mounts, actual Telegram calls, cache prune,
GitHub publication or live container changes.
