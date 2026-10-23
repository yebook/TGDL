# Build and image distribution

## 1. Scope / Trigger

Read for production Dockerfiles, Compose image/source deployment, package caches,
image release automation and distribution docs. User authorized this scope on
2026-10-23; Pages remains independent. No automatic NAS deployment is authorized.

## 2. Signatures

- `compose.yaml`: source-only deployment, fixed `tgd-app-source:<version>` and
  `tgd-bot-api-source:<version>` tags, `pull_policy: build` on app/API.
  Ordinary `up -d` builds current source; `up -d --build` is also supported.
  Intentional config-only recreation uses `up -d --no-build`. No override file,
  remote image variables or pull-policy variables. API and initializer share
  one image, built only by bot-api; init has no build and pull policy never.
  Two Bake targets exporting the same tag raced on the containerd image store
  during actual acceptance. Never restore duplicate exports; normal whole-stack
  up must build API before initializing an empty control volume.
  Compose fixes `TGD_NATIVE_IMAGE: bundled-native`.
  Do not assume Compose `--dry-run up` is side-effect free: actual Buildx builds
  occurred under the tested Compose version. Use isolated tags and paths even
  for a dry-run, and restore any test-only tag changes afterward.
  Do not assume Compose `--dry-run up` is side-effect free: actual Buildx builds
  occurred under the tested Compose version. Use isolated tags and paths even
  for a dry-run, and restore any test-only tag changes afterward.
- `TGD_BUILD_JOBS`: source-only compilation setting, integer 1..64, default 2;
  unrelated to application download concurrency.
- Independent `native` target: compiled Bot API/TDLib, runtime libraries and
  licenses, without supervisor/init scripts or numeric product label.
  `TGD_NATIVE_IMAGE` selects a trusted precompiled base by digest for runtime
  packaging; default `bundled-native` uses the checked-in linux/amd64 archive.
  Explicit `native` remains the complete C++ source-build target.
- `deploy/bot-api/prebuilt/linux-amd64.tar.gz` and `manifest.json`: ordinary Git
  files containing the linked Bot API/TDLib binary, licenses and provenance.
  `python scripts/bundle_native.py --image <fresh-native-image>` regenerates both.
- Image workflow: temporarily disabled; publisher and bundle entrypoints fail
  before registry access. Pages remains independent and enabled. The following
  registry contracts describe dormant internal tooling, not available deployment.
- Docker Hub secrets: `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN` (read/write),
  with public `tgdl-app` and `tgdl-bot-api` repositories only.
  GHCR remains authenticated build/cache storage. Mirror the same manifests,
  never perform duplicate native compilation per registry.

## 3. Contracts

First-release numeric metadata remains canonical. Version tooling updates owned
fixed local source tags; remote image examples/overrides are absent from env.
The production app/API pair must be reproducible; initializer always uses the
same API reference. No generic upstream binary may replace the project's pinned
patched API. Preserve native patch source verification, licenses, proxy/progress/
cleanup behavior and supervisor protocol. Runtime scripts remain isolated from
expensive native compile layers. Caches never include runtime data or credentials.

Publish only the application and final API images. Never build, inspect, alias,
mirror or require anonymous access to a separate native image repository.
API builds directly consume bundled-native and reuse final API content artifacts.
Native source identity excludes management scripts, product-version-only runtime
labels and application code; includes source pins, patches/headers, toolchain and
native dependency setup, platform and all native-target build inputs. API keys
additionally bind actual checked-in bundle bytes and runtime scripts. Record
native source/artifact identities in deployment metadata instead of a third
image reference. Avoid native compilation on ordinary app/supervisor updates.

Source deployment must work without a published native image or a surviving
compiler cache: use the checked-in native bundle by default. Missing, corrupt,
unsupported or source-mismatched bundles fail clearly; never silently compile
or relabel a stale binary. Pin source identity separately from concrete artifact
identity. Publisher cache keys also bind actual bundled bytes and validate the
local bundle even when a registry artifact exists, so changed/corrupt prebuilts
cannot be hidden by a source-only cache hit.

Bundle deterministic archive bytes with explicit safe modes and the upstream
licenses. Validate archive and member checksums, exact allowed file paths,
regular-file types, source pins, architecture and native input identity before
extracting. Reject path traversal, links and preexisting destination symlink
paths. Export fresh source-built artifacts only after provenance checks; initial
reuse requires verified original source tree, patch/header inputs and actual
compiled/exported binary agreement. Synthetic test manifests are never release
provenance. Changes to native inputs require regenerating and committing bundle
and manifest together; product/runtime-only edits do not require recompilation.

Image code permissions must not depend on host checkout modes or NAS ACL export.
Normalize packaged application code, dependency/static trees and startup scripts
for arbitrary configured runtime UID/GID: readable files, traversable directories,
and preserved required executable bits. Verify with restrictive Linux build
contexts, because Windows contexts can mask this failure. Apply permissions only
to image-owned code; never broaden mounted data, keys or control-file permissions.
Runtime packaging permission fixes must not invalidate native input identity.
Build-time guards after normalization must actually read entrypoints as a
non-owner UID/GID and verify required executables, rather than relying on root
access or source text assertions. Fail the build before an unreadable runtime
artifact is accepted. Test the entire source-only Compose lifecycle using fresh
isolated paths, no credentials and no inherited runtime .env; verify initializer
exit 0, app readiness, idle supervisor, actual image content and config-only
recreation behavior. Do not silently reuse an earlier product-image tag.

