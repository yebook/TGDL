# Windows 本地运行

适用于 Windows 上使用 Docker Desktop（Linux 容器）的本地验证，不依赖任何预先创建的私有 Compose 文件。所有命令在完整项目根目录执行。

## 1. 先使用云端 API

启动 Docker Engine，用 PowerShell 复制配置：

```powershell
Copy-Item .env.example .env
New-Item -ItemType Directory -Force data, downloads, bot-api, api-control, network
```

用编辑器修改 `.env`，保持目录为项目相对路径，默认监听 `127.0.0.1:8000`。API ID / Hash 可以留空。Windows bind mount 的权限映射不同于 Linux UID/GID；应用要求私有控制目录，若遇到权限错误，应使用满足 POSIX 权限的 Linux 文件系统或私有 Docker 数据卷，不应改用 root 或开放目录权限。

按 [部署指南](deployment.md) 执行 `config --quiet` 与 `up -d --build`。在数据目录读取 `bootstrap.key` 初始化管理员，再在 WebUI 保存 Bot Token、自己的用户 ID，使用云端测试小文件。配置只保存在本机，不提交 `.env` 或运行数据。

## 2. 接入本地 API

标准 Compose 已包含管理组件；在 WebUI 填写 API ID / Hash、选择本地并保存，按 [接入指南](telegram-api.md) 观察状态。不要同时启动第二套应用抢占同一个 Bot 或数据目录。

云端需要代理时可使用 Docker 可达的 `host.docker.internal` 和代理端口，不能填容器内的 `127.0.0.1`。本地 API 的 TUN 支持取决于 Docker Desktop Linux 环境，不能因为云端代理成功就认定本地路由也验证通过。

## 切换到完整下载服务

已有旧部署时，先等待任务结束，停服并备份数据库、密钥、配置、媒体、缓存和控制目录。将根目录 Compose 的持久化挂载指向原数据，不启动两套服务。参见 [升级指南](deployment.md#升级到-webui-管理本地-api)。

本地开发源码启动和自动测试见 [开发指南](development.md)，无凭据容器检查见 [Docker 隔离验收](docker-acceptance.md)。本机测试不代替飞牛 OS 或真实 Telegram 验收。
