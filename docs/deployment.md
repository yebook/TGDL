# 飞牛 OS / Docker 部署

标准部署只使用项目根目录的 **`compose.yaml`**。应用、本地 Bot API 管理组件和网络组件一起部署；之后在 WebUI 配置代理、填写 Telegram 凭据和切换 API，无需更换 Compose 文件。

当前仍处于首版开发阶段，采用源码构建，尚未提供公开应用镜像。真实大文件、飞牛 OS 和内置代理的验收边界见 [验证记录](verification.md)。

## 部署文件在哪里

需要完整项目源码，并在项目根目录执行命令。不能只上传 YAML 文件。

| 文件 | 用途 |
| --- | --- |
| [compose.yaml](https://github.com/yebook/TGDL/blob/main/compose.yaml) | 唯一标准部署入口，包含直连与代理能力 |
| [.env.example](https://github.com/yebook/TGDL/blob/main/.env.example) | 下载位置、UID/GID、WebUI 地址等部署参数 |
| `Dockerfile`、`backend/`、`frontend/`、`deploy/` | 镜像构建需要的源码与配置 |

不要复制其他实例的 `.env`、密钥或媒体到公开目录。固定源码与基础镜像来源见 [构建来源](https://github.com/yebook/TGDL/blob/main/deploy/sources.md)。

## 准备

- Docker Compose v2 和 Linux 容器环境。Windows 本机验证使用 Docker Desktop，见 [本地运行](local-run.md)。
- 标准栈的网络组件需要 `/dev/net/tun`、网络管理权限及 iptables。它只管理本项目 API 的容器网络，不修改宿主机代理设置。宿主不支持这些能力时，标准栈不能完整启动；具体飞牛 OS 机型仍需实测。
- 专属 Bot Token 和自己的数字 Telegram 用户 ID。先用官方云端不需要 API ID / Hash；本地大文件模式才需要 [申请应用凭据](telegram-api.md)。
- 应用与 API 使用非 root UID/GID，一个管理员、一个 Bot、单实例。媒体共享先挂载，并允许该用户写入。
- 应用数据库放在可靠本地文件系统，不放在 SMB/NFS 上。API 缓存和最终下载会各保存媒体，预留至少两份大文件及并发临时空间。

首次构建需要访问 Docker Hub、GitHub、Debian、PyPI、npm 和 Go 模块源；官方 API 编译可能耗时较长并占用数 GB 内存。

## 目录和参数

在项目根目录执行，按实际用户替换示例 `1000:1000`：

```sh
cp .env.example .env
id
mkdir -p data bot-api downloads
sudo chown 1000:1000 data bot-api downloads
sudo chmod 750 data bot-api downloads
chmod 600 .env
```

这些命令只设置项目顶层目录，不递归修改已有媒体库。已有 NAS 共享的 ACL 也必须允许该 UID/GID 写入。API 控制信息和网络配置使用私有 Docker 卷，初始化服务负责准备权限，不需要另建控制目录。

主要参数：

```dotenv
TGD_HOST_DATA=./data
TGD_HOST_DOWNLOADS=./downloads
TGD_HOST_API_CACHE=./bot-api
TGD_UID=1000
TGD_GID=1000
TGD_BIND_IP=127.0.0.1
TGD_WEB_PORT=8000
TGD_TIMEZONE=Asia/Shanghai
```

| 参数 | 说明 |
| --- | --- |
| `TGD_HOST_DATA` | 数据库、加密密钥、初始化密钥和持久日志 |
| `TGD_HOST_DOWNLOADS` | 最终媒体根目录；可改为已挂载的 NAS 共享目录 |
| `TGD_HOST_API_CACHE` | 官方 API 数据及媒体缓存，API 可写、应用只读 |
| `TGD_UID` / `TGD_GID` | 应用与 API 使用的非 root 数字用户和组 |
| `TGD_BIND_IP` / `TGD_WEB_PORT` | 默认只允许本机访问；NAS 内网访问改为 NAS 的固定局域网 IP |
| `TGD_TIMEZONE` | 应用默认时区，之后可在 WebUI 修改 |
| `TGD_NETWORK_SUBNET` | 内部子网；勿与 LAN、VPN、Docker 或 TUN 网段重叠 |

相对挂载路径以 Compose 文件所在目录为基准。WebUI 的下载根目录对应容器中的 `/downloads`；宿主实际位置在 `.env` 配置，页面不输入 NAS 宿主路径。启动前确认媒体共享已挂载，避免 Docker 在缺失路径下创建空目录。

Bot Token、用户 ID、API ID / Hash 和代理均在 WebUI 配置。凭据不要写进准备分享的 YAML。

## 构建并启动

```sh
docker compose --env-file .env -f compose.yaml config --quiet
docker compose --env-file .env -f compose.yaml up -d --build
docker compose --env-file .env -f compose.yaml ps
```

`config --quiet` 只验证配置，不打印展开后的内容。初始化服务正常完成后退出是预期行为；其他组件常驻。启动成功不表示 Telegram 已连接。

访问 `http://127.0.0.1:8000`，或已配置的 NAS 局域网地址。在数据目录读取 `bootstrap.key`（默认 `data/bootstrap.key`），创建管理员账号和 6–20 位密码。之后使用管理员密码登录。

默认选择官方云端。在 WebUI 保存 Bot Token 和用户 ID 白名单，按需启用代理。保存只检查格式，不要求在线；在系统状态查看实际连接，必要时手动校验。先给 Bot 发送小于 20 MB 的图片或视频验证保存结果。

仅 WebUI 端口对宿主开放，本地 API 的 8081 不对外发布。内网 HTTP 适用于可信网络，HTTPS 可由自己的反向代理终止；不要将 WebUI 或本地 API 直接暴露到公网。

## 后续接入本地 API

1. 按 [申请步骤](telegram-api.md#获取-api-id-api-hash) 获取 API ID / Hash。
2. 在 WebUI 填写凭据，选择本地模式并保存；如需代理，在同一设置页配置。
3. 后台等待活动任务和在途请求，应用目标网络，退出云端，启动本地进程并验证 Bot。
4. 查看已保存模式、实际模式、切换步骤和 Bot 连接；实际连接成功后再测试超过 20 MB 的视频。

日常无需停止应用、重启容器或手动执行 `logOut`。修改凭据可重启容器内 API 进程；WebUI 的“重启 API 进程”不代表重建容器。管理组件失联、宿主网络能力缺失或容器自身停止时仍需 Docker 排查。

云端退出结果不确定时系统保持暂停，需按状态提示核实后继续；不会反复退出或自动回连云端。云端退出成功后可立即登录本地，返回云端需等待至少 10 分钟。完整流程见 [API 接入](telegram-api.md)。

## 本地 API 的代理：三个容器

常驻服务为应用、本地 API 管理组件、网络组件；另有一次性权限初始化服务。网络组件与 API 共享网络命名空间，内置 sing-box TUN，应用通过内部地址访问 API。

- **关闭代理**：后台确认直连配置后使用宿主的 IPv4 网络出口；本地 API 网络暂不启用 IPv6。
- **启用代理**：在 WebUI 选择 HTTP CONNECT 或 SOCKS5，填写地址、端口和可选认证。云端请求由应用使用代理，本地 API 的 Telegram 连接由网络组件接管。
- **切换中**：页面显示等待应用的状态；保存成功不代表网络已生效或 Bot 已连接。
- **失败**：显示安全错误原因。启用代理失败不会自动回退直连；排查后按页面提示重试。

代理地址必须能由容器访问。宿主代理可填写 `host.docker.internal` 或可达的 LAN 地址，并允许 Docker 网络访问该端口；容器中的 `127.0.0.1` 指向容器自己。使用内置网络组件时无需另外开启宿主代理软件的 TUN，只需代理软件提供可达的 HTTP CONNECT / SOCKS5 服务。

系统代理、浏览器代理或 `HTTP_PROXY` 变量不会自动覆盖官方 API 的底层 Telegram 连接；官方 `--proxy` 参数用于 webhook，不能代替此网络配置。

## 飞牛 OS 图形界面

1. 将完整源码放到 NAS 项目目录，按上文准备 `.env` 和业务目录。
2. 在 Docker 中新建 Compose 项目，工作目录选择源码根目录，使用 `compose.yaml`。
3. 确认环境文件、TUN 设备和网络权限可用，执行构建和启动。
4. 打开 WebUI 完成初始化，后续只在设置页管理代理与 API。

若图形界面无法设置构建上下文、设备权限或 `.env`，使用 SSH 执行上述命令。飞牛 OS 各版本界面与机型支持仍需实机确认。

## 日常命令

```sh
docker compose --env-file .env -f compose.yaml ps
docker compose --env-file .env -f compose.yaml logs --tail 100 app
docker compose --env-file .env -f compose.yaml stop
docker compose --env-file .env -f compose.yaml start
```

容器维护前等待活动任务结束并备份。若重建网络组件，需要同时重建共享其网络的 API 组件；普通 WebUI 代理开关无需此操作。不要用 `down -v` 清理部署，它会删除私有持久卷。备份范围和故障处理见 [维护指南](operations.md)。
