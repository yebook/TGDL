# 飞牛 OS / Docker 部署

当前是首版实现与验证阶段。两份 Compose 已通过静态解析，应用镜像构建及独立容器的初始化、配置持久化、停服备份恢复已在 Windows Docker Desktop 验证。完整 Bot API 栈、真实 Telegram 大视频、代理 TUN 和飞牛 OS 实机尚未验收，详见 [验证记录](verification.md) 和 [Docker 隔离验收](docker-acceptance.md)。

## 部署文件在哪里

部署文件位于**项目根目录**，不是 `docs/` 或 `deploy/` 目录。本文的项目文件路径均相对于项目根目录，命令也在该目录执行；不要求项目放在特定磁盘或目录。

| 文件 | 相对项目根目录的位置 | 用途 |
| --- | --- | --- |
| [compose.yaml](https://github.com/yebook/TGDL/blob/main/compose.yaml) | `compose.yaml` | 应用 + 常驻本地 API 管理组件；默认云端，本地进程按 WebUI 配置启动 |
| [compose.proxy.yaml](https://github.com/yebook/TGDL/blob/main/compose.proxy.yaml) | `compose.proxy.yaml` | 代理：增加 TUN 网络组件，完整独立文件，不叠加默认文件 |
| [.env.example](https://github.com/yebook/TGDL/blob/main/.env.example) | `.env.example` | 参数模板，复制为同目录的 `.env` 后修改 |
| [Dockerfile](https://github.com/yebook/TGDL/blob/main/Dockerfile) 与 `deploy/` | 项目根目录及其子目录 | 从源码构建所需文件，不是单独的部署入口 |

目前提供源码构建部署，`tgd-app:0.1.0` 等名称是本地构建标签，不表示有公开镜像可拉取。两份 Compose 均以项目根目录为构建上下文，因此**需要复制整个项目**，保留以下结构与隐藏文件；不能仅复制 Compose 文件或将它粘贴到没有源码的空目录中。

```text
tgd/                       ← NAS 上的项目目录
├── compose.yaml
├── compose.proxy.yaml
├── .env.example
├── .env                   ← 在 NAS 创建，填写自己的参数
├── .dockerignore
├── Dockerfile
├── backend/               ← 包含 pyproject.toml、uv.lock、app、migrations 等
├── frontend/              ← 包含 package.json、pnpm-lock.yaml、源码等
└── deploy/                ← 应用入口、Bot API 和网络组件构建文件等
```

复制的是完整源码，以上只列出部署相关部分；不要携带其他部署的凭据、虚拟环境、`node_modules/` 或测试数据到 NAS。已有真实数据迁移请按 [维护指南](operations.md) 操作。

## 准备

- 飞牛 OS 的 Docker / Compose v2，或其他 Linux Docker 主机。首版为单实例，一个管理员、一个 Bot；不运行多个 Uvicorn worker。
- 通过 BotFather 创建专用测试 Bot。官方云端仅需 Bot Token，无需创建 Telegram 应用。本地 Bot API 才需通过 <https://my.telegram.org> 获取 API ID 与 API Hash，不需要个人 Telegram 账号常驻登录。
- 确定 NAS 用户的数字 UID/GID（例如 SSH 中执行 `id`），必须为非 root。确保相应用户可读写媒体目录。
- 四个持久化目录：应用数据、本地 API 缓存、私有 API 控制目录、媒体下载。代理模式另加网络配置目录。数据库应位于 NAS 本地可靠文件系统，不放在 SMB/NFS 上；远程媒体存储由宿主机先挂载到固定路径。
- API 缓存和下载目录都会存放完整媒体，预算至少两份大文件容量，并给并发、临时文件和未知大小文件预留余量。不要将 API 缓存作为最终媒体目录。

镜像从固定的官方源码提交构建，源代码和基础镜像摘要见 [构建来源](https://github.com/yebook/TGDL/blob/main/deploy/sources.md)。提供 amd64/arm64 基础镜像并不代表对应 NAS 架构已验证。构建需要访问 Docker Hub、GitHub、Debian 软件源、PyPI、npm 和 Go 模块源，首次编译本地 API 可能耗时且占用数 GB 内存。

## 目录和参数

先把完整源码复制到自己选择的 NAS 项目目录，再通过 SSH 或终端进入该目录。下面默认将持久化目录建在项目根目录下，可直接配合 `.env.example` 的相对路径使用：

```sh
cp .env.example .env
id
mkdir -p data bot-api api-control network downloads
sudo chown 1000:1000 data bot-api api-control network downloads
sudo chmod 750 data bot-api downloads
sudo chmod 700 api-control
sudo chmod 770 network
chmod 600 .env
```

将示例 UID/GID `1000:1000` 改为 `id` 查到的实际非 root 用户数值，再执行授权命令。不要对已有媒体库递归修改权限；只为本项目目录授权。NAS 的共享文件夹 ACL 也必须允许该 UID/GID 写入。应用不会自动以 root 修改宿主机媒体权限。

用编辑器修改 `.env`，直连模式的主要参数例如：

```dotenv
TGD_HOST_DATA=./data
TGD_HOST_DOWNLOADS=./downloads
TGD_HOST_API_CACHE=./bot-api
TGD_HOST_NETWORK=./network
TGD_UID=1000
TGD_GID=1000
TGD_BIND_IP=192.168.1.10
TGD_WEB_PORT=8000
TGD_TIMEZONE=Asia/Shanghai
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
```

相对挂载路径以 Compose 文件所在目录为基准，不以 `.env` 所在目录为基准。上述默认路径可直接使用；若媒体要放入已有 NAS 共享文件夹，将 `TGD_HOST_DOWNLOADS` 改为该共享文件夹实际宿主机路径，并检查挂载和授权。容器内路径保持不变。用户数值和 `192.168.1.10` 要换成自己的实际值。API 环境字段可留空，管理组件保持等待，后续在 WebUI 填写真实凭据。仅静态解析通过不表示凭据有效。TUN 代理模式另按需修改 `TGD_NETWORK_SUBNET`，避免网段冲突。

| 信息 | 从哪里获取 / 在哪里填写 |
| --- | --- |
| API ID、API Hash | `my.telegram.org` 的应用信息；在 WebUI 本地 API 设置填写，见 [申请步骤](telegram-api.md#获取-api-id-api-hash) |
| Bot Token | BotFather 创建 Bot 后获得；完整栈运行后在 WebUI 设置中填写 |
| Telegram 用户 ID | 自己账号的数字用户 ID；在 WebUI 的白名单中填写，不是用户名或频道 ID |
| 初始化密钥 | 首次启动自动生成，位于 `TGD_HOST_DATA/bootstrap.key`；用于创建管理员 |
| 管理员账号、密码 | 首次打开 WebUI 时自行设置；密码 6–20 位，用于之后登录 |

应用数据、API 缓存和下载目录应由可信用户管理，避免向不可信本地用户开放写权限。应用会校验文件名、路径和已有符号链接，但当前未使用完整的目录描述符锚定遍历，无法保证防御恶意本地进程在校验与写入之间替换父目录；不要将共享目录视为抵御宿主机攻击者的安全边界。

| 参数 | 用途 |
| --- | --- |
| `TGD_HOST_DATA` | 数据库、加密密钥、初始化密钥、会话和配置，对应 `/data` |
| `TGD_HOST_DOWNLOADS` | 媒体根目录，对应 `/downloads` |
| `TGD_HOST_API_CACHE` | 官方 API 数据与媒体缓存，对应 `/var/lib/telegram-bot-api`；API 可写、应用只读，同一路径 |
| `TGD_HOST_API_CONTROL` | 默认 `./api-control`，API 管理指令、状态及独立交接密钥；仅 app/API 同 UID 可访问，目录权限 700 |
| `TGD_HOST_NETWORK` | 代理模式配置交接；含代理凭据，限制访问 |
| `TGD_UID` / `TGD_GID` | 应用和 API 的非 root 身份；共享缓存必须可读 |
| `TGD_BIND_IP` / `TGD_WEB_PORT` | WebUI 绑定地址和端口，默认 `127.0.0.1:8000` |
| `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` | 可留空的旧部署兼容来源；优先在 WebUI 配置，WebUI 保存后不需重建容器 |
| `TGD_NETWORK_SUBNET` | 代理专用 Docker 子网；不得与 LAN、VPN、其他 Docker 网络或 TUN `172.29.255.0/30` 重叠 |

供局域网访问时，将 `TGD_BIND_IP` 改为 NAS 的固定局域网 IP。仅 WebUI 8000 映射到宿主机，API 8081 不对外发布。默认 HTTP 适合可信局域网；需要 HTTPS 时通过自己的反向代理终止 TLS，再配置应用安全 Cookie。不要直接在路由器上转发 WebUI 或本地 Bot API 端口。

Docker 的短格式 bind mount 在路径不存在时可能创建空目录，因此启动前必须确认媒体共享已经挂载。WebUI 设置中选择部署允许的默认下载根目录，它映射为容器内 `/downloads`；实际 NAS 路径由 `.env` 的 `TGD_HOST_DOWNLOADS` 决定，界面不输入宿主机路径。

## 默认启动：云端 API

```sh
docker compose --env-file .env -f compose.yaml config --quiet
docker compose --env-file .env -f compose.yaml up -d --build
docker compose --env-file .env -f compose.yaml ps
```

在项目根目录执行以上命令。`config --quiet` 校验 Compose 而不打印解析后的密钥；`up -d --build` 构建并在后台启动，不代表 Telegram 连接已验证。首次构建较慢，请等待结束；失败时先查看报错，不要反复重新创建 Bot 应用。

已设置 NAS 局域网绑定时，在浏览器打开 `http://NAS局域网IP:8000`，端口以 `.env` 为准。若保留 `TGD_BIND_IP=127.0.0.1`，则只能在 NAS 本机访问，电脑访问 NAS IP 会失败。

在 NAS 本机用编辑器读取 `TGD_HOST_DATA` 目录中的 `bootstrap.key`，完成一次性管理员初始化；默认配置下为项目根目录中的 `data/bootstrap.key`，容器内同一文件为 `/data/bootstrap.key`。初始化完成后不再使用该密钥，之后用管理员账号密码登录。不要把密钥或 Bot Token 发到聊天或问题报告中。

随后在 WebUI 的“Bot API 服务”选择“Telegram 官方云端”，配置 Bot Token、Telegram 用户 ID 白名单、时区及下载参数。新部署默认云端，已有部署升级保留本地，需手动切换。白名单必须是数字用户 ID，不是用户名或频道 ID。默认确认有效期 30 分钟、文件并发 3、额外自动重试 3 次。发送一个不超过 20 MB 的小文件验证；超限文件不能通过官方云端下载，未知大小仍受上限约束。

“保存设置”仅检查字段格式并持久化，不要求 Telegram 在线；“校验配置”可单独检查当前输入，不会保存，也无需先保存。云端校验使用当前输入的代理，本地校验使用已应用的 TUN 配置。结果以短暂消息提示显示。保存成功不代表 Bot 已连接，需查看系统状态；原 Bot 有活动或待确认请求时，新 Bot 身份仍会被阻止启用，需先结束原请求或恢复原 Token。

云端需要代理时，直接在同一 WebUI 填写 HTTP CONNECT / SOCKS5 地址和可选认证，无需更换 Compose 或启动 TUN。后台连接确认后才显示已应用。容器里的 `127.0.0.1` 不是宿主机，可使用 `host.docker.internal` 或容器可达的 LAN 地址，并允许 Docker 网络访问代理。

## 后续接入本地 API

按 [获取步骤](telegram-api.md#获取-api-id-api-hash) 申请凭据后，直接在 WebUI 填写 API ID / Hash，选择本地模式并保存。标准部署的管理组件已常驻，无凭据时等待；后台自动协调任务排空、退出云端、API 子进程启动与 Bot 身份验证。设置和系统页显示当前步骤、进程状态、Bot 连接与错误。应用及容器保持运行；日常无需手动执行 Compose 或 `logOut`。

使用 [接入指南](telegram-api.md#从云端切换到本地顺序很重要) 的 WebUI 流程；不要与旧版手动退出脚本同时执行。修改 WebUI 中的应用凭据会在安全边界重启 API 子进程，失败可手动重试/重启进程。外部 API 部署仍由部署者管理，界面明确显示不可控制。`--profile local-api` 旧命令可继续使用，但新版默认已启动管理组件，不再依赖该 profile。

本地 API 的 `getFile` 在本地模式返回绝对文件路径，应用验证后流式复制；不会通过云端接口绕回 20 MB 限制。

## 升级到 WebUI 管理本地 API

旧版需进行一次部署更新：等待活动任务结束，备份应用数据与密钥，停止旧栈，更新完整源码；保留原数据、缓存、下载和网络挂载。新增 `TGD_HOST_API_CONTROL`（默认项目下 `api-control`），按上方要求创建并授权，再用原来的 Compose 类型运行 `config --quiet` 和 `up -d --build`。这次升级会更新容器；升级后日常切换和修改凭据不需更新容器。

如果原来使用自定义的仅 WebUI 栈，按 [本地升级步骤](local-run.md#切换到完整下载服务) 核对持久化挂载后迁移到根目录 Compose，不能同时启动两套应用。API 控制目录包含独立密钥和加密指令，限制访问，按停服备份流程保留；不要与媒体下载目录混用，也不要把整个 app 数据目录或 Docker socket 挂给 API 服务。

首次启动后检查设置中的管理组件状态。组件失联或容器自身故障仍需 Docker 排查；WebUI 的“重启 API 进程”只重启容器内官方 API 子进程。API 进程可用、Bot 已连接及真实下载成功是三个不同的检查结果。原 Bot 被其他程序使用时，先停止其他消费者，本项目不能代管它们。

旧部署保存的模式若为本地，首次启用管理时保留该选择，但实际模式暂为未确认，暂停自动连接；不会因升级就自动退出 Bot。确认凭据、挂载与其他消费者后，在 WebUI 点击“重试操作”明确启用，后台才执行首次协调。原本云端的部署继续使用云端，无需为了升级切换模式。

## 飞牛 OS Docker Compose 界面部署

如果不习惯命令行，可使用飞牛 OS Docker 应用的 Compose 项目功能，具体按钮名称随系统版本变化：

1. 在文件管理器将**完整源码**放到自己选择的 NAS 项目目录，按上一节准备目录权限及 `.env`。
2. 新建 Compose 项目，将项目/工作目录设为该源码根目录，选择其中的 `compose.yaml`；默认已包含 API 管理组件，无需 profile。仅本地 API 的 TUN 代理才选独立的 `compose.proxy.yaml`。
3. 确认界面以该目录作为构建上下文，并加载同目录 `.env` 进行 `${变量}` 插值。某些界面需要额外导入环境变量；仅上传 YAML 或在容器环境变量栏填写，未必能用于 Compose 插值。
4. 执行构建并启动，查看项目内各服务状态与构建日志，再按上一节打开 WebUI、读取初始化密钥并配置 Bot。

如果界面无法设置源码目录、构建镜像或加载 `.env`，改用上一节的 SSH 命令启动，不要把 API 密钥直接写进准备分享的 YAML。此路径是操作指南，飞牛 OS 实机界面兼容性仍待验证。

## 查看状态、日志与停止

以下均在 NAS 项目根目录执行。代理部署将每条命令的 `compose.yaml` 改成 `compose.proxy.yaml`，不要混用：

```sh
docker compose --env-file .env -f compose.yaml ps
docker compose --env-file .env -f compose.yaml logs --tail=100 app bot-api
docker compose --env-file .env -f compose.yaml stop
docker compose --env-file .env -f compose.yaml up -d
```

按需逐条执行；`stop` 停止服务，最后一条再次启动。代理模式还可查看 `logs --tail=100 telegram-network`。分享日志前检查并遮盖密钥；不要用不带 `--quiet` 的 `config` 输出真实部署配置。停止或重建容器不会删除这些宿主机绑定目录，备份和升级另见 [维护指南](operations.md)。

新版默认 Compose 命令会同时管理应用和 API 管理组件，无需额外 profile；子进程是否运行由 WebUI 设置决定。

## 本地 API 的代理三个容器

仅本地 API 需要此网络拓扑；云端使用 WebUI 代理即可。使用完整独立的 `compose.proxy.yaml`，不要与 `compose.yaml` 合并。网络组件使用 sing-box TUN，并与 API 共享网络命名空间。应用访问内部 `http://telegram-network:8081`。API 管理组件可在无凭据时常驻，后续在 WebUI 填写凭据、配置代理并选择本地 API。

首次部署可直接启动下面的完整管理栈，再按 [WebUI 接入流程](telegram-api.md#从云端切换到本地顺序很重要) 操作。管理组件运行不等于本地 Bot 登录，模式切换由后台协调；不要再混用旧手动退出脚本。

```sh
test -c /dev/net/tun
docker compose --env-file .env -f compose.proxy.yaml config --quiet
docker compose --env-file .env -f compose.proxy.yaml up -d --build
docker compose --env-file .env -f compose.proxy.yaml ps
```

网络容器需要 `/dev/net/tun` 和限定的 `NET_ADMIN` 等权限；不使用 `privileged`、宿主机网络或 Docker socket。若飞牛 OS 不提供 TUN、IPv4/IPv6 iptables 或所需内核能力，容器不能进入可用状态；应修复宿主环境，不能将其显示为代理已生效。

在 WebUI 启用代理，填写 HTTP CONNECT 或 SOCKS5 的主机、端口及可选用户名/密码。HTTP 代理必须允许 CONNECT 隧道；不支持仅转发普通 HTTP 的服务，也不支持 HTTPS 类型代理或 IPv6 代理地址。主机支持 IPv4 和可解析到 IPv4 的域名。

- 容器里的 `127.0.0.1` 指向该容器。宿主机代理可填写 `host.docker.internal` 或 NAS 局域网 IP；代理必须监听容器可访问的接口，并允许 Docker 子网连接。
- 网络组件为代理主机进行 IPv4 DNS 引导，选择一个地址加入允许列表；应用配置变更时重新解析，暂不自动刷新 DNS。root supervisor 的固定 HTTPS 探测域名 `api.telegram.org` 也可能使用该引导 DNS 例外，因此不能声称所有 DNS 均经过代理。Bot API 进程的 DNS 与 Telegram 传输通过 TUN/代理；探测的 TLS 连接也通过 TUN。
- API 的普通直连出站被防火墙拒绝。只有代理端点、TUN 和内部 API 响应可以通过；代理失败时不会切回直连。
- 代理配置经严格字段校验、`sing-box check`、进程启动及经 TUN 到 `api.telegram.org` 的真实 TLS 请求后，才写回已应用的网络配置版本。网络版本只随代理内容变化，可不同于全局设置版本；修改日志保留等无关设置不会重新加载 TUN。该检查证明检查时刻的链路，不代表连接永远在线，也不等于真实视频下载验收。
- 切换代理会暂停派发并等待安全边界；已有获取请求可能无法立即停止。界面若显示 pending / restart_required，应按状态说明操作，不能视为已生效。验证失败保留上一有效配置并尝试恢复，失败状态仍会保留。
- 从代理改为直连需要更换 Compose 并重建服务，WebUI 不能移除 TUN 或修改 Docker 网络拓扑。

网络容器被替换时，API 必须一起重建，避免仍绑定旧 namespace。先等待活动任务完成，保存配置，再运行：

```sh
docker compose --env-file .env -f compose.proxy.yaml down
docker compose --env-file .env -f compose.proxy.yaml up -d --force-recreate
```

本地 API 更换为直连时，先对旧代理文件执行 `down`，再运行 `docker compose --env-file .env -f compose.yaml --profile local-api up -d --force-recreate`，确保本地 API 一起启动；反向切换同理。不删除任何持久化目录。涉及云端/本地模式变化时，另须遵循 [迁移顺序与冷却要求](telegram-api.md#从云端切换到本地顺序很重要)。

## 从云端 API 迁移

标准管理部署按 [WebUI 接入流程](telegram-api.md#从云端切换到本地顺序很重要) 操作，后台负责暂停消费者、云端退出和连接验证。外部或旧手动管理部署按官方要求自行协调，不能与自动切换同时使用。不要在每次启动时重复退出。

成功 `logOut` 后本地可立即登录；10 分钟冷却针对重新登录云端。反向切换及更换本地服务器另见指南和 [官方说明](https://github.com/tdlib/telegram-bot-api#switching)。本项目不会自动修改其他应用的 webhook 或退出其会话，操作前确认 Bot 归属。

## 初次验收

先小文件，再大于 20 MB 的视频、混合媒体组、重复发送、重试/取消、应用重启及代理断开。检查最终文件大小、同组同目录、Bot 反馈和 WebUI 状态。验证边界见 [verification.md](verification.md)。故障和备份见 [operations.md](operations.md)。
