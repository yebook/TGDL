# Research: Public documentation and static site

- Query: 整理开源项目文档并准备 GitHub Pages，保留 GitHub Markdown 阅读体验；不得恢复已移除的 GitHub Actions。
- Scope: mixed; repository facts inspected, external references provided without claiming live verification
- Date: 2026-10-21

## Findings

### Files found and source of truth

| File | Purpose / observed fact |
| --- | --- |
| `README.md` | 入口介绍、部署命令、范围；混入当前电脑 `.runtime` 和 Trellis 规划链接，应缩成面向新用户的入口。 |
| `compose.yaml`, `compose.proxy.yaml`, `.env.example` | 部署真实来源；前者 app+常驻 API 管理组件，默认 cloud，后者独立 TUN 拓扑；不是叠加文件。 |
| `Dockerfile` | 多阶段源码构建，仅复制 frontend/backend/deploy；文档依赖应独立，不加入应用构建。 |
| `deploy/sources.md` | 官方 API 及基础镜像构建来源，公共文档应保留入口。 |
| `docs/deployment.md` | 最完整用户部署手册；保留文件名避免破坏已有链接。 |
| `docs/telegram-api.md` | 凭据获取、WebUI 切换、外部管理兼容；含真实可执行 logOut 辅助代码，必须强调标准部署用 WebUI，勿把文档审查变成执行。 |
| `docs/operations.md` | 维护、请求保护、诊断日志、备份、恢复、权限；日志策略本轮同时在变更，需要最终同实现核对。 |
| `docs/templates.md` | 用户命名与通知配置参考。 |
| `docs/local-run.md` | 当前 Windows 电脑的特殊部署记录，引用不随源码分发的临时 compose；不能作为一般用户快速开始。 |
| `docs/acceptance.md`, `docs/docker-acceptance.md` | 可重复检查工具文档，适合开发/验证栏目。 |
| `docs/verification.md` | 43 KB 逐次会话历史，重复段、实时本机状态、过往 CI 说法；不是发布说明或快速开始。 |
| `docs/project-requirements.md` | 早期产品发现与决策史，引用 `.trellis`，且抬头技术方案待评审过时；不适合首页导航。 |
| `docs/README.md` | 当前文档索引，含 `.trellis` 实施资料和机器当前状态；改为公共文档导航。 |
| `frontend/package.json` | Node24/pnpm11 应用包；Vue/Vite，但没有文档站依赖。 |
| `.gitignore`, `.dockerignore` | 已忽略 node_modules/dist/runtime；新站构建缓存与产物需显式覆盖，不让站构建进入镜像。 |
| `.trellis/spec/backend/diagnostic-log-contracts.md` | 诊断事件脱敏、安全文件边界及授权导出的现有契约。 |

未发现根目录 LICENSE、CONTRIBUTING、SECURITY、CHANGELOG。不得擅自选择许可证/版权主体；无许可证不应声称可按 MIT 等授权复用。可增加贡献流程与安全报告说明，不能捏造私有联系邮箱或承诺安全响应时限。

### Code patterns and constraints (citations)

- `Dockerfile:4`、`:6`、`:7` 的 frontend 构建仅需应用 package；文档独立 `docs/package.json` 和锁文件避免扩大镜像构建依赖。
- `Dockerfile:19`、`:20`、`:21` 最终镜像只复制 backend、frontend/dist、入口；文档站不应成为应用运行时依赖。
- `docs/deployment.md:5` 部署源文件在根目录；`:105` 默认云端；`:125` WebUI 接入本地；`:133` 旧版升级；`:169` TUN 代理；这些应保留为权威用户流程。
- `docs/deployment.md:15` 存在未分发 `.runtime` 文件条目；`:139` 回链当前电脑的迁移；公开部署指南应移除机器特例或改为不依赖私有文件的通用说明。
- `README.md:26` 当前电脑临时栈；`:87`、`:88` Trellis 链接；应移出公开入口。
- `docs/README.md:6`–`:8` 规划与研究链接会使站点泄漏内部布局，VitePress 也不能默认把仓库外链接变为可浏览页面。
- `docs/local-run.md:3` 明示针对当前电脑；`:11` 等引用 ignored 文件；应重写为适用于任意 Windows 开发者的指引，或从站点排除并明确为历史记录。
- `docs/verification.md:36`、`:52` 旧 CI 状态；`:83`、`:84` 同一浏览器结果重复；不要逐轮改写历史为当前事实，改为当前覆盖范围摘要，原始细节留开发历史而不发布整个 Trellis。
- `backend/app/services/diagnostics.py:97` 原诊断默认 3 归档；`:219` status、`:223` export 是本轮自动/手动清理的交汇；旧 `docs/operations.md:28` 固定约40 MiB不保证天数文案需按最终实现更新。

