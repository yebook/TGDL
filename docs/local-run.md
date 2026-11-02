# Windows 本地运行

在 Windows 的 Docker Desktop 中使用 Linux 容器运行 TGDL。需要 x86_64 主机，所有命令在完整项目根目录执行。

## 1. 先使用云端 API

启动 Docker Engine，用 PowerShell 复制配置：

```powershell
Copy-Item .env.example .env
New-Item -ItemType Directory -Force data, downloads, bot-api
```

用编辑器修改 `.env`，保持目录为项目相对路径，默认发布 WebUI 端口 `8000`。本机访问 `http://localhost:8000`，其他设备使用 `http://部署主机IP:8000`；修改 `TGD_WEB_PORT` 时使用对应端口。Telegram 凭据稍后在 WebUI 填写。私有控制目录使用 Docker 命名卷并自动初始化，无需在 Windows 手动设置 POSIX 权限；无需 TUN 设备。

在完整源码根目录执行 `docker compose up -d --build`。打开 WebUI 前，用 `Get-Content data/bootstrap.key` 读取初始化密钥，创建管理员后保存 Bot Token 和自己的用户 ID，即可按 [第一次下载](quick-start.md) 开始使用。只修改 `.env` 时，执行 `docker compose up -d --no-build` 重新创建容器即可。请妥善保存 `.env` 和运行数据。

## 2. 接入本地 API

标准 Compose 已包含管理组件；在 WebUI 填写 API ID / Hash、选择本地并保存，按 [接入指南](telegram-api.md) 观察状态。不要同时启动第二套应用抢占同一个 Bot 或数据目录。

需要代理时，在同一 WebUI 填写 Docker 可达的 `host.docker.internal` 和代理端口，不能填容器内的 `127.0.0.1`。两种 API 均使用所配代理，关闭则使用容器默认出口，不会自动读取系统/浏览器代理。无需更换 Compose 或另外开启宿主软件 TUN。两种模式仍需各自确认实际 Bot 连接。

如果无法打开页面或连接 Bot，请参阅 [常见问题](faq.md) 和 [网络排障](operations.md#网络连通诊断)。
