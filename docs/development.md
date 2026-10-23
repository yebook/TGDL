# 开发指南

后端位于 `backend/`（Python 3.12、FastAPI、SQLite），前端位于 `frontend/`（Vue、TypeScript）。部署服务为单实例，不运行多个应用 worker。

## 工具与依赖

当前首版尚未正式发布，不为未发布版本保留兼容分支；功能修改同步更新当前契约、测试与文档。不兼容的旧测试数据可以清理，优先保持当前设计简洁；清理前明确范围，媒体文件不随测试数据清理，正常运行中的下载所有权与 Telegram 安全保护不能绕过。正式发布后再制定版本兼容和迁移策略。

数据库当前使用单一 `initial` 初始结构，不提供早期开发修订的自动升级。遇到不支持的开发数据库时，先停服、备份并明确要保留的配置及文件，再重建测试数据库；不要只修改版本号强行启动，也不要通过清理运行状态绕过不确定获取或 Telegram 冷却。配置导入只恢复设置，不恢复这些安全状态。

安装 Python 3.12、uv、Node.js 24 和 pnpm 11.23.0。使用提交的锁文件，不手动修改锁定版本。文档有独立依赖包，不加入应用镜像或前端依赖。

## 版本与更新日志

版本唯一来源是 `backend/app/version.json`，仅保存数字版本，当前版本显示在站点导航，变更见 [更新日志](changelog.md)。不添加阶段标签或英文预发布后缀；修复递增修订号，新功能递增次版本，首次正式版使用 `1.0.0`。

修改版本源后，在项目根目录执行：

```sh
python scripts/version.py --write
python scripts/version.py --check
```

脚本同步并校验各包、锁文件、镜像与构建元数据；不会生成更新说明、创建 Git 标签或发布镜像。说明在根目录 `CHANGELOG.md` 维护：新改动先记入「未发布」，确定版本时记录数字版本、日期和新增/改进/修复。文档站 [更新日志](changelog.md) 直接复用该文件，不维护第二份副本。版本检查后仍需运行受影响包的检查。

## 应用检查

在 `backend/` 中执行：

```sh
uv sync --locked
uv run ruff check app tests tools ../scripts ../deploy
uv run mypy app
uv run pytest -q
```

在 `frontend/` 中执行：

```sh
pnpm install --frozen-lockfile
pnpm lint
pnpm typecheck
pnpm test:unit
pnpm build
```

优先运行受影响的测试文件或用例；共享基础设施、发布检查或出现未解决失败时再扩大范围。以上完整命令供需要全量验证时使用，不要求每次文字修改都全量回归。

## 浏览器与容器检查

在 `frontend/` 安装浏览器并运行：

```sh
pnpm exec playwright install chromium
pnpm test:e2e
```

该命令构建前端，启动真实本地后端及独立临时数据库，不连接 Telegram。不要把测试服务指向生产数据。Windows 的部分 POSIX 文件权限测试会跳过，需要 Linux 隔离环境补充。

运行中部署的只读检查见 [部署后验收](acceptance.md)；使用临时容器修改临时数据的工具见 [Docker 隔离验收](docker-acceptance.md)。真实设备待测项见 [验证范围](verification.md)。

## 源码构建与缓存

部署只使用 `compose.yaml` 从源码构建。固定本机源码标签与默认构建策略，启动时不会拉取远程应用/API 镜像；可复用有效构建缓存。首次构建：

使用完整源码和自己的 `.env`，不加载 `images.env`。Compose 已屏蔽远程运行镜像与拉取策略覆盖参数。

```sh
docker compose up -d --build
```

仅前后端源码改变、本地 API 未改动时：

```sh
docker compose build app
docker compose up -d --no-build --no-deps app
```

维护前先等待下载和 API 切换结束，备份配置及数据；`--no-deps` 不是绕过所有权恢复的手段。修改本地 API 管理脚本后，只需重新打包轻量 API 运行镜像；修改原生源码、补丁、头文件、编译工具链或原生依赖后，才需重新编译原生组件。两种情况都须让 `bot-api`、`init-volumes` 保持同一最终镜像。应用与 API 私有协议有变动时应成对验证，不随意混装。

### 原生组件单独预编译

源码仓库附带 Bot API／TDLib 的预编译压缩包，默认构建时校验并解压成原生基础镜像，最终 API 镜像在其上打包监督进程、启动及权限初始化脚本。TDLib 已链接进 Bot API 可执行文件，无需另带一套独立库。当前可执行文件约 43.4 MiB，压缩后约 14.8 MiB；同时保留许可证及来源信息，不使用 Git LFS。普通源码下载与 ZIP 下载均能取得实际产物。

