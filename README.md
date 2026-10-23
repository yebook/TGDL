# TGDL · Telegram 媒体下载助手

把发送或转发给专属 Telegram Bot 的视频、图片，经确认后保存到本地或 NAS。通过中文 WebUI 配置 Bot、管理下载和维护服务，适合个人内网自托管。

**[在线文档](https://yebook.github.io/TGDL/)** · [第一次下载](https://yebook.github.io/TGDL/quick-start.html) · [更新日志](https://yebook.github.io/TGDL/changelog.html) · [问题反馈](https://github.com/yebook/TGDL/issues)

目前尚未正式发布；[当前版本与更新日志](https://yebook.github.io/TGDL/changelog.html) 可在文档站查看。默认部署文件已改为拉取配套生产镜像，公开镜像尚待首次发布；**目前可用的启动方式仍是下方源码构建**。真实 Telegram 大文件、Docker 宿主机和代理完整链路的验收范围见 [验证说明](https://yebook.github.io/TGDL/verification.html)。

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
- **统一连接配置**：WebUI 切换云端与本地 API，支持 HTTP CONNECT / SOCKS5 代理。

## 选哪种 API

| 模式 | 适用场景 | 需要 |
| --- | --- | --- |
| 官方云端（默认） | 先开始使用，单文件不超过 **20 MB** | Bot Token |
| 本地 Bot API | 下载超过 **20 MB** 的文件 | Bot Token、API ID 和 API Hash |

两种模式均需配置允许使用的数字 Telegram 用户 ID。标准部署已包含本地 API 管理服务，之后在 WebUI 填写应用凭据、切换模式即可，日常无需重启容器。详见 [大文件与 API 配置](https://yebook.github.io/TGDL/telegram-api.html)。

## 快速部署

需要完整项目源码、Docker Compose v2、`linux/amd64` 容器环境、专属 Bot Token 和自己的数字 Telegram 用户 ID。当前源码构建与预编译镜像均仅支持 amd64（x86_64）。源码获取与 Bot 准备方法见 [部署前准备](https://yebook.github.io/TGDL/deployment.html#部署前准备)。数据库使用可靠本地存储，媒体共享先挂载。

### 1. 准备配置与目录

源码构建与镜像部署共用根目录 [compose.yaml](compose.yaml)，环境参数模板是 [.env.example](.env.example)。以下为 Linux / SSH 命令，在完整项目根目录执行。将示例 `1000:1000` 替换为有目录权限的用户与组，并在 `.env` 中填写相同的 `TGD_UID` / `TGD_GID`。推荐使用普通用户；也支持 `0:0` 或单独将 UID、GID 设为 `0`，但目录权限仍需匹配。

```sh
cp .env.example .env
id
mkdir -p data bot-api downloads
sudo chown 1000:1000 data bot-api downloads
sudo chmod 750 data bot-api downloads
chmod 600 .env
```

编辑 `.env`，设置持久目录、UID/GID 和 WebUI 端口；完整的注释版 `.env` 与 `compose.yaml` 见 [参数设置](https://yebook.github.io/TGDL/deployment.html#参数设置)。修改宿主目录后，对实际目录创建并授权；已有共享还需允许该用户读写，这些命令不会递归修改已有媒体库。Windows 本机操作见 [本地运行](https://yebook.github.io/TGDL/local-run.html)。

启动后使用 `http://部署主机IP:8000` 访问，本机也可打开 `http://localhost:8000`。若修改 `TGD_WEB_PORT`，使用对应端口即可，无需另外配置绑定地址。

### 2. 构建并启动

确认路径、权限与共享挂载后，在项目根目录执行：

```sh
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

源码附带 `linux/amd64` 的 Bot API／TDLib 预编译压缩包，默认直接校验并使用，不在 NAS 编译 TDLib；仍需下载基础镜像、应用依赖并构建前端。之后只修改 `.env` 时，执行 `docker compose up -d --no-build` 即可重建容器，无需再次构建。`init-volumes` 初始化成功后退出是正常现象。配套镜像发布后可按 [镜像部署](https://yebook.github.io/TGDL/deployment.html#镜像部署) 连应用构建也省去；预编译产物更新与手动编译入口见 [构建指南](https://yebook.github.io/TGDL/development.html#原生组件单独预编译)。

### 3. 初始化并验证

打开 WebUI，从应用数据目录读取 `bootstrap.key`（默认 `data/bootstrap.key`），创建管理员账号和 **6–20 位密码**。初始化密钥仅用于首次设置，不要分享。

进入「设置 → 连接 Telegram」，先选择官方云端，填入 Bot Token 和自己的用户 ID 并保存；不能直连 Telegram 时，在这里配置容器可访问的代理。保存无需在线校验，可手动点击「校验配置」，在顶栏及连接设置中查看实际状态。

向 Bot 发送 `/start` 检查回复，再发送小于 20 MB 的图片或视频，点击「确认下载」，检查保存结果与 Bot 反馈。本地模式显示「本地 API 可用」仅表示服务可响应，仍需实际收发消息确认连接。完整操作见 [第一次下载](https://yebook.github.io/TGDL/quick-start.html)。

## 使用指南

| 想做什么 | 文档 |
| --- | --- |
| 完成安装与第一次下载 | [Docker](https://yebook.github.io/TGDL/deployment.html) · [首次使用](https://yebook.github.io/TGDL/quick-start.html) |
| 修改保存、连接、日志等选项 | [设置怎么用](https://yebook.github.io/TGDL/settings.html) |
| 下载大文件、获取应用凭据、配置代理 | [本地 API 与网络](https://yebook.github.io/TGDL/telegram-api.html) |
| 查看进度、管理任务和目录 | [任务与归档](https://yebook.github.io/TGDL/usage.html) · [提示与命名](https://yebook.github.io/TGDL/templates.html) |
| 迁移配置、处理故障 | [配置备份](https://yebook.github.io/TGDL/configuration-backup.html) · [维护](https://yebook.github.io/TGDL/operations.html) · [常见问题](https://yebook.github.io/TGDL/faq.html) |

## 参与与安全

欢迎通过 [Issues](https://github.com/yebook/TGDL/issues) 反馈问题或提交 Pull Request；开发与检查命令见 [开发指南](https://yebook.github.io/TGDL/development.html)，提交约定见 [贡献指南](https://yebook.github.io/TGDL/project.html#参与贡献)。文档站的发布只更新静态文档，不部署应用。

报告问题请提供版本、部署方式、复现步骤及脱敏错误码，不要发送 Bot Token、API Hash、初始化密钥、数据库或真实媒体。安全问题按 [安全报告指引](https://yebook.github.io/TGDL/project.html#安全报告) 处理。只保存自己有权处理的媒体；项目的节流、等待与认证暂停用于减少无效调用，无法保证 Telegram 不限制 Bot 或关联账号。

## 许可与致谢

采用 [MIT 许可证](https://yebook.github.io/TGDL/project.html#许可)。第三方组件适用各自许可证，见 [构建来源](https://yebook.github.io/TGDL/project.html#构建来源与第三方组件)。感谢 [tg-bot-video-download](https://github.com/welkin621/tg-bot-video-download) 提供参考思路。
