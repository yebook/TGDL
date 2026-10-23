# Windows 本地运行

适用于 Windows 上使用 Docker Desktop（Linux 容器）的本地验证，不依赖任何预先创建的私有 Compose 文件。所有命令在完整项目根目录执行。

## 1. 先使用云端 API

启动 Docker Engine，用 PowerShell 复制配置：

```powershell
Copy-Item .env.example .env
New-Item -ItemType Directory -Force data, downloads, bot-api
```

用编辑器修改 `.env`，保持目录为项目相对路径，默认发布 WebUI 端口 `8000`。本机访问 `http://localhost:8000`，其他设备使用 `http://部署主机IP:8000`；修改 `TGD_WEB_PORT` 时使用对应端口。Telegram 凭据稍后在 WebUI 填写。私有控制目录使用 Docker 命名卷并自动初始化，无需在 Windows 手动设置 POSIX 权限；无需 TUN 设备。

按 [部署指南](deployment.md) 选择启动方式：在完整源码根目录使用 `docker compose up -d --build`，远程应用/API 镜像部署暂时关闭。在数据目录读取 `bootstrap.key` 初始化管理员，再在 WebUI 保存 Bot Token、自己的用户 ID，使用云端测试小文件。只修改 `.env` 时重新创建容器，无需重新编译。配置只保存在本机，不提交 `.env` 或运行数据。

## 2. 接入本地 API

标准 Compose 已包含管理组件；在 WebUI 填写 API ID / Hash、选择本地并保存，按 [接入指南](telegram-api.md) 观察状态。不要同时启动第二套应用抢占同一个 Bot 或数据目录。

需要代理时，在同一 WebUI 填写 Docker 可达的 `host.docker.internal` 和代理端口，不能填容器内的 `127.0.0.1`。两种 API 均使用所配代理，关闭则使用容器默认出口，不会自动读取系统/浏览器代理。无需更换 Compose 或另外开启宿主软件 TUN。两种模式仍需各自确认实际 Bot 连接。

本地开发源码启动和自动测试见 [开发指南](development.md)，无凭据容器检查见 [Docker 隔离验收](docker-acceptance.md)。本机测试不代替 Docker 宿主机或真实 Telegram 验收。