### Recommended information architecture

Prefer preserve existing document filenames, reorganize navigation rather than bulk moves:

- Root README: name, purpose, main capabilities, supported scope/limitations, short deployment path, public docs index, development/contribution/security/license status.
- `docs/index.md`: site homepage with getting started, day-to-day use, troubleshooting; `docs/README.md` for GitHub index may link same public pages.
- User: existing deployment, telegram-api, templates, operations; new compact `quick-start.md`, `usage.md`, `faq.md` if not duplicating deployment.
- Developer: new `development.md` with Python3.12/uv Node24/pnpm11 commands, isolated test boundary; existing acceptance and docker-acceptance as detailed tool references.
- Project: root CONTRIBUTING.md, SECURITY.md; document license decision is pending. Do not fabricate CHANGELOG from unverified feature dates. Add changelog only with reviewed release scope, no fake release tags.
- `docs/verification.md`: keep concise current verification matrix and clear real Telegram/local >20MB/TUN/fnOS limitations; historical operational specifics can remain outside public site. Do not publish `.runtime` directory or `.trellis` research to satisfy broken links.
- `project-requirements.md`: optional historical design record excluded from default site; if kept in GitHub docs, label historical and source of current behavior is user guides. Never expose internal architecture simply to resolve its `.trellis` links.

Avoid per-section duplicated facts: deployment owns Compose/bootstrap/volume permissions, telegram-api owns API credentials & switching, operations owns log retention/clear and backups, templates owns placeholders, development owns check commands. Link instead of copying commands into every document.

### Static site recommendation

Recommend a pinned **stable VitePress 1.x** in `docs/package.json` with independent `docs/pnpm-lock.yaml`, `.vitepress/config.mts`, standard theme, Chinese navigation and local search. Choose a concrete supported version from actual package registry when implementing and verify Node24 compatibility with the real build; do not use prerelease solely because latest points there. No Vue app integration or external CDN assets needed. Keep Markdown content in docs as the source, avoid Vue-only blocks or absolute `/foo` links in prose so GitHub remains readable.

- `base: '/TGDL/'` for intended repository Pages URL; allow environment override `/` for generic static host. Case matters.
- `srcExclude` explicitly excludes `project-requirements.md`, historical verification if retained, tooling README duplicates when appropriate, package/lock/scripts content. Build only the curated public `.md` pages; nav exclusion alone does NOT exclude a page from build/search.
- Default local search is bundled, works without an external search service.
- Treat a production static build as **offline network independent when served over localhost**. Do not promise double-clicking `file://.../index.html`: VitePress JavaScript modules/client routing usually require HTTP. Provide `pnpm docs:preview` or a documented Python `http.server` command under `/TGDL/`; preview is simpler because it honors base.
- VitePress source links outside docs (`../compose.yaml`, `../Dockerfile`, `../deploy/sources.md`) require deliberate handling: convert deployment source references to canonical GitHub blob links while leaving file paths in code, or copy a narrow reviewed set to static downloads during build; never allow arbitrary repo traversal or symlink publishing.
- Build default detects missing page links; add repository Markdown relative-link/anchor validator since VitePress does not replace full GitHub Markdown link checking. Internal links `.md` should work both GitHub and site; test generated HTML nav/assets under `/TGDL/`, not only root `/`.
- Standalone script can validate anchors including Chinese heading slugs and duplicate suffixes; code-fence text is not a live link. Explicit exclusion must not silently exempt public pages.