发布流程直接用附带产物构建最终 API 镜像，不再单独发布原生基础镜像。最终 API 的复用标识同时覆盖原生构建输入、压缩包、清单和管理脚本；即使命中镜像缓存，也会先检查仓库内产物。原生源码、补丁或工具链改变后，维护者必须重新编译并更新预编译产物及其清单；旧产物会被校验拒绝，不会静默回退到长时间编译。

当前源码构建与预编译产物均仅支持 `linux/amd64`，构建会明确拒绝其他架构。Bot API 与 TDLib 分别固定源码提交，编译时校验实际 Git 提交。原生产物内的来源清单记录这两个提交、架构和原生输入指纹，指纹覆盖补丁、头文件、工具链及构建定义。最终 API 打包时再次校验，即使手动指定 `TGD_NATIVE_IMAGE`，缺少清单或与当前输入不符也会失败；更新原生代码后必须重新生成匹配产物。管理脚本与产品版本变化不会单独触发原生重编译。

| 改动范围 | 需要的构建 |
| --- | --- |
| 目录、端口或运行参数 | 无需构建，重新部署容器 |
| 前端、后端应用 | 仅应用镜像 |
| API 监督进程、启动或初始化脚本 | 仅轻量 API 打包，复用原生基础镜像 |
| 官方 Bot API / TDLib 版本、项目原生补丁或头文件、编译工具链及原生依赖 | 重新编译原生基础镜像，再打包 API |

普通用户始终只拉取应用与最终 API 两个运行镜像，不需要单独部署原生基础镜像，不需要安装编译器。原生产物包含项目定制的代理、进度和缓存清理功能，不能替换为普通第三方或未修改的官方 Bot API 二进制。

源码构建默认 `TGD_NATIVE_IMAGE=bundled-native`，使用 `deploy/bot-api/prebuilt/linux-amd64.tar.gz` 及配套 `manifest.json`。无需改配置，也无需等待镜像首次发布。维护者通常只需更新附带的预编译包；项目不再发布独立原生基础镜像，不能用普通 Bot API 或任意最终 API 镜像代替该产物。

只有需要修改原生代码时，才在项目根目录单独构建 `native` 目标并更新预编译包；普通 Compose 部署固定使用附带产物：

```sh
docker build -f deploy/bot-api/Dockerfile --target native --build-arg TGD_BUILD_JOBS=2 -t tgd-native:source-build .
python scripts/bundle_native.py --image tgd-native:source-build
```

第二步导出新产物、许可证和清单；更新后将压缩包、清单与对应的原生代码修改一起提交，普通部署继续使用固定的 `bundled-native`。缺失、损坏或与当前原生输入不匹配的产物会让默认构建失败，不能只修改版本号或绕过摘要校验来继续部署。

### 构建缓存与资源

Dockerfile 另外使用 BuildKit 的 pnpm、uv 下载缓存和 ccache 编译缓存，锁文件仍强制校验。缓存减少重复下载和手动原生重编译；这与仓库附带的预编译原生产物是不同机制。缓存挂载属于构建器，不能假定更换主机或导入远程层缓存后仍有相同的包/ccache 缓存。默认源码构建使用附带的原生产物，仍需下载应用依赖与基础镜像。

`.env` 的 `TGD_BUILD_JOBS=2` 仅控制原生编译线程数（1–64），小内存设备可降低为 `1`；不要直接按 CPU 核心数拉满。增加线程不加速网络下载，也可能因内存不足失败。未完成冷/热构建对比前，不承诺数值提速。

主动选择完整原生编译时，需要编译完整 TDLib C++ Release 代码，CPU 持续高占用属于正常编译行为，CPU 使用率与内存占用需分别观察。改为 `TGD_BUILD_JOBS=1` 后重新发起构建，可降低并行编译的 CPU 和内存压力，但仍可能占满一个核心，也不能保证降低单个源文件的内存峰值。缓存主要减少重复编译；Compose 运行容器的 CPU、内存限制不等于构建器的资源限制。默认使用附带产物时无需执行这些编译步骤。

构建缓慢时先看日志停在哪一步：Docker 引擎拉取基础镜像、构建环境下载 Debian/npm/Python 依赖、应用运行时连接 Telegram 是三个网络环节。WebUI 代理只配置最后一项，不会加速镜像拉取或依赖下载。镜像源/引擎代理由 Docker 主机配置；构建代理需由构建环境明确配置，不能将带密码代理写入镜像层或发布日志。`--no-cache` 和 Docker prune 不应作为常规更新步骤。

