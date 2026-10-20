# Telegram 媒体下载助手

将发送或转发给专属 Telegram Bot 的视频、图片保存到本地或 NAS。确认后后台下载，Bot 反馈结果，WebUI 管理设置与任务。主要面向飞牛 OS 的 Docker 自托管用户。

## 首版范围

- 单文件直接保存到 `YYYY-MM-DD/`，媒体组保存到该日期下的「文字摘要_短ID」目录，同组图片、视频放在一起。
- 优先用转发消息的附带文字命名任务，缺失时使用原文件名或媒体类型；标题不追加编号，Bot 与 WebUI 使用一致标题，另行显示完整任务编号。
- 大于 20 MB 的视频，通过本地 Telegram Bot API 服务获取。
- 同一个 WebUI 可切换官方云端 / 本地 Bot API；云端只需 Bot Token，下载上限 20 MB。
- 用户 ID 白名单、确认有效期、去重、排队、取消、重试、重启恢复。
- 中文 WebUI：初始化、登录、任务、配置、系统状态；已结束任务支持单条或多选删除记录，保留媒体文件。
- 默认确认 30 分钟、全局同时下载 3 个文件、失败最多自动重试 3 次，均可配置。
- 可选 HTTP CONNECT/SOCKS5 网络代理；WebUI 主要供内网访问。

首版不处理消息链接，不登录个人 Telegram 账号，不监听频道，不提供转码、播放和媒体删除。

## 部署

**Compose 文件在项目根目录，与本 README 同级**。本文中的文件路径均相对于项目根目录，命令也在该目录执行。

| 部署方式 | 配置文件 | 适用情况 |
| --- | --- | --- |
| 默认服务 | [compose.yaml](compose.yaml) | 应用 + 常驻本地 API 管理组件；默认云端，无凭据时本地 API 等待 |
| 本地 API 需要代理 | [compose.proxy.yaml](compose.proxy.yaml) | 应用 + 本地 Bot API + TUN 网络组件，独立使用此文件 |
| 当前 Windows 仅体验 WebUI | `.runtime/local-docker/compose.web.yaml` | 当前电脑临时配置，不用于 NAS 部署；升级后可选云端下载 |

目前采用**完整源码在目标机器构建镜像**的方式，未提供可直接拉取的已发布应用镜像。请将整个项目复制到 NAS，不能只上传 YAML；构建还需要 `Dockerfile`、`backend/`、`frontend/`、`deploy/` 等文件，并保留 `.env.example`、`.dockerignore` 等隐藏文件。

飞牛 OS 的目录准备、`.env` 示例、命令行和 Docker Compose 界面操作见 [部署指南](docs/deployment.md)；当前电脑的 WebUI 体验及后续接入见 [Windows 本地运行](docs/local-run.md)。云端只需 Bot Token 和 Telegram 用户 ID 白名单，本地 API 才需要 API ID/API Hash；真实密钥只填写在本地配置中。

完成部署指南中的 `.env`、目录权限配置后，在项目根目录启动直连服务：

```sh
docker compose --env-file .env -f compose.yaml config --quiet
docker compose --env-file .env -f compose.yaml up -d --build
docker compose --env-file .env -f compose.yaml ps
```

默认绑定 `127.0.0.1:8000`，只能在 Docker 主机本机访问。要从电脑访问 NAS，请先在 `.env` 中将 `TGD_BIND_IP` 改为 NAS 的固定局域网 IP，再访问 `http://NAS局域网IP:8000`。

新部署默认使用云端，本地 API 管理组件常驻等待。取得 API ID / Hash 后在 WebUI 设置填写并选择本地模式，由后台协调任务等待、云端退出和本地连接；页面显示进程状态、切换步骤与失败原因，也可手动重试或重启 API 进程。修改凭据可重启 API 子进程，应用和容器保持运行。旧部署需先升级一次组件和控制目录挂载，外部 API 部署仍可保留手动管理。完整步骤见 [本地 Bot API 接入指南](docs/telegram-api.md)。云端代理在 WebUI 配置；本地 API 代理的直连/TUN 拓扑仍需部署时选定。API 缓存与最终文件可能同时占用空间，需预留容量。WebUI 不控制 Docker 或修改宿主机挂载。

首次管理员初始化使用 `TGD_HOST_DATA` 目录中的一次性 `bootstrap.key`，无通用默认密码。配置、密钥和数据库应一起备份；媒体文件单独备份。应用镜像和隔离容器验收已有本地结果，真实 Telegram、大文件、代理链路及飞牛 OS 实机验收仍待完成。

## 开发与验证

后端位于 `backend/`，前端位于 `frontend/`。使用 Python 3.12、uv 和 Node/pnpm。

后端工作目录：

```text
uv sync --locked
uv run ruff check app tests
uv run mypy app
uv run pytest -q
```

前端工作目录：

```text
pnpm install --frozen-lockfile
pnpm lint
pnpm typecheck
pnpm test:unit
pnpm build
```

这些命令验证应用实现；真实 Telegram、代理和飞牛 OS 需要相应部署环境。当前实际验证范围见 [验证记录](docs/verification.md)，不将模拟测试等同于设备验收。

自动浏览器验收使用真实本地后端、生产前端构建及独立临时数据库，不连接 Telegram。先安装浏览器，再在前端目录运行：

```text
pnpm exec playwright install chromium
pnpm test:e2e
```

`test:e2e` 会构建前端并启动、关闭本机测试服务，不需要配置 Bot。部署已有服务后，可运行 `python scripts/acceptance.py --base-url http://127.0.0.1:8000 --json` 检查接口；可选登录与结果解释见 [验收工具说明](docs/acceptance.md)。

## 文档

- [需求与已确认决策](docs/project-requirements.md)
- [技术设计](.trellis/tasks/10-19-telegram-media-downloader/design.md)
- [实施与验收计划](.trellis/tasks/10-19-telegram-media-downloader/implement.md)
- [运维、升级与备份](docs/operations.md)
- [自定义 Bot 提示与文件名](docs/templates.md)
- [飞牛 OS / Docker 部署](docs/deployment.md)
- [Windows 本地运行](docs/local-run.md)
- [本地 Bot API 切换与 API ID / Hash 获取](docs/telegram-api.md)
- [自动验收与部署检查](docs/acceptance.md)