Alternative: MkDocs removes Node-docs toolchain but adds Python-doc dependency and dual tooling; still requires explicit nav, base behavior and public-source filtering. Docsify consumes Markdown without build but often CDN/runtime fetch reliant, no reliable built-link gate, and file:// still not broadly usable; not recommended here.

### Pages publishing without repository Actions

Do not create `.github/workflows/*`. Repository owner may choose Settings → Pages → Deploy from a branch → `gh-pages` / root. Locally build `.vitepress/dist`, publish only that generated tree with `.nojekyll` to a dedicated branch; explain branch deploy rather than dumping generated files into main or docs source. Use a separate worktree/temporary repository, verify exact target, no force push by default, preserve CNAME if intentionally configured. Provide preparatory instructions and optionally a dry-run artifact before any actual publication. Current user authorized preparation of docs/static site, not assumed repository visibility changes or actual public exposure of previously private repository data.

GitHub branch publishing may itself use GitHub-managed Pages build/deploy runs visible in Actions; no repo workflow is needed. Do not promise absolutely no Actions entries; distinguish removing repository CI workflows from GitHub's hosting internals. GitHub Pages plan/private-repo availability is unknown. A private source repository's Pages may still be public depending plan/access-control setup. Verify repository visibility, plan support, and intended audience before publishing, never change repo visibility to make Pages available. Static build also usable with nginx/Caddy/NAS web hosting if Pages unavailable.

### External references

These are reference targets, not claims of live fetched documentation in this research session:

- VitePress deployment/base: https://vitepress.dev/guide/deploy
- VitePress site configuration: https://vitepress.dev/reference/site-config
- VitePress local search: https://vitepress.dev/reference/default-theme-search
- GitHub Pages publishing source: https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site
- GitHub Pages limits/availability: https://docs.github.com/en/pages/getting-started-with-github-pages/about-github-pages
- GitHub licensing a repository: https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository

## Related specs

- `.trellis/spec/frontend/index.md`: scoped checks/build requirements; docs standalone should not require broad application browser suite.
- `.trellis/spec/backend/diagnostic-log-contracts.md`: logging privacy and endpoint authorization; UI docs must match retention/clear implementation.
- `.trellis/spec/guides/cross-layer-thinking-guide.md`: settings/status/docs DTO touchpoints.

## Caveats / Not Found

- No external network calls, runtime secrets, user database, Docker or real Telegram accessed.
- No existing documentation generator/dependency/Pages publish branch confirmed; `.github/workflows` directory currently exists but no workflow contents were accessed/needed.
- Repository plan/visibility, Pages enabled state, license choice, canonical public docs URL remain unverified. Never claim site already online.
- Concurrent log-retention implementation may alter defaults, UI labels and endpoint names; only final reviewed production behavior should enter docs.
- Research writes only this task research file; no application, docs or package edits performed.

## Confirmed user steering after initial scan

Main session confirmed user explicitly chose MIT and default diagnostic retention of 7 days. Therefore add MIT LICENSE with 2026 project contributors / yebook attribution consistent with repository identity, do not guess real personal name. Earlier license-pending recommendations are superseded by this explicit authorization. Operations docs must state 7-day default with final implementation's valid range, 0/off behavior and cleanup schedule verified separately.

Minimal implementation handoff: independent `docs/package.json` and lockfile, `.vitepress/config.mts` pinned stable VitePress, preserve existing MD paths, small docs/index homepage, concise root/docs indexes and standalone development/Pages guides. Build `/TGDL/` locally, static output under ignored docs/.vitepress/dist, no Dockerfile/frontend package changes or workflow. Curated public Markdown only; either remove internal links from published pages or exclude history pages explicitly from build/search. GitHub source links for root deployment files prevent cross-root VitePress import ambiguity. Screenshot/smoke test production preview at /TGDL/ and verify Markdown links; no app regression needed unless actual app files touched.
