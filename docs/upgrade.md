# 版本升级

## 版本升级步骤 {#upgrade}

以下是 Docker 源码部署的通用升级流程。命令在原项目根目录执行，继续使用原 `.env`、目录挂载和 Compose 项目名；图形化管理工具也应更新**原项目**，避免另建项目后连到空数据或新的控制卷。升级前先读 [更新日志](changelog.md)，确认目标版本及该版本是否有额外操作；普通升级无需清空数据或重新登记下载目录。

### 1. 停服、备份与保留回退镜像

先停止向 Bot 发送新媒体，等待任务、API 切换和缓存清理结束。记录左下角的当前版本，并记录原源码 revision（Git 部署用 `git rev-parse HEAD`；ZIP 部署保留原源码副本）。在删除或重建容器前保存实际运行的镜像 ID，并加上本地回退标签：

```sh
docker compose images
docker image tag "$(docker inspect --format '{{.Image}}' "$(docker compose ps -q app)")" tgd-app-rollback:before-upgrade
docker image tag "$(docker inspect --format '{{.Image}}' "$(docker compose ps -q bot-api)")" tgd-bot-api-rollback:before-upgrade
docker compose stop
```

这些是 Linux/macOS shell 命令；图形化部署可在镜像管理中记录 ID、保留镜像并停止原项目。不要在完成升级验证前删除旧镜像或清理 Docker 缓存。

确认服务已停止，再按 [完整停服备份](operations.md#备份与恢复) 保存以下内容到项目目录之外：

- 实际使用的 `.env`、Compose 文件、原源码版本。
- `TGD_HOST_DATA` 对应的**完整应用数据目录**，包括数据库、可能存在的 WAL/SHM、`master.key`、下载目录身份记录及其他配置。
- `TGD_HOST_DOWNLOADS`、`TGD_HOST_API_CACHE` 对应目录，以及原项目的 `api-control` 命名卷。路径可能已在 `.env` 自定义，不能只备份默认目录名。

WebUI 导出的密码加密配置备份仅用于迁移可编辑设置，**不能替代上述完整备份**：它不包含任务数据库、管理员账号、应用加密主密钥、目录身份、媒体或 API 会话。完整备份包含密钥，需妥善保管。不要执行 `down -v`。

### 2. 更新源码（任选一种）

**Git 部署**：确认本地没有需要保留的源码修改，再拉取更新；若命令因本地修改失败，先保留并处理修改，不要使用强制重置覆盖。`.env` 和业务目录保持原样。

```sh
git status --short
git pull --ff-only
git rev-parse --short HEAD
```

记录更新后的 revision，确认它对应准备部署的内容；不假设已有发布标签。

**ZIP 部署**：下载目标版本源码，先解压到**另一个临时目录**，再将源码更新到原项目目录：

1. 保留原 `.env`，不要用 `.env.example` 替换；以原 `.env` 中三个宿主目录参数为准确认业务数据的位置。
2. 保留数据、下载、API 缓存目录及命名卷。自定义目录即使位于源码树内，也应排除在源码替换之外；不要对整个部署目录执行清空或镜像同步。
3. 只替换项目源码和构建文件，包括隐藏的 `.dockerignore`；对照新源码移除确已废弃的**源码文件**，不要删除业务目录。若此前自定义了 Compose，先比对再合并，保留原挂载与项目名。
4. 仍在**原项目目录**启动。不要直接从临时解压目录运行，以免 `./data` 等相对路径指向新的空目录。

### 3. 构建并启动

先确认原挂载可用、`.env` 未被覆盖，再执行：

```sh
docker compose config --quiet
docker compose up -d --build
docker compose ps -a
docker compose logs --tail 100 app bot-api init-volumes
```

`config --quiet` 只做校验，不打印展开的配置或密钥。初始化服务退出码为 `0` 是正常结果；应用与 API 管理服务应保持运行。构建会复用有效缓存，匹配当前源码的原生预编译包可直接复用，无需每次重新编译 TDLib。

打开 WebUI 刷新页面，确认左下角显示目标版本，任务记录、设置和已有媒体仍可访问；查看顶栏实际 API 模式、Bot 与代理状态。需要时手动校验连接，再发送一条测试媒体，核对保存结果和 Bot 回复。容器成功启动不等于 Telegram 实际收发已验证。

仅改 `.env` 而没有更新源码时，使用 `docker compose up -d --no-build`；单纯 `restart` 不会加载新的挂载或环境变量。

### 4. 失败时回退

若构建或启动失败，先保留脱敏日志并停止新栈，不要清空数据重试。恢复升级前保存的源码、`.env`、Compose 与**同一份停服备份**中的应用数据、媒体、API 缓存及控制卷，保留原项目名和挂载位置。回退数据时也必须停服，避免混用新数据库与旧密钥。

将前面保留的两个回退镜像分别重新标记为**已恢复的旧版 Compose 中对应的镜像标签**。以下变量需填写旧文件中 `app.image` 和 `bot-api.image` 的实际值：

```sh
OLD_APP_IMAGE='填写旧版 app 的完整镜像标签'
OLD_API_IMAGE='填写旧版 bot-api 的完整镜像标签'
docker image tag tgd-app-rollback:before-upgrade "$OLD_APP_IMAGE"
docker image tag tgd-bot-api-rollback:before-upgrade "$OLD_API_IMAGE"
docker compose config --quiet
docker compose up -d --no-build
docker compose ps -a
```

这里必须使用已恢复的旧版 Compose。`--no-build` 使用保留的旧镜像，避免从新源码重新构建。回退后按上述步骤核对版本、数据与实际收发；不提供任意跨版本数据库降级保证。不要只切回旧镜像而继续使用已被新版修改的数据。若恢复到新目录或设备，按 [下载目录身份登记](operations.md#storage-identity) 核对挂载后处理；宿主机升级与恢复演练范围见 [验证记录](verification.md)。
