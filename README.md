# TGDL · Telegram 媒体下载助手

把发送或转发给专属 Telegram Bot 的视频、图片，经确认后保存到本地或 NAS。通过中文 WebUI 配置 Bot、管理下载和维护服务，适合个人内网自托管。

**[在线文档](https://yebook.github.io/TGDL/)** · [第一次下载](docs/quick-start.md) · [更新日志](CHANGELOG.md) · [问题反馈](https://github.com/yebook/TGDL/issues)

目前尚未正式发布；[当前版本与更新日志](https://yebook.github.io/TGDL/changelog.html) 可在文档站查看。采用源码构建，暂未提供可直接拉取的公开应用镜像；真实 Telegram 大文件、Docker 宿主机和代理完整链路的验收范围见 [验证说明](docs/verification.md)。

## 怎么使用

1. 将实际视频、图片或同组多文件发送、转发给自己的 Bot。
2. 核对 Bot 返回的信息，点击「确认下载」或「取消」。
3. 在 WebUI 查看下载、保存进度，完成后接收 Bot 结果反馈。

单文件按 `YYYY-MM-DD/` 保存；同组图片与视频放在日期下的同一个组目录，优先使用消息文字摘要命名，不附加任务编号。组目录重名时使用「名称 (2)」「名称 (3)」递增，不覆盖已有目录。只处理实际媒体，暂不支持消息链接、频道自动监听或个人账号登录。

## 主要功能

- **下载与归档**：单文件和混合媒体组、可读大小、默认 30 分钟确认期限、默认 3 个文件并发、去重、取消与失败重试。
- **任务管理**：逐文件真实进度，区分下载与保存；搜索、多选和删除已结束记录，保留媒体文件。
- **简洁 WebUI**：最顶部与账户位于同一顶栏查看连接、队列与剩余空间，左下角查看版本；分区配置网络、保存及提示，调试详情默认隐藏。
- **自定义与维护**：Bot 提示、文件名模板；含密钥的密码加密配置备份；日志占用、导出及清理，默认保留 7 天、每天 08:00 清理。
- **统一连接配置**：同一 WebUI 切换云端与本地 API，支持 HTTP CONNECT / SOCKS5 代理，无需 TUN 或额外代理 Compose 文件。

## 选哪种 API

| 模式 | 适用场景 | 需要 |
| --- | --- | --- |
| 官方云端（默认） | 先开始使用，单文件不超过 **20 MB** | Bot Token |
| 本地 Bot API | 下载超过 **20 MB** 的文件 | Bot Token、API ID 和 API Hash |

两种模式均需配置允许使用的数字 Telegram 用户 ID。标准部署已包含本地 API 管理服务，之后在 WebUI 填写应用凭据、切换模式即可，日常无需重启容器。详见 [大文件与 API 配置](docs/telegram-api.md)。

## 部署

需要完整项目源码、Docker Compose v2、Linux 容器环境、专属 Bot Token 和自己的 Telegram 用户 ID。下载目录需已挂载且允许非 root 用户写入，数据库使用可靠本地存储。

根目录 [compose.yaml](compose.yaml) 是唯一部署入口，环境参数模板是 [.env.example](.env.example)。在项目根目录执行：

```sh
cp .env.example .env
```

编辑 `.env`，设置持久目录、非 root UID/GID 和 WebUI 绑定地址，按 [部署指南](docs/deployment.md#目录和参数) 创建目录并授予权限。使用 NAS 等网络存储时，确认媒体共享已挂载后再启动：

```sh
docker compose --env-file .env -f compose.yaml config --quiet
docker compose --env-file .env -f compose.yaml up -d --build
docker compose --env-file .env -f compose.yaml ps
```

首次构建会编译本地 API，耗时较长。默认访问 `http://127.0.0.1:8000`；其他设备通过内网访问需将 `TGD_BIND_IP` 改为部署主机的固定局域网 IP。用应用数据目录的 `bootstrap.key`（默认 `data/bootstrap.key`）初始化管理员。

进入「设置 → 连接 Telegram」，填入 Bot Token 和自己的用户 ID 并保存。保存无需在线校验；先发送 `/start` 和小于 20 MB 的媒体验证结果。本地模式显示「本地 API 可用」仅表示服务可响应，仍需实际收发消息确认 Telegram 连接。

## 使用指南

| 想做什么 | 文档 |
| --- | --- |
| 完成安装与第一次下载 | [Docker](docs/deployment.md) · [首次使用](docs/quick-start.md) |
| 修改保存、连接、日志等选项 | [设置怎么用](docs/settings.md) |
| 下载大文件、获取应用凭据、配置代理 | [本地 API 与网络](docs/telegram-api.md) |
| 查看进度、管理任务和目录 | [任务与归档](docs/usage.md) · [提示与命名](docs/templates.md) |
| 迁移配置、处理故障 | [配置备份](docs/configuration-backup.md) · [维护](docs/operations.md) · [常见问题](docs/faq.md) |

## 参与与安全

欢迎通过 [Issues](https://github.com/yebook/TGDL/issues) 反馈问题或提交 Pull Request；开发与检查命令见 [开发指南](docs/development.md)，提交约定见 [贡献指南](CONTRIBUTING.md)。文档站的发布只更新静态文档，不部署应用。

报告问题请提供版本、部署方式、复现步骤及脱敏错误码，不要发送 Bot Token、API Hash、初始化密钥、数据库或真实媒体。安全问题按 [安全报告指引](SECURITY.md) 处理。只保存自己有权处理的媒体；项目的节流、等待与认证暂停用于减少无效调用，无法保证 Telegram 不限制 Bot 或关联账号。

## 许可与致谢

采用 [MIT 许可证](LICENSE)。第三方组件适用各自许可证，见 [构建来源](deploy/sources.md)。感谢 [tg-bot-video-download](https://github.com/welkin621/tg-bot-video-download) 提供参考思路。
