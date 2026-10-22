# TGDL · Telegram 媒体下载助手

将发送或转发给专属 Telegram Bot 的视频、图片，经确认后下载到本地或 NAS。通过中文 WebUI 完成配置、查看下载状态和管理本地 Bot API，适合个人自托管使用。

**[在线文档](https://yebook.github.io/TGDL/)** · [快速开始](docs/quick-start.md) · [问题反馈](https://github.com/yebook/TGDL/issues) · [参与贡献](CONTRIBUTING.md) · [MIT 许可](LICENSE)

## 使用流程

1. 在 Telegram 中，将视频或图片发送、转发给自己的 Bot。
2. 查看 Bot 返回的文件信息，点击「确认下载」或「取消」。
3. 确认后开始下载，在 WebUI 查看进度，完成后由 Bot 反馈成功或失败。

支持单文件和同组多文件，包括图片与视频混合的媒体组。只处理实际发送给 Bot 的媒体，暂不支持消息链接、频道自动监听或个人账号登录。

## 主要功能

- **确认下载**：用户 ID 白名单，确认有效期可配置，默认 30 分钟。
- **自动归档**：按 `YYYY-MM-DD/` 保存；单文件直接归档，同组文件放入组目录，优先使用消息文字摘要命名任务。
- **任务管理**：逐文件状态、云端及配套本地 API 的下载进度，区分下载与保存阶段；支持去重、取消、重试与恢复，文件并发默认 3，可配置。
- **WebUI 配置**：切换云端 / 本地 Bot API，自定义 Bot 确认提示和保存文件名。
- **配置备份**：独立密码加密导出并恢复配置与密钥，不包含任务、数据库或媒体。
- **记录管理**：单条或多选删除已结束任务记录，保留已下载的媒体文件。
- **维护与诊断**：脱敏日志默认保留 7 天，每日 08:00 清理，时间可配置；查看空间占用、导出和手动清理。
- **网络配置**：一份 Compose 部署，云端与本地统一使用 WebUI 的 HTTP CONNECT / SOCKS5 代理，无需 TUN；内置请求节流、冷却和认证异常暂停。

## 选择 Bot API 模式

| 模式 | 适用场景 | 所需 Telegram 凭据 |
| --- | --- | --- |
| 官方云端（默认） | 下载不超过 **20 MB** 的文件，先开始使用 | Bot Token，无需 API ID / Hash |
| 本地 Bot API | 下载超过 **20 MB** 的文件 | Bot Token、API ID 和 API Hash |

两种模式均需配置允许使用的 Telegram 用户 ID。标准部署包含常驻的本地 API 管理组件，无应用凭据时等待；之后可在 WebUI 填写 API ID / Hash、切换模式、查看进程状态并重试或重启 API 进程，日常无需重启应用或容器。详见 [本地 API 接入与凭据获取](docs/telegram-api.md)。

## 快速部署

需要 Docker Compose v2、Linux 容器环境、完整项目源码、Bot Token 和自己的数字 Telegram 用户 ID。目前采用**源码构建**，尚未提供可直接拉取的公开应用镜像。

### 1. 准备配置与目录

在项目根目录执行：

```sh
cp .env.example .env
```

编辑 `.env`，设置下载位置、非 root UID/GID 和 WebUI 绑定地址。启动前，按 [Docker / 飞牛 OS 部署指南](docs/deployment.md#目录和参数) 创建持久目录、授予读写权限，并确认 NAS 媒体共享已挂载。

| 根目录文件 | 用途 |
| --- | --- |
| [compose.yaml](compose.yaml) | 唯一部署入口，包含应用和本地 API 管理服务，初始使用云端模式 |
| [.env.example](.env.example) | 环境参数模板，复制为 `.env` 后修改 |

### 2. 构建并启动

完成上一步后，在项目根目录执行：

```sh
docker compose --env-file .env -f compose.yaml config --quiet
docker compose --env-file .env -f compose.yaml up -d --build
docker compose --env-file .env -f compose.yaml ps
```

首次构建需要下载依赖并编译本地 API，耗时较长。默认 WebUI 地址为 `http://127.0.0.1:8000`，仅部署主机可访问；NAS 内网访问需将 `.env` 中的 `TGD_BIND_IP` 改为 NAS 的固定局域网 IP，再访问对应地址。

### 3. 初始化并验证

从应用数据目录读取 `bootstrap.key`（默认 `data/bootstrap.key`），在 WebUI 中创建管理员账号和密码。随后选择官方云端模式，填写 Bot Token 和允许用户 ID，保存配置。

保存配置无需在线验证；可手动点击「校验配置」，并在系统状态页查看实际连接。本地模式的“本地 API 可用”只表示服务可响应，仍需实际收发消息验证 Telegram 连接。先向 Bot 发送一个小于 20 MB 的媒体文件，确认下载并检查保存结果。完整步骤见 [快速开始](docs/quick-start.md)。

## 文档

在线阅读：**https://yebook.github.io/TGDL/**，支持中文搜索；也可直接阅读仓库中的 [文档索引](docs/README.md)。

| 主题 | 内容 |
| --- | --- |
| [Docker / 飞牛 OS 部署](docs/deployment.md) | 目录权限、挂载、内网访问和代理部署 |
| [Telegram API 接入](docs/telegram-api.md) | API ID / Hash 获取、云端与本地切换、进程管理 |
| [任务与归档](docs/usage.md) / [模板设置](docs/templates.md) | 多文件、目录结构、任务操作、Bot 提示和文件名 |
| [配置备份与恢复](docs/configuration-backup.md) | 独立密码加密，迁移配置及密钥，不包含业务数据和文件 |
| [维护与诊断](docs/operations.md) / [常见问题](docs/faq.md) | 日志、备份恢复、请求保护和故障排查 |
| [开发指南](docs/development.md) / [文档站维护](docs/pages.md) | 本地开发、测试、文档构建与发布 |

当前处于首版实现与验证阶段，真实 Telegram 大文件、飞牛 OS 和代理的完整验收仍待完成，详见 [验证范围](docs/verification.md)。

## 参与贡献

欢迎通过 [Issues](https://github.com/yebook/TGDL/issues) 反馈问题、提出建议，或提交 Pull Request。开发环境和检查命令见 [开发指南](docs/development.md)，提交约定见 [贡献指南](CONTRIBUTING.md)，已记录的变更见 [CHANGELOG](CHANGELOG.md)。

报告问题时请勿包含 Bot Token、API Hash、初始化密钥或真实媒体；安全漏洞请按 [安全报告指引](SECURITY.md) 提交。请求保护用于减少无效调用，无法保证 Telegram 不限制 Bot 或关联账号。

## 许可与致谢

本项目采用 [MIT 许可证](LICENSE)。感谢 [tg-bot-video-download](https://github.com/welkin621/tg-bot-video-download) 提供的参考思路。
