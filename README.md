# TGDL · Telegram 媒体下载助手

把发送或转发给专属 Telegram Bot 的视频、图片，确认后保存到本地或 NAS。通过中文 WebUI 配置 Bot、查看任务和管理本地 Bot API，面向个人自托管与飞牛 OS 用户。

## 能做什么

- 单文件、同组多文件及图片/视频混合归档，Bot 确认后下载并反馈结果。
- 按日保存：单文件直接放入 `YYYY-MM-DD/`，同组文件放入日期下的组目录；优先采用消息文字摘要命名任务。
- 同一 WebUI 切换官方云端与本地 Bot API。云端下载上限 **20 MB**，无需 API ID / Hash；本地模式用于大文件。
- 白名单、可配置确认有效期（默认 30 分钟）、全局文件并发（默认 3）、去重、取消、重试与恢复。
- 已结束任务可单条或多选删除记录，保留媒体文件；可自定义 Bot 提示与文件名。
- 持久脱敏诊断日志，默认保留 7 天，可导出、调整保留天数或手动清理。
- 可选 HTTP CONNECT / SOCKS5 代理；内置请求节流、冷却和认证异常暂停。

只处理实际发给 Bot 的媒体，暂不支持消息链接、个人账号登录、频道监听、转码或媒体删除。请求保护可减少无效调用，不能保证 Telegram 不限制 Bot 或关联账号。

## 开始部署

部署文件位于**项目根目录**，与本 README 同级：

| 文件 | 用途 |
| --- | --- |
| [compose.yaml](compose.yaml) | 默认部署：应用 + 常驻本地 API 管理组件，初始使用云端 |
| [compose.proxy.yaml](compose.proxy.yaml) | 本地 API 需要 TUN 代理时独立使用，不与默认文件叠加 |
| [.env.example](.env.example) | 复制为 `.env`，配置下载位置、UID/GID 和 WebUI 绑定地址 |

目前需要**完整源码构建**，尚未提供可直接拉取的公开应用镜像。先阅读 [快速开始](docs/quick-start.md) 与 [Docker / 飞牛 OS 部署](docs/deployment.md)，准备 `.env`、持久目录和权限，再在项目根目录执行：

```sh
docker compose --env-file .env -f compose.yaml config --quiet
docker compose --env-file .env -f compose.yaml up -d --build
docker compose --env-file .env -f compose.yaml ps
```

默认 WebUI 为 `http://127.0.0.1:8000`；NAS 内网访问需要将 `.env` 的 `TGD_BIND_IP` 改为 NAS 固定局域网 IP。首次初始化使用数据目录的 `bootstrap.key`，没有通用默认密码。

先在 WebUI 保存 Bot Token 和允许用户 ID 即可使用云端。之后可在 WebUI 填写 API ID / Hash 并切换本地，日常无需重启应用或容器；操作状态、失败重试和 API 进程重启都在页面中查看或执行，详见 [本地 API 接入](docs/telegram-api.md)。

## 文档与开发

- [文档索引](docs/README.md)：部署、使用、模板、日志、维护与常见问题。
- [开发指南](docs/development.md) / [参与贡献](CONTRIBUTING.md) / [安全报告](SECURITY.md) / [变更记录](CHANGELOG.md)。
- [验证范围](docs/verification.md)：模拟测试与真实 Telegram、飞牛 OS、TUN 验收分开记录，尚未完成的实机验证不能视为已通过。
- [在线文档](https://yebook.github.io/TGDL/)：支持中文搜索；[构建与发布指南](docs/pages.md)介绍本地预览、GitHub Pages 和 NAS 静态托管。

在 `docs/` 中运行 `pnpm install --frozen-lockfile`、`pnpm docs:build`、`pnpm docs:preview`，即可本地查看文档站；默认子路径 `/TGDL/`。仓库仅提供[文档站自动发布工作流](.github/workflows/docs-pages.yml)，启用方式见[Pages 指南](docs/pages.md)。它不构建应用镜像、不更新 NAS，也不恢复应用检查任务。

代码按 [MIT](LICENSE) 许可证分发。感谢 [tg-bot-video-download](https://github.com/welkin621/tg-bot-video-download) 提供的参考思路。
