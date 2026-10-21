# 快速开始

准备支持 Linux 容器和 TUN 的 Docker Compose v2 主机、完整项目源码、专属 Bot Token 和自己的数字 Telegram 用户 ID。先使用云端模式，无需申请 API ID / Hash。

## 1. 准备部署

在项目根目录将 `.env.example` 复制为 `.env`，配置持久目录、非 root UID/GID、WebUI 绑定地址。按 [部署指南](deployment.md#目录和参数) 创建目录并授权。NAS 媒体共享必须先挂载好；不要只上传 Compose 文件。

唯一的 `compose.yaml` 已包含应用、本地 API 管理组件和网络组件，并自动初始化私有控制卷；没有应用凭据时 API 进程等待，不妨碍云端使用。直连和代理都在同一 WebUI 切换，无需更换部署文件。

## 2. 启动与初始化

在项目根目录运行：

```sh
docker compose --env-file .env -f compose.yaml config --quiet
docker compose --env-file .env -f compose.yaml up -d --build
docker compose --env-file .env -f compose.yaml ps
```

浏览器访问部署主机地址与端口。默认绑定 `127.0.0.1:8000` 仅本机可见；内网访问要先配置 NAS 的局域网 IP。从数据目录的 `bootstrap.key` 读取一次性初始化密钥，设置管理员账号和 6–20 位密码。保管密钥，不要上传或发送到聊天。

## 3. 保存配置并验证

在「设置」中选择官方云端，填写 Bot Token 与自己的用户 ID 白名单，保存。保存只检查格式，不要求在线；需要时点击「校验配置」，并到「系统状态」查看实际连接。容器里的 `127.0.0.1` 不是宿主机，代理填写方法见 [部署指南](deployment.md)。

把一个小于 20 MB 的图片或视频发送/转发给 Bot，检查确认卡片，点击「确认下载」。在任务页查看进度，检查本地文件和 Bot 结果。成功后再尝试同组多文件；超过 20 MB 的视频需先 [接入本地 API](telegram-api.md)。

## 接下来

- [任务与归档](usage.md)：确认有效期、目录结构、取消、重试与删除记录。
- [模板设置](templates.md)：定制 Bot 确认文案和文件名。
- [维护与诊断](operations.md)：日志默认保留 7 天、手动清理、请求保护与备份。
