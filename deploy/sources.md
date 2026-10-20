# Pinned build sources

The managed API sidecar also uses Debian's `python3` and `python3-cryptography`
packages for its supervisor and encrypted credential handoff. These follow the
same distribution-managed package-index boundary described below.

Queried read-only from GitHub Git references/commit API and Docker Registry manifest headers on 2026-10-20. No application secrets were used.

| Source | Pin |
| --- | --- |
| Official `tdlib/telegram-bot-api` | `e3e9dd8e5b3d7ab8537cd5a10dc31d5ffa8f82d1`; its submodule gitlinks pin TDLib dependencies; repository returned no release tags |
| `SagerNet/sing-box` v1.11.15 | `bc35aca01704497c179da1a03e45ad8e32f1a51b` |
| `python:3.12.12-slim-bookworm` | `sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c` |
| `node:24.11.1-bookworm-slim` | `sha256:48abc13a19400ca3985071e287bd405a1d99306770eb81d61202fb6b65cf0b57` |
| `golang:1.24.10-bookworm` | `sha256:aaa1dedbfef846dbd62fa803cecd7486b0e98a32cb6f68656eee20acac5dd53a` |
| `debian:bookworm-slim` | `sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587` |
| Python dependencies | `backend/uv.lock`, uv 0.12.5 |
| Frontend dependencies | `frontend/pnpm-lock.yaml`, pnpm 11.23.0 |

Each Dockerfile pins source revisions and multiarchitecture base manifest digests. Build-time Debian package indexes remain distribution-managed, so this is **not** a byte-for-byte reproducible or fully offline toolchain. Preserve built image digests and dependencies in your registry for repeat deployments. Runtime does not fetch `latest` or install dependencies.

The Bot API image installs its and TDLib's license files. The network image includes the sing-box license. Review upstream license requirements before redistribution; the network build contains GPL-licensed sing-box. This source list records provenance; it is not a successful image-build report.
