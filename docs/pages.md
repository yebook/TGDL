# 静态文档站与 GitHub Pages

在线文档：[https://yebook.github.io/TGDL/](https://yebook.github.io/TGDL/)。

文档使用独立的 VitePress 1.6.4 包，源文件在 `docs/`，不改变应用前端依赖或 Docker 镜像。支持中文本地搜索、响应式目录与仓库子路径，已启用仅用于文档的自动发布工作流；后续发布状态可在仓库 Pages 页面查看。

## 构建与本地预览

安装 Node.js 24、pnpm 11.23.0，在 `docs/` 中运行：

```sh
pnpm install --frozen-lockfile
pnpm docs:build
pnpm docs:preview
```

打开终端提示的 HTTP 地址并带上 `/TGDL/`，默认如 `http://127.0.0.1:4173/TGDL/`。预览只监听本机；端口被占用或系统保留时，使用 `pnpm docs:preview --port 18769` 选择其他可用端口。`pnpm docs:dev` 用于实时编辑预览。站点资源需要 HTTP 服务，不能保证直接双击 `index.html` 可用。

输出位于 `docs/.vitepress/dist/`，包含 `.nojekyll`、页面、资源与本地搜索索引，构建产物和缓存不提交到主分支。默认 `base` 是大小写敏感的 `/TGDL/`。若托管到域名根路径，可在构建与预览两个命令的环境中设置 `DOCS_BASE=/`；PowerShell 可先执行 `$env:DOCS_BASE = '/'`。路径必须以 `/` 开始并结束。

构建会检查公开 Markdown 相对路径和锚点、VitePress 页面链接，以及输出中是否出现内部运行记录。`project-requirements.md` 和文档索引副本不生成页面；运行数据、内部任务与历史会话不进入页面或搜索。根目录部署文件使用明确的 GitHub 源码链接，站点不复制整个仓库。

## GitHub Pages：自动发布

工作流位于 `.github/workflows/docs-pages.yml`，只安装 `docs/` 的独立依赖并执行 `pnpm docs:build`，发布的内容仅为 `docs/.vitepress/dist/`。它不运行应用测试、不构建 Docker 镜像、不连接 NAS、不读取项目凭据，也不改变仓库可见性。使用 GitHub 自动签发的工作流身份，无需填写 Bot Token、API ID / Hash、SSH 密钥或 PAT。

1. **先检查公开范围**：如准备将仓库转为公开，需先检查远程分支、标签和 Git 历史是否含凭据或个人信息，而非只检查当前文件。发现真实凭据应先吊销、替换，再按需要清理历史；删除当前文件不能清除历史泄露。站点产物也需检查。仓库是否必须公开取决于 GitHub 套餐，私有仓库的 Pages 也未必私有。
2. 确认仓库 **Settings → Actions → General** 允许运行此工作流及其引用的官方 Actions。如果组织有策略限制，由管理员调整所需权限。
3. 打开 **Settings → Pages → Build and deployment**，将 **Source** 设为 **GitHub Actions**。本项目默认子路径为 `/TGDL/`；仓库改名、自定义域名或根路径托管时，先相应调整构建 base。
4. 将工作流及文档提交到 `main`。之后修改 `docs/**`、工作流自身或构建检查所依赖的根目录 `README.md`、`CONTRIBUTING.md`、`SECURITY.md`、`CHANGELOG.md`、`LICENSE`，或版本源 `backend/app/version.json` 并推送 `main`，会自动构建和部署。只修改其他应用代码不会触发此工作流；版本源参与文档版本显示，因此单独纳入触发路径。
5. 也可到 **Actions → Documentation Pages → Run workflow** 选择 `main` 手动发布；其他分支不会部署，PR 不触发发布。如果为 `github-pages` 环境设置了审批规则，需完成审批才会发布。
6. 等待 `build` 和 `deploy` 完成，从 **Settings → Pages** 获取实际站点地址，验证 `/TGDL/` 资源、中文搜索与手机目录。默认项目站点地址形式为 `https://<用户名>.github.io/TGDL/`；部署成功前不能认为站点已上线。

工作流使用 Node.js 24 和固定版本 pnpm 11.23.0，按锁文件安装依赖，明确以 `/TGDL/` 构建。引用的官方 Actions 锁定到已核对的提交；升级时应重新核对官方来源。发布操作串行执行，新的提交不会中断正在部署的站点。

发布失败时只检查 **Documentation Pages** 工作流的失败步骤。Pages 未启用、套餐限制或权限不足会阻止发布；文档链接检查失败应先修复链接。它们不会改变正在运行的应用。修复后推送文档变更，或在 **Actions → Documentation Pages** 手动重新运行，最终以最近一次成功部署为准。

## GitHub Pages：可选人工分支发布

不希望运行仓库文档工作流时，可在仓库 Actions 中停用 **Documentation Pages**，改用 Pages 的 **Deploy from a branch**，以仅包含生成站点的 `gh-pages` 分支根目录作为发布源。GitHub 自身仍可能生成托管的 Pages 构建/部署任务并显示在 Actions。

以下为**人工发布步骤**，仅在选择分支模式时执行：

1. 在主源码工作区运行完整文档构建，预览 `/TGDL/`，检查搜索、手机目录及生成文件。
2. 使用一个单独的发布工作区维护 `gh-pages`。首次创建仅包含构建输出的分支；已有分支先获取最新提交，不强制覆盖历史。
3. 将 `docs/.vitepress/dist/` 内的内容复制到该工作区根目录，包含隐藏的 `.nojekyll`。仅替换站点文件，若已有自定义域名配置，保留并核对 `CNAME`。不要复制源码、`.env`、数据或整个项目目录。
4. 检查提交文件清单与差异，提交后推送 `gh-pages`。这一步会发布站点，应在站点内容与公开范围确认后执行。
5. 到 GitHub 仓库 **Settings → Pages → Build and deployment → Deploy from a branch**，选择 `gh-pages`、`/(root)`，保存并等待托管部署完成。
6. 从 Pages 页面获取实际站点地址，验证资源子路径、搜索和移动端。成功前不要添加“已上线”链接或徽章。

首次从独立空目录初始化发布仓库时，可使用 `git init -b gh-pages`，然后只复制生成文件再提交；remote 使用自己的仓库 SSH 地址。已有发布分支时使用其独立克隆或 worktree，不重复创建孤立历史。本文不提供强制推送或清空目录命令，避免误操作源码与现有站点。

## NAS 或其他静态托管

若 Pages 不可用，生成产物可由 nginx、Caddy 或 NAS 静态服务托管。域名根路径使用 `DOCS_BASE=/` 重建；子目录托管确保 URL 与构建 base 一致。站点是静态文件，不需要应用数据库、Bot Token、Docker socket 或 API 管理组件。只发布生成目录，不挂载应用数据目录。
