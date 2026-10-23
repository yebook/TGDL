# Research: Docker build optimization proposal

- Query: Evaluate slow first deployment and update builds; propose a practical optimization plan without changing code or publishing images.
- Scope: internal source audit with external reference links, not externally verified in this session.
- Date: 2026-10-23

## Findings

### Files found and current behavior

- `Dockerfile:1-14`: Separate frontend/Python dependency layers already precede source copies. Frontend changes rebuild `pnpm build`; backend-only source changes reuse frontend and dependency layers. Dependencies are locked. No explicit BuildKit package cache mounts exist.
- `deploy/bot-api/Dockerfile:1-14`: Pins upstream Bot API commit and its TDLib submodule, fetches sources once, compiles proxy patch with Release CMake at fixed parallelism 2, then incrementally compiles progress and cleanup patches. This is one full build plus two incremental builds, not three full builds.
- `deploy/bot-api/Dockerfile:16-28`: Production stage copies compiled installation and small supervisor/init scripts. Supervisor/init script changes should not invalidate compilation layers.
- `compose.yaml:1`: App and API currently have local `build` definitions and local `0.1.0` image names. API and init-volumes share the same API build definition and image; this does not inherently mean two complete native compilations. App depends on API management even in cloud mode, preserving WebUI switching without container topology changes.
- `.dockerignore:1`: Excludes git, developer tools, local dependencies, docs, private runtime and default data/download/cache paths. Build context exclusion is already substantial; it is unlikely to be the primary native compilation bottleneck.
- `docs/deployment.md:9,47,80`: Documents source-only distribution, initial native build/network requirements, and `up -d --build` first launch.
- `.github/workflows/docs-pages.yml:1`: Only static documentation build/publication exists. Earlier user authorization explicitly restricted automation to documentation, so this proposal does not authorize adding or enabling image workflows.
- `.trellis/spec/backend/managed-api-contracts.md`: Private supervisor, API lifecycle, progress and cache behavior must be preserved.
- `.trellis/spec/backend/version-contracts.md`: Numeric product version is canonical; do not silently invent releases or continue overwriting published immutable version tags with differing content.

### Bottleneck assessment

The strongest source-level bottleneck is first-time C++ Bot API/TDLib compilation and associated source/dependency fetching. A lower-powered NAS may be especially constrained by CPU, memory and package/source network access. No fresh build or timing measurement was performed, so no numerical speedup or duration is claimed. Recent fast local API build logs used a prebuilt, verified native build cache and cannot represent cold compilation.

Warm source builds are materially different: unchanged API source and patch layers can be reused in full. Ordinary app/UI changes should not recompile TDLib, assuming the same builder/cache and no explicit cache purge. Need logs before attributing repeated full rebuilds to a cache bug. Base image upgrades, upstream revision or early native proxy patch changes invalidate expensive downstream work by design.

### Priority 1: remove unnecessary builds from routine deployment

- Configuration/mount/port/UID/GID changes: reuse installed images, reconcile containers with `docker compose up -d --no-build`; do not instruct users to click a graphical “rebuild image” operation for configuration-only changes.
- Source changes confined to app/UI: build app only; leave API image unchanged. API supervisor scripts can rebuild the lightweight production image while preserving native build cache.
- Explain image build versus container recreation in user documentation. Do not run Docker prune as ordinary maintenance or put `--no-cache` in routine instructions.
- Existing media must be migrated safely for storage changes. Not compiling does not bypass application shutdown/download ownership or data migration safeguards.

### Priority 2: distribute production images (largest user-facing improvement)

Publish both the app and the project's patched API production images to a registry such as GHCR. Default user Compose would pull images without source `build` definitions; retain separate source-building instructions/override for contributors. Init-volumes must use the exact same API image as bot-api. Candidate addresses are proposals, not currently available artifacts.

Benefits: NAS downloads and extracts images instead of installing compilers, fetching npm/PyPI/GitHub source and compiling TDLib. A new app release can reuse an unchanged API image digest; updating UI must not trigger API rebuilds. Deployment still depends on registry reachability and bandwidth. Offer release tar image archives (`docker save/load`) if registry access is impractical; archives must be per architecture and checked by hash.

Use immutable commit-addressed build tags and OCI revision/source metadata during current development. Publish numeric release tags only when intentionally releasing that revision; reference fixed tested versions or digests in example deployment instead of an unreviewed floating latest tag. App/API image content can have separate revision identities while preserving canonical numeric product metadata. Build and record compatibility as a pair to avoid old API lacking newer private methods.

