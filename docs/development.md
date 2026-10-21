# 开发指南

后端位于 `backend/`（Python 3.12、FastAPI、SQLite），前端位于 `frontend/`（Vue、TypeScript）。部署服务为单实例，不运行多个应用 worker。

## 工具与依赖

当前仍在首版开发中，不为未发布版本增加兼容分支；功能修改同步更新当前契约、测试与文档。保留现有测试数据和安全状态，不用清空数据库规避问题。正式发布后再制定旧版本兼容和迁移策略。

安装 Python 3.12、uv、Node.js 24 和 pnpm 11.23.0。使用提交的锁文件，不手动修改锁定版本。文档有独立依赖包，不加入应用镜像或前端依赖。

在 `backend/` 中执行：

```sh
uv sync --locked
uv run ruff check app tests tools ../scripts ../deploy
uv run mypy app
uv run pytest -q
```

在 `frontend/` 中执行：

```sh
pnpm install --frozen-lockfile
pnpm lint
pnpm typecheck
pnpm test:unit
pnpm build
```

优先运行受影响的测试文件或用例；共享基础设施、发布检查或出现未解决失败时再扩大范围。以上完整命令供需要全量验证时使用，不要求每次文字修改都全量回归。

## 浏览器与容器检查

在 `frontend/` 安装浏览器并运行：

```sh
pnpm exec playwright install chromium
pnpm test:e2e
```

该命令构建前端，启动真实本地后端及独立临时数据库，不连接 Telegram。不要把测试服务指向生产数据。Windows 的部分 POSIX 文件权限测试会跳过，需要 Linux 隔离环境补充。

运行中部署的只读检查见 [部署后验收](acceptance.md)；使用临时容器修改临时数据的工具见 [Docker 隔离验收](docker-acceptance.md)。真实设备待测项见 [验证范围](verification.md)。

## 文档开发

在 `docs/` 使用 `pnpm install --frozen-lockfile`、`pnpm docs:build` 和 `pnpm docs:preview`。构建会检查公开 Markdown 的相对链接、锚点与静态输出边界；不请求外部网站检查可达性。完整说明见 [文档站](pages.md)。

## 贡献与安全

提交时附上问题说明、变更行为、测试结果与未验证边界。贡献、安全报告、许可和变更入口见 [项目说明](project.md)。测试不应要求真实凭据、读取用户数据或向 Telegram 发送消息。
