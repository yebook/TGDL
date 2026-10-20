# Windows 本地接入 Bot 验证

本文针对当前电脑已准备好的专用环境。路径均相对于项目根目录，PowerShell 命令也在根目录执行。Docker Desktop 需运行 Linux 容器。新电脑或飞牛 OS 请直接使用 [部署指南](deployment.md)，不要复制当前电脑的 `.runtime/`、密钥或测试数据。

## 配置文件位置与适用范围

| 文件 | 用途 |
| --- | --- |
| `compose.yaml` | 新版标准栈：应用 + 常驻 API 管理组件 |
| `compose.proxy.yaml` | 本地 API 需要 TUN 代理时独立使用，不叠加默认文件 |
| `.runtime/local-docker/compose.web.yaml` | 当前电脑原 WebUI 栈，已原地升级为应用 + API 管理组件；不随源码分发 |
| `.runtime/local-docker/.env` | 当前电脑持久化挂载参数，不分享或复制到 NAS |

## 1. 先使用云端 API

云端只需在 WebUI 填写 Bot Token、数字 Telegram 用户 ID 白名单，无需 API ID / Hash。仅支持下载不超过 20 MB 的单文件。旧临时栈的管理命令为：

```powershell
docker compose --env-file .runtime/local-docker/.env -f .runtime/local-docker/compose.web.yaml config --quiet
docker compose --env-file .runtime/local-docker/.env -f .runtime/local-docker/compose.web.yaml up -d --build
```

访问 <http://127.0.0.1:8000>。首次在本机打开 `.runtime/local-docker/data/bootstrap.key` 完成管理员初始化；已初始化直接登录。保存配置不要求在线验证，随后可手动校验并查看 Bot 状态。尚未升级的旧临时栈会显示外部管理/未部署，无法通过按钮启动缺少的容器。

当前电脑已保留原项目名和原数据挂载，新增的私有控制目录使用 Docker Linux 数据卷 `tgd-local-web_api-control`（容器内 `/run/tgd-api-control`，UID/GID 与应用一致，权限 700）。Windows 磁盘绑定目录可能显示为 root/777，不符合私有控制文件要求，不能通过放宽应用检查解决。不要执行 `down -v` 删除控制卷；备份时与原应用数据一并保存。NAS 的 Linux 绑定目录按部署指南设置权限即可。

## 2. 填写本地 API 凭据

