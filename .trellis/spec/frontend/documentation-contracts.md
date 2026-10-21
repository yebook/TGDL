# Public documentation contracts

## 1. Scope / Trigger

Read when changing README, public guides or the static documentation site.

## 2. Signatures

The independent docs package builds static files under docs/.vitepress/dist;
its source is curated public Markdown, with `/TGDL/` as the default site base.
`pnpm docs:preview [--port PORT]` serves only that output on 127.0.0.1;
`DOCS_BASE` is the same slash-delimited base used at build time.

## 3. Contracts

- Public pages describe actual application behavior and portable deployment
  commands. Keep internal plans, machine-specific runtime records and historical
  sessions outside the generated site and search index.
- Root README is the open-source landing page: purpose, visible online docs,
  core workflow/features, executable deployment prerequisites, guide links,
  contribution/security and MIT attribution. Put detailed operational and docs
  toolchain instructions in the guides; never shorten setup into commands that
  omit required directory permissions or claim unverified NAS support.
- Documentation dependencies and lockfile live in docs independently of frontend
  and the application Docker image. Keep release-age policy enabled.
- Use strict missing-link checks; test the case-sensitive `/TGDL/` base, local
  search and mobile navigation. Static assets need HTTP serving; do not promise
  double-click file browsing. Build output/cache are ignored.
- VitePress 1.x preview does not reliably honor a host argument. The local
  preview wrapper must bind loopback explicitly, allow read-only GET/HEAD,
  reject path traversal/symlinks and serve correct HTML/JS/CSS MIME types.
- Root source links must resolve from GitHub Markdown and the site. Use reviewed
  canonical source links where needed; never publish the entire repository merely
  to resolve documentation links.
- MIT was explicitly authorized. Do not invent release tags, published images,
  contact addresses, live site URLs or completed real Telegram/NAS acceptance.
- A clean static artifact does not imply a clean public source repository. Before
  visibility changes review reachable Git history and author/committer metadata;
  deleting current files cannot hide past emails or internal notes. Keep findings
  redacted and require explicit authorization for history rewriting/force-push.
- User authorized a documentation-only Pages workflow on 2026-10-21. It may build
  only the independent docs package and publish only docs/.vitepress/dist, with
  path-scoped main-branch pushes and explicit manual dispatch. Do not restore
  application checks, build application images, access NAS/SSH or change repository
  visibility. Use minimal Pages/OIDC permissions and no project credentials.
- Pin official Actions to verified commit SHAs. Select Node 24 before installing
  pnpm 11.23.0, use frozen docs lockfile, and set DOCS_BASE=/TGDL/. Serialize
  deployments without cancelling one already in progress. The user has accepted
  the audited existing commit-email/internal-note disclosure and authorized
  ordinary push/publication; history rewriting remains outside authorization.

## 4. Validation & Error Matrix

| Condition | Required result |
| --- | --- |
| Broken public page link | Build/check fails |
| Historical/internal source | Excluded from generated pages and search |
| Pages not published | No live-site claim |
| Repository hosting unavailable | Static artifact remains usable on other hosts |

## 5. Good / Base / Bad Cases

Good: portable deployment guides work from source and static hosting. Base: a
local HTTP preview renders under `/TGDL/`. Bad: publishing the repository root
exposes runtime configuration merely to make relative links resolve.

## 6. Tests Required

Verify standalone build, links, generated content exclusion and mobile/search
under the configured base. Documentation-only changes need no app regression.

## 7. Wrong vs Correct

Wrong: restore application CI or deploy to NAS alongside the authorized docs job.
Correct: isolate the explicitly authorized Pages workflow to documentation inputs,
dependencies and artifacts; do not change repository visibility automatically.