## 手动镜像发布

远程镜像发布与部署暂时停用，当前不需要准备 Docker Hub 凭据或运行发布工作流。相关工具保留供后续恢复，发布入口会明确拒绝执行；文档站 Pages 发布不受影响。

## 原生代理隔离验证

修改本地 API 源码补丁时，先编译实际镜像，再运行专用传输检查。以下命令在项目根目录执行，不挂载应用数据，不填写真实凭据；专用检查镜像安装 `strace`，只在断网容器中验证。

```sh
docker build -f deploy/bot-api/Dockerfile -t tgd-bot-api-source:0.1.0 .
docker build -f deploy/bot-api/Dockerfile.verify-native -t tgd-native-transport-acceptance .
docker run --rm --network none --cap-drop ALL --cap-add SYS_PTRACE --security-opt no-new-privileges:true --user 1000:1000 --read-only --tmpfs /tmp:rw,nosuid,size=256m tgd-native-transport-acceptance
```

检查脚本要求只有回环网卡，通过假 SOCKS5 / HTTP CONNECT 服务和系统调用记录验证：代理拒绝时没有直连尝试、同一缓存关闭代理后使用默认连接、无效代理或缺少模式时不启用网络。仅输出汇总，不输出请求、凭据或原始子进程日志。`SYS_PTRACE` 仅用于该隔离测试，正常部署无需此权限。

该检查不证明真实代理认证、Telegram 大文件下载或 Docker 宿主机已通过验收。

## 本地下载进度隔离验证

进度扩展读取现有 TDLib 下载事件的内存快照。修改后使用相同原生镜像构建专用检查镜像：

```sh
docker build -f deploy/bot-api/Dockerfile.verify-progress --build-arg TGDL_API_IMAGE=tgd-bot-api-source:0.1.0 -t tgd-native-progress-acceptance .
docker run --rm --network none --cap-drop ALL --cap-add SYS_PTRACE --security-opt no-new-privileges:true --user 1000:1000 --read-only --tmpfs /tmp:rw,nosuid,size=256m tgd-native-progress-acceptance
```

该检查覆盖生产缓存的别名、容量、完成保留与重试边界，并向实际原生服务发送缺少已有 Bot 客户端的进度查询，确认不会创建客户端或发起对外连接。后端的 `tests/test_local_download_progress.py` 覆盖进度传输、状态投影和下载生命周期；页面测试使用合成进度。真实 Telegram 的字节增长、速度和缓存命中仍需单独验收。

## 本地媒体缓存清理隔离验证

清理扩展在最终文件保存后调用 TDLib 的离线缓存删除方法。修改原生实现后，先构建 API，再构建专用验证镜像：

```sh
docker build -f deploy/bot-api/Dockerfile -t tgd-bot-api:cache-cleanup .
docker build -f deploy/bot-api/Dockerfile.verify-cache-cleanup --build-arg TGDL_API_IMAGE=tgd-bot-api:cache-cleanup -t tgd-native-cache-cleanup-acceptance .
docker run --rm --network none --cap-drop ALL --cap-add SYS_PTRACE --security-opt no-new-privileges:true --user 1000:1000 --read-only --tmpfs /tmp:rw,nosuid,size=256m tgd-native-cache-cleanup-acceptance
```

验证复用生产代码中的可删除条件、并发保护与文件消失检查，并确认缺少已有客户端时，实际服务不创建客户端、不对外连接。后端 `tests/test_cache_cleanup.py` 覆盖发布与恢复、取消、存储不足、校验失败、并发等待和独立重试。隔离检查不包含真实授权客户端的 TDLib 删除，实际文件删除和空间释放仍需真实环境验收；不得把成功回调直接视为磁盘空间已释放。

## 文档开发

在 `docs/` 使用 `pnpm install --frozen-lockfile`、`pnpm docs:build` 和 `pnpm docs:preview`。构建会检查公开 Markdown 的相对链接、锚点与静态输出边界；不请求外部网站检查可达性。完整说明见 [文档站](pages.md)。

## 贡献与安全

提交时附上问题说明、变更行为、测试结果与未验证边界。贡献、安全报告、许可和变更入口见 [项目说明](project.md)。测试不应要求真实凭据、读取用户数据或向 Telegram 发送消息。