按 [API ID / Hash 申请步骤](telegram-api.md#获取-api-id--api-hash) 获取应用凭据。升级新版管理栈后直接在 WebUI 填写；Hash 留空保留已存值，显式清除才删除。旧 `.env` 凭据可作兼容来源，优先使用 WebUI。不要把凭据发到聊天或问题报告。

使用专用 Bot，避免其他程序同时接收更新。本项目只能协调自己的消费者，其他服务及旧 webhook 需先排查。云端不需要应用凭据，管理组件无凭据时保持等待。

## 3. 切换到完整下载服务

当前电脑已原地升级时无需执行下面的项目迁移，继续使用上面的临时栈命令和 WebUI 即可。以下步骤供尚未升级的旧临时栈参考；Windows 控制目录必须位于支持 Linux 权限的文件系统或数据卷，不能直接使用显示为 root/777 的 Windows 绑定目录。

这是旧临时栈的一次性部署升级；完成后日常修改凭据、切换模式和重启 API 子进程均在 WebUI 操作。旧项目为 `tgd-local-web`，根目录完整项目为 `tgd`，不能同时写数据库或占用 8000。沿用原数据，不需要重建管理员。

1. 检查 `.runtime/local-docker/.env` 中原有挂载。相对路径以 **Compose 文件所在目录** 为基准，根目录 Compose 必须指向原目录，例如 `./.runtime/local-docker/data`，不要误用 `./data`。保留原 `data`、`downloads`、`bot-api`、`network`；新增 `TGD_HOST_API_CONTROL=./.runtime/local-docker/api-control`，创建该私有目录。Linux/NAS 需按部署指南授权。
2. 先校验和构建。管理组件常驻不代表 API 子进程已启动，也不代表 Bot 已连接：

```powershell
docker compose --env-file .runtime/local-docker/.env -f compose.yaml config --quiet
docker compose --env-file .runtime/local-docker/.env -f compose.yaml build app bot-api
```

3. 首次编译官方 API 可能较久。构建完成后，暂停发新媒体、等待活动任务结束并备份应用数据；保持原模式，不提前手工退出云端。停止旧项目，命令不删除绑定目录：

```powershell
docker compose --env-file .runtime/local-docker/.env -f .runtime/local-docker/compose.web.yaml down
```

4. 停止其他使用同一 Bot 的程序，然后启动新版栈：

```powershell
docker compose --env-file .runtime/local-docker/.env -f compose.yaml up -d
docker compose --env-file .runtime/local-docker/.env -f compose.yaml ps
```

5. 打开原 WebUI，确认原账号、任务、设置保留，查看管理组件状态。在设置填写 API ID / Hash，选择本地并保存，等待切换和身份验证结果。后台负责暂停、任务排空、云端退出和 API 启动，不要同时运行旧 `logOut` 脚本。若意外出现初始化界面，先停服务检查数据挂载，不重新初始化。

Bot Token 和白名单保持原配置。默认下载根目录对应 `.runtime/local-docker/downloads`，WebUI 不填宿主机路径。默认确认 30 分钟、并发 3、额外重试 3 次。之后不再启动旧临时项目。

旧设置若已经选择本地，首次启用管理会显示实际模式未确认并等待用户操作，不会自动退出 Bot；确认凭据和其他消费者后点击“重试操作”再启动协调。若本机临时 Compose 已升级为管理栈且页面显示管理组件在线，无需再迁移到另一个项目，继续使用当前 Compose 即可。

## 4. 网络和验证顺序

Windows 浏览器能访问 Telegram 不代表 Docker 可达。云端在 WebUI 配置 HTTP CONNECT / SOCKS5；本地 API 若需要代理，应在升级时选独立 `compose.proxy.yaml` 并确认 TUN 支持。命令中的文件名相应替换，原挂载保持不变。网络拓扑变更仍需部署操作，进程按钮不能添加 TUN 或修改容器网络。

宿主机代理可使用 `host.docker.internal` 或容器可达的 LAN 地址，不能填容器里的 `127.0.0.1`。完整说明见 [部署指南](deployment.md#本地-api-的代理三个容器)。

验证顺序：

1. 检查 API 进程、Bot 连接、代理和存储状态，确认显示的实际模式正确。
2. 发小图片/视频，检查确认卡、完整任务编号、下载文件与反馈。
3. 本地模式验证超过 20 MB 的视频，再验证混合媒体组同目录。
4. 验证去重、取消、重试、API 手动重启和应用重启恢复。

以上 Telegram 操作由你发起。文本消息链接不在首版范围。容器/进程运行不能代替真实下载验收，结果记录到 [验证记录](verification.md)。

## 5. 停止与重启

日常 API 故障先在 WebUI 查看原因，使用重试或重启 API 进程；有任务时等待安全边界，应用和容器持续运行。管理组件/容器自身失联则需部署排查。整栈维护命令为：

```powershell
docker compose --env-file .runtime/local-docker/.env -f compose.yaml ps
docker compose --env-file .runtime/local-docker/.env -f compose.yaml logs --tail=100 app bot-api
docker compose --env-file .runtime/local-docker/.env -f compose.yaml stop
docker compose --env-file .runtime/local-docker/.env -f compose.yaml up -d
```

分享日志前脱敏；不要输出展开的 Compose 配置。停止不删除持久化目录，API 控制目录也包含需保护的密钥。旧 `--profile local-api` 命令仍兼容，新版不需要它。若仍使用旧临时栈，管理命令继续使用其 `.runtime/local-docker/compose.web.yaml`。