Final runtime stages must explicitly normalize permissions after the last COPY;
COPY --chmod alone is not the acceptance boundary. On 2026-10-23 the user's
source-built images contained root-owned 0700 app/API Python entrypoints with
0755 parents, which cannot be read by UID 1000. Exact ZIP revision/builder cause
remains unknown; do not infer a specific builder bug from this observation.
The user's subsequent UID/GID 0 attempt reported the removed initializer error
`TGD_UID and TGD_GID must be nonroot numeric IDs`, proving the running API image
contains an outdated initializer. Current source permits each ID independently
from 0 to 2147483647. Update actual source/image and recreate containers; an
unchanged numeric tag or recent image timestamp is not evidence of current code.
Exercise explicit runtime chmod with restrictive 0700 copied files, including a
fixture without COPY --chmod, and verify actual reads under non-owner IDs.
Retain a negative fixture that rejects unreadable scripts. Never fix this by
making mounted user data public or requiring root runtime.

Permission acceptance must reproduce the hosted execution mode: bind the verifier
at its absolute container script path and invoke Python with that filename, with
no inherited PYTHONPATH. Stdin execution is insufficient: Python places the script
directory rather than the application working directory on sys.path. The offline
verifier must explicitly resolve the real application root before importing
app.main, while production startup continues to use uvicorn's application path.

Freshness is enforced, not inferred from tag names: native artifacts carry
versioned provenance with exact Bot API/TDLib source pins and native input
fingerprint. One shared standard-library helper under `deploy/bot-api/` computes
the identity for publication and Docker builds. Native packaging records it;
runtime packaging recomputes the expected identity and refuses missing or
mismatched metadata before accepting an external base. Source builds use the
same check. Pin and verify the actual upstream TDLib submodule revision before
compilation. No manual version bump or floating tag substitutes for these gates.

BuildKit package cache paths are explicit; pnpm package import and uv link modes
must not create references to ephemeral cache mounts in final images. Ccache
identity distinguishes target architecture and compiler/source context. Retain
late-patch incremental reuse unless an actual measured improvement justifies
restructuring. Mutable cache mounts are not assumed to export via layer cache.

Manual image publication uses least-privilege scoped GHCR permissions, pinned
actions, no workflow on untrusted pull requests, no shell interpolation of
untrusted input, and independent app/API caches. Native reuse keys cover every
actual API build input. Existing immutable tags cannot silently change meaning.
No actual published image or speedup may be claimed from local configuration
validation. Arm64 requires real architecture-specific build/smoke evidence.

Check immutable alias conflicts in both registries before expensive builds.
Copy complete manifest indexes and attestations without changing digests;
interrupted copies must remain safely retryable. Actual production-image
permission checks and offline smoke precede release aliases. Anonymous Docker
Hub manifest pulls for app/API are required before publication succeeds.
Deployment bundle creation is currently blocked at its public entrypoint.
Dormant registry helpers retain manifest/digest safety for future restoration,
but current Compose cannot deploy remote product images. Source startup must
not query remote application/API repositories; base image/package network
access remains necessary.
Never print Docker Hub Tokens or require deployment-host credentials for public
Docker Hub pulls. Neither image publication nor Pages updates deployed containers.

## 4. Validation & Error Matrix

| Condition | Behavior |
| --- | --- |
| Configuration-only change | `up -d --no-build`, recreate containers without compiling |
| Source startup with default env | Ordinary `up -d` builds fixed local source tags; no remote production-image lookup |
| Remote image/pull-policy variables set | Ignored by fixed Compose; cannot select remote product images |
| Publication or deployment bundle request | Fail before registry access while source-only mode is active |
| App-only source edit | Build app only; keep API artifact unchanged |
| Management script edit | Repackage API using the same precompiled native base |
| Bundled native artifact valid, builder cache empty | Extract verified bundle; never compile TDLib as runtime dependency |
| Native source/patch/toolchain/dependency change | Build a new native input artifact |
| Explicit old/unversioned native base | Reject mismatched or missing native provenance; do not package stale binaries |
| Invalid compile concurrency | Fail before compilation |
| First GHCR publication not complete | Explain prerequisite and working source fallback |
| Non-main workflow invocation | Refuse publication |
| Existing numeric release tag | Refuse replacement |
| Unchanged API inputs | Reuse verified final API content artifact |
| Runtime secrets or user data | Never copied into build context/artifacts/cache |

## 5. Good / Base / Bad Cases

Good: fixed source tags build current code, initialization consumes the newly built API image.
Base: default 2 compiler jobs and warm dependency/native caches.
Bad: download-directory edit triggers TDLib compilation; publishing a new app
silently replaces a numeric API tag; docs present unavailable images as deployed.

## 6. Tests Required

Quiet Compose checks for fixed local source references, ignored remote variables and zero/mixed
runtime identity; version tooling preservation and drift tests; build argument
validation; actual isolated package-cache install and runtime source checks where
possible. Review workflow permissions, main gate, tag collision/retry behavior,
content input hash and exact deployment image pair. Document unexecuted hosted
build/publication and native cold compile separately from passing local checks.
Parse actual Compose configuration and source build plan; assert distinct local
source tags, exactly two image builds, API/init image equality, build policy for
app/API and never policy for init, and bundled-native. Assert publication and
bundle entrypoints reject before registry calls; exercise real full source up
using fresh isolated paths and test port without credentials.
Docs build validates current code blocks and links. No live Telegram or current
deployment/data mutations are needed.

## 7. Wrong vs Correct

Wrong: `--no-cache` or Docker prune as routine upgrade steps; a floating latest
tag as proof of a compatible API; enabling privileged containers to fix builds.
Correct: existing-image recreation for config, explicit source builds for code,
fixed verified image pairs and unchanged runtime permission/storage boundaries.

Wrong: a source edit leaves a manually configured native digest silently usable.
Correct: recompute native inputs during runtime packaging and fail on mismatch;
runtime-script/product-version-only changes leave native identity unchanged.
