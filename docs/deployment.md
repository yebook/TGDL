# Docker 部署

::: warning 账号可能受限或封禁
Bot 及关联的所有者个人账号均可能受影响，云端和本地 API 都不能保证免于限制。创建或接入 Bot 前，请阅读 [账号风险与异常处理](faq.md#account-risk)。
:::

先用默认的**官方云端模式**完成第一次下载：只需 Bot Token 和自己的 Telegram 数字用户 ID，无需申请 API ID / Hash。部署统一使用根目录 **`compose.yaml`** 从源码构建，图形化部署也只需选择这一个文件。后续代理配置、本地 API 切换都在 WebUI 完成。

## 部署前准备

### 1. 取得源码与部署文件

任选一种方式取得完整项目源码：

- **下载 ZIP**：打开 [项目仓库](https://github.com/yebook/TGDL)，选择 **Code → Download ZIP**，解压后进入包含 `compose.yaml` 的目录，将该目录放到部署主机。
- **使用 Git**：在部署主机执行以下命令，后续可用 Git 更新源码。

```sh
git clone https://github.com/yebook/TGDL.git
cd TGDL
```

所有后续命令都在这个项目根目录执行。源码构建不能只上传 YAML 文件：首次启动会用本地源码构建两个镜像。

| 必须保留 | 用途 |
| --- | --- |
| [compose.yaml](https://github.com/yebook/TGDL/blob/main/compose.yaml) | 共用服务与构建定义；默认使用本机标签进行源码构建 |
| [.env.example](https://github.com/yebook/TGDL/blob/main/.env.example) | 复制成 `.env`，填写路径、权限和访问端口 |
| `Dockerfile`、`.dockerignore` | 应用构建入口；排除本机配置、数据和依赖缓存 |
| `backend/`、`frontend/` | 应用源码、版本信息、依赖清单和锁文件；保留完整目录 |
| `deploy/` | 应用启动脚本、本地 API 构建文件、预编译压缩包及校验清单、补丁、头文件和管理脚本；保留完整目录 |
| `.gitattributes` | Git 检出时保持补丁等文件的换行格式 |
| `LICENSE`、`deploy/sources.md` | 项目许可和第三方来源；第三方 LICENSE / NOTICE 如有也一并保留 |

**只用于部署时可以不保留的内容：**

| 内容 | 删除后的影响 |
| --- | --- |
| `.codex`、`.agents`、`.trellis`、`AGENTS.md` | AI 辅助开发配置与记录，不参与镜像构建 |
| `.github` | GitHub 仓库自动化配置，不影响本机运行 |
| `docs/` | 文档站源码，可直接阅读在线文档 |
| `scripts/` | 仓库开发与检查工具；不要误删 `deploy/` 内的运行脚本 |
| 本机生成的 `node_modules`、`.venv`、`__pycache__`、测试报告与构建缓存 | 镜像会自行安装依赖和构建；不要连同源码、依赖清单或锁文件一起删 |
| `.git` | 可以运行，但失去 `git pull` 等更新能力，之后需重新下载源码；建议 Git 部署保留 |

这些是精简源码副本的建议，不是启动前必须执行的清理步骤。最省事的方式是保留下载的源码原样。**已运行实例的 `.env`、数据目录、API 缓存、下载文件和私有 Docker 卷都不是开发垃圾**；不要为了精简部署而删除，也不要分享其中的密钥和私人数据。重新下载 ZIP 更新源码时保留现有 `.env`、数据和挂载位置，先按 [维护指南](operations.md) 备份，不能用新的源码目录覆盖或清空业务数据。

### 2. Docker 与存储环境

- 安装 Docker Engine 和 **Docker Compose v2**，使用 `linux/amd64` 容器（主机 `x86_64`）。当前源码构建与预编译镜像均仅支持此架构；ARM 主机暂不支持。运行 `docker version` 和 `docker compose version` 确认引擎可访问；Windows 使用 Docker Desktop，见 [本地运行](local-run.md)。
- 首次源码构建需要访问 Docker Hub、Debian、PyPI 和 npm。源码已附带 `linux/amd64` 的 Bot API／TDLib 预编译压缩包，**默认无需下载原生源码或编译 TDLib**；应用依赖安装与前端构建仍会执行。只有主动选择完整原生编译时，才需要访问 GitHub 下载上游源码并承担 C++ 编译的时间与内存开销。运行时尚未填写本地凭据也可先用云端。
- Compose 固定使用附带的 Bot API／TDLib 预编译包，无需额外选择原生镜像或修改编译模式。
- 准备应用数据、下载、API 缓存三个目录，并允许所选 UID/GID 写入。数据库使用可靠本地文件系统，不放在 SMB/NFS 上；媒体共享应先挂载。本地下载保存期间，API 缓存与目标文件会暂时各占一份空间；最终文件安全保存后自动清理对应媒体缓存，API 运行数据保留。仍需预留两份大文件及并发临时空间，详见 [缓存维护](operations.md#api-缓存维护)。
- 云端和本地均可在 WebUI 配置 HTTP CONNECT / SOCKS5，无需 TUN 设备或 NET_ADMIN 权限。Docker 不会自动继承宿主的系统或浏览器代理。

### 3. Bot Token 与自己的用户 ID

1. 在 Telegram 找到官方 **[@BotFather](https://t.me/BotFather)**，发送 `/newbot`，按提示创建专属 Bot 并保存 Bot Token。
2. 准备自己账号的**数字用户 ID**，不是 `@用户名`、手机号、Bot ID 或频道 ID。可向自己信任的用户信息查询 Bot 发送 `/start`，读取回复中的用户数字 ID；这类第三方工具不属于本项目，不要向其提供 Bot Token、密码或登录验证码。
3. 同一个 Bot 仅供一个程序接收消息，停止其他使用该 Token 的程序。本项目按一个管理员、一个 Bot、单实例部署。

这两项稍后填在 WebUI，**不需要写入 `.env` 或 `compose.yaml`**。仅下载超过 20 MB 的文件时，再 [申请 API ID / Hash 并切换本地 API](telegram-api.md)。

## 最小启动步骤

以下为 Linux / SSH 命令。在项目根目录执行，按实际用户替换示例 `1000:1000`，UID 和 GID 可以不同。`id` 显示当前用户信息，也可用 `id 用户名` 查询指定用户。推荐使用普通用户；UID 和 GID 均支持 `0`，也可只将其中一项设为 `0`。容器仍移除 capabilities，使用 `0` 不会绕过所有目录权限检查，挂载目录与私有控制文件的权限仍需匹配：

```sh
cp .env.example .env
id
mkdir -p data bot-api downloads
sudo chown 1000:1000 data bot-api downloads
sudo chmod 750 data bot-api downloads
chmod 600 .env
```

这些命令只设置项目顶层目录，不递归修改已有媒体库。已有 NAS 共享的 ACL 也必须允许该 UID/GID 写入。API 控制信息使用私有 Docker 卷，初始化服务负责准备权限，不需要另建控制目录。

编辑刚复制的 `.env`：将 `TGD_UID` / `TGD_GID` 与实际授权的用户对应；默认路径和端口可直接用于测试，端口冲突时修改 `TGD_WEB_PORT`。若修改了三个宿主目录路径，也要为对应路径创建目录并授予权限，不能只给默认目录授权。完整配置见下方 [参数设置](#参数设置)。

确认媒体共享已挂载后，使用源码构建并启动：

启动时加载自己的 `.env`。Compose 固定使用本机源码镜像，自动构建并复用有效缓存。

```sh
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

`config --quiet` 只验证配置，不打印展开后的内容。源码部署后续也只使用同一份 `compose.yaml`；仅修改 `.env` 时执行 `docker compose up -d --no-build`，无需重新构建。初始化服务正常完成后退出是预期行为；其他组件常驻。启动成功不表示 Telegram 已连接。

访问 `http://部署主机IP:8000`，本机也可使用 `http://localhost:8000`；修改了 `TGD_WEB_PORT` 时使用对应端口，无需另外配置绑定地址。读取初始化密钥并创建管理员账号和 6–20 位密码。默认数据目录下执行：

```sh
cat data/bootstrap.key
```

如果修改了 `TGD_HOST_DATA`，请读取对应目录中的 `bootstrap.key`；权限不足时由有权访问该目录的用户读取，例如使用 `sudo cat data/bootstrap.key`，不要放宽密钥文件权限。该密钥只用于首次初始化，不要分享；之后使用管理员密码登录。

默认选择官方云端。在「设置 → 连接 Telegram」保存 Bot Token 和用户 ID 白名单，按需启用代理。保存只检查格式，不要求在线；在顶部查看连接概况，必要时手动校验。先发送 `/start` 检查回复，再发送小于 20 MB 的图片或视频，确认下载后检查文件。操作细节见 [第一次下载](quick-start.md)。

仅 WebUI 端口对宿主开放，本地 API 的 8081 不对外发布。WebUI 仍需初始化管理员并登录；HTTPS 可由自己的反向代理终止。

## 参数设置

通常只需修改 `.env`，`compose.yaml` 保持原样。下面两份均为完整注释示例，与仓库模板配置一致；每个参数或配置项**下方的 `#` 注释**说明其用途和是否需要修改，可直接复制。示例中的容器内部路径需要彼此对应，不要只改其中一处。

### .env：需要按主机调整的参数

在项目根目录将 `.env.example` 复制为 `.env`，或使用以下内容。相对挂载路径以 Compose 文件所在目录为基准。

```dotenv
TGD_HOST_DATA=./data
# 应用数据的宿主目录，保存数据库、加密密钥、初始化密钥和日志；可改，使用可靠本地存储。
TGD_HOST_DOWNLOADS=./downloads
# 最终媒体的宿主目录；可改为已挂载的媒体共享，必须允许下面的 UID/GID 写入。
TGD_HOST_API_CACHE=./bot-api
# 本地 API 运行数据和媒体缓存的宿主目录；可改。保存期间临时占两份空间，成功后自动清理对应媒体缓存，保留运行数据。
TGD_UID=1000
# 应用与本地 API 的数字用户 ID；支持 0，推荐普通用户，按目录权限设置，可用 id 查询。
TGD_GID=1000
# 对应的数字组 ID；支持 0，按实际用户组和目录权限修改，不一定与 UID 相同。
TGD_WEB_PORT=8000
# WebUI 的宿主端口；被占用时可改，容器内部仍使用 8000。
TGD_TIMEZONE=Asia/Shanghai
# 应用时区，影响日期归档和日志清理；标准部署由此环境变量固定，WebUI 中只读，修改后需重建 app 容器。
TGD_BUILD_JOBS=2
# 仅手动选择完整原生编译时使用，范围 1–64；默认部署使用附带产物，无需编译 TDLib。
```

WebUI 的下载根目录对应容器中的 `/downloads`，宿主实际位置由 `TGD_HOST_DOWNLOADS` 决定，不在页面输入宿主路径。Bot Token、用户 ID、API ID / Hash 和代理均在 WebUI 配置，不放进准备分享的部署文件。

已运行实例修改 `TGD_TIMEZONE` 后，等待任务和 API 切换结束，按 [维护指南](operations.md) 备份，再执行以下命令重建应用容器以载入新环境变量；只执行 `restart` 不会更新环境变量，无需重新构建镜像：

```sh
docker compose up -d --no-build --no-deps app
```

### compose.yaml：服务与挂载配置

以下对应根目录 [compose.yaml](https://github.com/yebook/TGDL/blob/main/compose.yaml)。`${变量:-默认值}` 表示优先采用环境文件中的值，未设置时使用默认值；一般无需改这些表达式。固定使用本机源码标签，默认启动时构建，不拉取远程应用/API 镜像；仅修改运行参数时可用 `--no-build` 跳过构建。

```yaml
name: tgd
# Compose 项目名称；通常保留，已部署后修改会创建另一组容器和私有卷。
services:
# 三个服务：WebUI/下载应用、本地 API 管理服务、一次性权限初始化。
  app:
  # 应用服务，承载 WebUI、Bot 消息处理和下载任务。
    image: tgd-app-source:0.1.1
    # 本机源码构建标签，与旧运行标签区分；不从仓库拉取。
    pull_policy: build
    # 默认启动时构建源码；有效构建缓存仍可复用。
    build:
    # 源码构建定义，仅修改运行参数时使用 --no-build 跳过。
      context: .
      # 完整项目根目录作为构建上下文；源码部署不要删构建依赖目录。
      dockerfile: Dockerfile
      # 应用镜像的构建文件。
    user: "${TGD_UID:-1000}:${TGD_GID:-1000}"
    # 运行身份（支持 UID/GID 为 0）；在 .env 修改 UID/GID，不必改这里。
    init: true
    # 启用轻量 init，协助转发信号、回收子进程；保留。
    restart: unless-stopped
    # 异常退出自动重启，手动停止后保持停止；保留。
    ports: ["${TGD_WEB_PORT:-8000}:8000"]
    # 按 Docker 默认方式发布 WebUI；宿主端口在 .env 设置，内部 8000 保留，无需绑定 IP 参数。
    environment:
    # 以下是容器内部配置；不是需要填写 Telegram 密钥的位置。
      TGD_DATA_DIR: /data
      # 应用数据内部路径，需与下方数据挂载目标一致；保留。
      TGD_DOWNLOAD_ROOT: /downloads
      # 下载根目录内部路径，需与下方媒体挂载目标一致；保留。
      TGD_BOT_API_BASE: http://bot-api:8081
      # 应用访问内部本地 API 的地址，不向宿主发布；保留。
      TGD_BOT_API_ROOT: /var/lib/telegram-bot-api
      # 应用读取本地 API 缓存的内部路径，必须与 API 一致；保留。
      TGD_MANAGED_LOCAL_API: "true"
      # 允许 WebUI 管理本地 API 子进程；云端起步也保留此项。
      TGD_LOCAL_API_CONTROL_DIR: /run/tgd-api-control
      # 与本地管理服务交换控制信息的私有卷路径；保留。
      TGD_STATIC_DIR: /app/frontend/dist
      # 镜像内 WebUI 静态文件位置；保留。
      TGD_TIMEZONE: ${TGD_TIMEZONE:-Asia/Shanghai}
      # 应用时区来自 .env，WebUI 中只读；修改 .env 后需重建 app 容器，不是重新构建镜像。
    volumes:
    # 持久化业务数据、媒体、API 缓存及私有控制信息。
      - ${TGD_HOST_DATA:-./data}:/data
      # 宿主数据目录挂载到 /data；改宿主位置请修改 .env。
      - ${TGD_HOST_DOWNLOADS:-./downloads}:/downloads
      # 最终媒体挂载，可写；启动前确认共享已挂载并授予权限。
      - ${TGD_HOST_API_CACHE:-./bot-api}:/var/lib/telegram-bot-api:ro
      # 应用只读访问 API 缓存，ro 请保留；实际文件由 API 服务写入。
      - api-control:/run/tgd-api-control
      # Docker 私有控制卷；自动准备，不要当临时文件删除。
    extra_hosts: ["host.docker.internal:host-gateway"]
    # 提供宿主访问名称，便于填写宿主代理地址；不等于自动启用代理。
    networks: [tgd]
    # 加入项目专用网络，与内部 API 通信；保留。
    depends_on:
    # 启动依赖；不代表 Telegram 上游已连接。
      bot-api: {condition: service_started}
      # 等管理服务容器启动，不要求已经填写 API ID / Hash。
      init-volumes: {condition: service_completed_successfully}
      # 等一次性控制卷权限初始化成功；保留。
    read_only: true
    # 容器根文件系统只读，业务写入使用明确的挂载；保留。
    tmpfs: ["/tmp:rw,noexec,nosuid,size=64m"]
    # 内存临时目录，最多 64 MiB，禁止执行与提权；通常保留。
    cap_drop: [ALL]
    # 移除额外 Linux 能力；保留。
    security_opt: [no-new-privileges:true]
    # 禁止进程获得新权限；保留。
    stop_grace_period: 90s
    # 停止时最多给予 90 秒收尾；维护前仍应等待任务完成。
    logging: &logging
    # 定义共享的 Docker 日志轮转配置，与 WebUI 诊断日志清理分开。
      driver: json-file
      # 使用 Docker JSON 文件日志驱动；通常保留。
      options: {max-size: "10m", max-file: "3"}
      # 每个容器日志文件最多 10 MiB、最多 3 个；按需调整容量。
  bot-api:
  # 常驻本地 API 管理服务；初次使用云端也会启动，但无需填写本地凭据。
    image: tgd-bot-api-source:0.1.1
    # 配套 API 镜像引用，必须与初始化服务完全相同；不可换成普通第三方镜像。
    pull_policy: build
    # 与应用采用相同构建策略，不从 GHCR 拉取运行镜像。
    build:
    # API 镜像只由此服务构建，初始化服务直接复用构建结果。
      context: .
      # 完整项目根目录作为构建上下文。
      dockerfile: deploy/bot-api/Dockerfile
      # 使用项目定制的 API 构建文件。
      args:
      # 以下仅在源码构建时使用。
        TGD_BUILD_JOBS: ${TGD_BUILD_JOBS:-2}
        # 仅完整原生编译时的线程数；默认使用预编译包。
        TGD_NATIVE_IMAGE: bundled-native
        # 默认校验并使用仓库附带的 Bot API／TDLib 产物。
    user: "${TGD_UID:-1000}:${TGD_GID:-1000}"
    # 与应用相同的 UID/GID，便于共享缓存；在 .env 设置。
    init: true
    # 协助进程信号与回收；保留。
    restart: unless-stopped
    # 异常重启、手动停止后保持停止；保留。
    networks: [tgd]
    # 仅加入内部通信网络，8081 不发布到宿主；保留。
    extra_hosts: ["host.docker.internal:host-gateway"]
    # 允许本地 API 使用可达的宿主代理地址，仍需在 WebUI 启用代理。
    depends_on:
    # 先准备控制卷权限，再启动管理服务。
      init-volumes: {condition: service_completed_successfully}
      # 一次性初始化必须成功；保留。
    environment:
    # 本地管理服务配置，无需在这里写 API ID / Hash。
      TGD_LOCAL_API_CONTROL_DIR: /run/tgd-api-control
      # 控制卷路径，与应用和初始化服务一致；保留。
    volumes:
    # API 需要写缓存和控制信息，不挂载应用数据库或下载目录。
      - ${TGD_HOST_API_CACHE:-./bot-api}:/var/lib/telegram-bot-api
      # API 缓存可写，与应用的只读缓存指向同一宿主目录。
      - api-control:/run/tgd-api-control
      # 与应用共享的私有控制卷；保留。
    read_only: true
    # 根文件系统只读；保留。
    tmpfs: ["/tmp:rw,noexec,nosuid,size=64m"]
    # 有容量和执行限制的内存临时目录；通常保留。
    cap_drop: [ALL]
    # 不授予额外 Linux 能力，无需 NET_ADMIN；保留。
    security_opt: [no-new-privileges:true]
    # 禁止获得新权限；保留。
    stop_grace_period: 90s
    # 为本地 API 子进程退出预留收尾时间；保留。
    logging: *logging
    # 复用 app 中定义的 Docker 日志轮转策略。
  init-volumes:
  # 只准备私有控制卷权限，成功退出是正常现象。
    image: tgd-bot-api-source:0.1.1
    # 与 bot-api 使用同一镜像引用；不独立选择版本。
    pull_policy: never
    # 使用 bot-api 在服务创建前构建好的镜像，不重复构建或远程拉取。
    entrypoint: ["python3", "/usr/local/lib/tgd-init-volumes.py"]
    # 执行镜像内权限初始化脚本，不启动 Telegram 服务；保留。
    restart: "no"
    # 一次性操作完成后不重启；保留。
    network_mode: none
    # 初始化无需网络；保留。
    environment:
    # 将所选运行用户和组传给初始化脚本。
      TGD_UID: ${TGD_UID:-1000}
      # 在 .env 选定的用户 ID（支持 0）；保留表达式。
      TGD_GID: ${TGD_GID:-1000}
      # 在 .env 选定的组 ID；保留表达式。
    volumes:
    # 初始化只操作私有控制卷，不处理宿主数据目录权限。
      - api-control:/run/tgd-api-control
      # 与 app、bot-api 相同的控制卷挂载；保留。
    read_only: true
    # 根文件系统只读；保留。
    cap_drop: [ALL]
    # 先移除所有额外 Linux 能力；保留。
    cap_add: [CHOWN, DAC_OVERRIDE, FOWNER]
    # 仅为控制卷的权限初始化增加所需能力；不必给常驻服务增加。
    security_opt: [no-new-privileges:true]
    # 禁止获得新权限；保留。
    logging: *logging
    # 复用 Docker 日志轮转策略。
volumes:
# 声明 Docker 管理的持久卷，不是业务目录清理列表。
  api-control:
  # 私有控制卷，由 init-volumes 准备；不要使用 down -v 随意删除。
networks:
# 声明服务通信网络；未启用 internal 隔离，仍可按配置访问外网。
  tgd:
  # 项目专用网络，通常保留默认配置。
```

## 源码镜像与构建缓存

本机使用 `tgd-app-source:0.1.1` 与 `tgd-bot-api-source:0.1.1`。默认 `pull_policy: build` 会在启动时构建源码，Docker 仍会复用有效构建缓存。基础镜像与应用依赖仍需联网下载，Bot API／TDLib 使用仓库附带的预编译包。

## 修改配置时不用构建

更新应用版本请按 [版本升级步骤](upgrade.md#upgrade) 操作：先停服备份并保留旧镜像，再更新源码、构建和检查。不要直接用新 ZIP 覆盖现有配置或业务目录。

改 `.env` 中的目录、端口、UID/GID 或时区后，等待任务结束并备份，用原部署方式重新创建容器即可。源码部署：

```sh
docker compose up -d --no-build
```

单纯 `restart` 不会更新环境变量和目录挂载。更换媒体宿主目录须先停服，迁移原媒体并确认权限和文件可访问，然后按[下载目录身份登记](upgrade.md#storage-identity-011)明确登记新目录；不要通过换空目录绕过存储保护。

只更新应用源码时可 [单独构建 app](development.md#源码构建与缓存)，无需重新编译未变的本地 API。日常更新不要加 `--no-cache`，也不要清空 Docker 缓存。

## 后续接入本地 API

1. 按 [申请步骤](telegram-api.md#获取-api-id-api-hash) 获取 API ID / Hash。
2. 在 WebUI 填写凭据，选择本地模式并保存；如需代理，在同一设置页配置。
3. 后台先准备切换，有活动下载时等待安全结束，并协调在途请求；随后退出云端，按目标代理配置启动本地进程并验证 Bot。没有活动下载时不会提示等待下载。
4. 查看已保存模式、实际模式、切换步骤和 Bot 连接；实际连接成功后再测试超过 20 MB 的视频。

日常无需停止应用、重启容器或手动执行 `logOut`。修改凭据可重启容器内 API 进程；WebUI 的“重启 API 进程”不代表重建容器。管理组件失联、宿主网络能力缺失或容器自身停止时仍需 Docker 排查。

云端退出结果不确定时系统保持暂停，需按状态提示核实后继续；不会反复退出或自动回连云端。云端退出成功后可立即登录本地，返回云端需等待至少 10 分钟。完整流程见 [API 接入](telegram-api.md)。

## 统一的网络代理

常驻服务为应用与本地 API 管理服务，另有一次性权限初始化服务。两种 API 使用同一份 WebUI 代理设置；本地服务采用固定官方源码及 TDLib 原生代理补丁构建，不需要额外网络容器。

- **关闭代理**：使用容器的默认网络出口；不会自动读取宿主的系统或浏览器代理。
- **启用代理**：在 WebUI 选择 HTTP CONNECT 或 SOCKS5，填写地址、端口和可选认证。云端 HTTPS 与本地 Telegram 连接均使用此配置；应用到内部本地 API 的 HTTP 连接保持直连。
- **切换中**：页面显示等待应用的状态；保存成功不代表网络已生效或 Bot 已连接。
- **失败**：显示安全错误原因。启用代理失败不会自动回退直连；排查后按页面提示重试。

代理地址必须能由容器访问。宿主代理可填写 `host.docker.internal` 或可达的 LAN 地址，并允许 Docker 网络访问该端口；容器中的 `127.0.0.1` 指向容器自己。两种模式均无需另外开启宿主代理软件的 TUN，只需提供可达的 HTTP CONNECT / SOCKS5 服务。

系统代理、浏览器代理或 `HTTP_PROXY` 变量不会自动配置本项目的连接。请在 WebUI 填写；原版官方 API 的 `--proxy` 参数仅用于 webhook，本项目的本地 Telegram 代理由固定源码补丁接入 TDLib 实现。

## 使用 Docker 图形管理工具

1. 将完整源码放到部署主机的项目目录，按上文准备 `.env` 和业务目录。
2. 在 Docker 管理工具中新建 Compose 项目，选择项目根目录的 `compose.yaml`。启用源码构建，仅需要这一个 Compose 文件。
3. 确认环境文件、挂载与用户权限可用。按当前源码构建后启动。以后只修改 `.env` 时，重新部署容器即可，无需重新构建镜像；单纯“重启”不会更新挂载或环境变量。
4. 打开 WebUI 完成初始化，后续只在设置页管理代理与 API。

若图形界面不支持加载 `.env` 或设置构建上下文，使用 SSH 执行对应命令。不同工具的界面与构建支持有所差异，以实际工具为准。

## 日常命令

```sh
docker compose --env-file .env -f compose.yaml ps
docker compose --env-file .env -f compose.yaml logs --tail 100 app
docker compose --env-file .env -f compose.yaml stop
docker compose --env-file .env -f compose.yaml start
```

容器维护前等待活动任务结束并备份。普通 WebUI 代理开关无需重建容器；本地代理变更会在安全边界重启 API 子进程。不要用 `down -v` 清理部署，它会删除私有持久卷。备份范围和故障处理见 [维护指南](operations.md)。