Build-image workflow would be separate from docs Pages, opt-in/manual initially, scoped to relevant files or deliberate release events later. No automatic NAS deployment. Requires explicit next-step authorization because existing workflow scope was docs only; do not implement or enable this as part of a research-only request.

Architecture: start with the actual target `linux/amd64` if confirmed, then add `linux/arm64` only after real builds and smoke tests. NAS product name alone does not prove CPU architecture. Check pinned image manifests, Python/Node package support and native API build for each platform. Native ARM runners are preferred for TDLib over assuming slow QEMU emulation is an acceptable build pipeline; do not promise working cross compilation from Docker buildx alone. Preserve production stage, pinned upstream source and patches, licenses and existing private control/security model.

Do not substitute a generic stock Bot API image: this project relies on custom proxy/progress/cache cleanup endpoints and its supervisor. A stock upstream executable would silently lose required behavior.

### Priority 3: improve source/CI build caching

- Enable BuildKit cache mounts for pnpm store, uv package cache and compiler cache. Keep lockfile-first ordering. Specify pnpm store location explicitly and ensure installation method/package import works across the cache mount; ensure uv copies rather than relying on cross-mount hardlinks. These preserve downloaded artifacts after layer invalidation, not magically avoid initial downloads/builds.
- C++: add ccache with `CMAKE_CXX_COMPILER_LAUNCHER=ccache` and stable compiler paths. Partition cache by target architecture/toolchain/upstream source as needed; retain generated sources/build trees with care. Mounting only ccache is simpler than making CMake's whole build directory persistent. A fresh machine with no imported cache still pays cold compile cost.
- Keep upstream acquisition, compilation toolchain, patch inputs and small runtime script copies isolated. For cold builds applying all patches before a single compile avoids the two extra incremental compiles. However it worsens warm reuse for frequent late patch edits because changing the cleanup/progress patch would invalidate the single compile layer; ccache or a dedicated reusable verified TDLib build stage should precede that change. Avoid restructuring solely for fewer RUN instructions.
- Make build jobs configurable with a conservative default instead of hardcoded 2. More parallel jobs help only when CPU and RAM permit; unrestricted `nproc` on small NAS risks OOM and a slower failed build. Changing Docker build settings does not change download concurrency.
- Use external registry/CI cache export/import for source layers on ephemeral builders. Ordinary layer caches and mutable cache mounts have different persistence/export semantics; plan mount-cache persistence explicitly rather than assuming `cache-to` automatically exports pnpm/ccache contents. Cache secrets must never include user runtime/data paths.
- Final runtime-only image already avoids shipping compilers/node. Review accidental inclusion of backend tests/tools only if image transfer size evidence warrants it; compile effort dominates the primary problem.

### Recommended sequence and acceptance

1. Documentation clarification and existing-image redeploy guidance; no topology change.
2. Approved image distribution design: registry ownership, first architecture, immutable tagging and manual release entry point. Build patched API once per source change; app independently.
3. Source build cache mounts and measured native cache/job tuning as developer optimization.
4. Optional arm64 publication after real build/runtime smoke checks.

Compare cold build on a fresh builder, identical warm rebuild, app-only edit, native patch edit and configuration-only recreation separately. Record dependency fetch/compile/image export durations and peak RAM instead of only total elapsed time. Verify artifacts have correct revision, app/API pair, protected volume mounts, supervisor no-credential startup, private progress/cache endpoint smoke behavior and no production secrets. No real Telegram calls are needed for these build checks.

## External references

- Docker cache optimization: https://docs.docker.com/build/cache/optimize/
- Docker cache backends: https://docs.docker.com/build/cache/backends/
- Multi-platform builds: https://docs.docker.com/build/building/multi-platform/
- GitHub Container Registry: https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry
- Compose up/no-build: https://docs.docker.com/reference/cli/docker/compose/up/

## Caveats / Not Found

- Research only: no code, workflow, registry, Git or running Docker changes made.
- No fresh builds, remote fetches or architecture checks performed. Exact timings and memory costs are unknown.
- Public images do not currently exist per repository docs; proposed GHCR addresses must not be presented as working commands.
- Existing local build cache cannot be assumed to exist on the user's NAS or GitHub runner.
