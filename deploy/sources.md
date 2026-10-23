# Pinned build sources

The managed API sidecar also uses Debian's `python3` and `python3-cryptography`
packages for its supervisor and encrypted credential handoff. These follow the
same distribution-managed package-index boundary described below.

Queried read-only from GitHub Git references/commit API and Docker Registry manifest headers on 2026-10-20. No application secrets were used.

| Source | Pin |
| --- | --- |
| Official `tdlib/telegram-bot-api` | `e3e9dd8e5b3d7ab8537cd5a10dc31d5ffa8f82d1`; its submodule gitlinks pin TDLib dependencies; repository returned no release tags |
| TDLib submodule | `bc9c263e2bfee06aaab41e82db51a103376030bc`, pinned by the Bot API source |
| `python:3.12.12-slim-bookworm` | `sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c` |
| `node:24.11.1-bookworm-slim` | `sha256:48abc13a19400ca3985071e287bd405a1d99306770eb81d61202fb6b65cf0b57` |
| `debian:bookworm-slim` | `sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587` |
| Python dependencies | `backend/uv.lock`, uv 0.12.5 |
| Frontend dependencies | `frontend/pnpm-lock.yaml`, pnpm 11.23.0 |

Each Dockerfile pins source revisions and multiarchitecture base manifest digests. Build-time Debian package indexes remain distribution-managed, so this is **not** a byte-for-byte reproducible or fully offline toolchain. Preserve built image digests and dependencies in your registry for repeat deployments. Runtime does not fetch `latest` or install dependencies.

The Bot API image applies [native-proxy.patch](bot-api/native-proxy.patch) to the exact pinned source and TDLib; this is a project-specific build, not an unmodified upstream binary. The patch starts TDLib networking disabled, explicitly selects SOCKS5 / HTTP CONNECT or disables cached proxies, and enables networking only after initialization succeeds. Upstream's original `--proxy` option remains webhook-specific. No TUN or separate network image is used.

The image installs the Bot API and TDLib license files. Review their license requirements before redistribution. This source list records provenance; it is not a successful image-build report.

Source deployments also include a precompiled linux/amd64 runtime under
`bot-api/prebuilt/`. TDLib is linked into the Bot API executable. The compressed
archive includes upstream licenses; its adjacent manifest records source pins,
native input identity and archive/binary checksums. Default builds verify and
use this artifact without a C++ compile. The explicit `native` build target
and `scripts/bundle_native.py` regenerate the archive when native inputs change.
Keep binaries, manifests, license notices and their corresponding pinned source
and project patches together when redistributing.

The same pinned build additionally applies [native-progress.patch](bot-api/native-progress.patch) and includes [ProgressCache.h](bot-api/ProgressCache.h). This extension exposes bounded in-memory download observations from existing TDLib updates to the application through a private local-only method. It does not start downloads or create Bot clients when queried; ordinary upstream builds do not provide this extension.

The source patch is checked by `git apply --check` before compilation. Keep its LF line endings (`.gitattributes` enforces this on Windows checkouts); changing the upstream pins requires rechecking and compiling the patch.
