# 开发指南

后端位于 `backend/`（Python 3.12、FastAPI、SQLite），前端位于 `frontend/`（Vue、TypeScript）。部署服务为单实例，不运行多个应用 worker。

## 工具与依赖

当前仍在首版开发中，不为未发布版本保留兼容分支；功能修改同步更新当前契约、测试与文档。不兼容的旧测试数据可以清理，优先保持当前设计简洁；清理前明确范围，媒体文件不随测试数据清理，正常运行中的下载所有权与 Telegram 安全保护不能绕过。正式发布后再制定版本兼容和迁移策略。

数据库当前使用单一 `initial` 初始结构，不提供早期开发修订的自动升级。遇到不支持的开发数据库时，先停服、备份并明确要保留的配置及文件，再重建测试数据库；不要只修改版本号强行启动，也不要通过清理运行状态绕过不确定获取或 Telegram 冷却。配置导入只恢复设置，不恢复这些安全状态。

安装 Python 3.12、uv、Node.js 24 和 pnpm 11.23.0。使用提交的锁文件，不手动修改锁定版本。文档有独立依赖包，不加入应用镜像或前端依赖。

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

## 原生代理隔离验证

修改本地 API 源码补丁时，先编译实际镜像，再运行专用传输检查。以下命令在项目根目录执行，不挂载应用数据，不填写真实凭据；专用检查镜像安装 `strace`，只在断网容器中验证。

```sh
docker build -f deploy/bot-api/Dockerfile -t tgd-bot-api:0.1.0 .
docker build -f deploy/bot-api/Dockerfile.verify-native -t tgd-native-transport-acceptance .
docker run --rm --network none --cap-drop ALL --cap-add SYS_PTRACE --security-opt no-new-privileges:true --user 1000:1000 --read-only --tmpfs /tmp:rw,nosuid,size=256m tgd-native-transport-acceptance
```

检查脚本要求只有回环网卡，通过假 SOCKS5 / HTTP CONNECT 服务和系统调用记录验证：代理拒绝时没有直连尝试、同一缓存关闭代理后使用默认连接、无效代理或缺少模式时不启用网络。仅输出汇总，不输出请求、凭据或原始子进程日志。`SYS_PTRACE` 仅用于该隔离测试，正常部署无需此权限。

该检查不证明真实代理认证、Telegram 大文件下载或飞牛 OS 已通过验收。

## 本地下载进度隔离验证

进度扩展读取现有 TDLib 下载事件的内存快照。修改后使用相同原生镜像构建专用检查镜像：

```sh
docker build -f deploy/bot-api/Dockerfile.verify-progress --build-arg TGDL_API_IMAGE=tgd-bot-api:0.1.0 -t tgd-native-progress-acceptance .
docker run --rm --network none --cap-drop ALL --cap-add SYS_PTRACE --security-opt no-new-privileges:true --user 1000:1000 --read-only --tmpfs /tmp:rw,nosuid,size=256m tgd-native-progress-acceptance
```

该检查覆盖生产缓存的别名、容量、完成保留与重试边界，并向实际原生服务发送缺少已有 Bot 客户端的进度查询，确认不会创建客户端或发起对外连接。后端的 `tests/test_local_download_progress.py` 覆盖进度传输、状态投影和下载生命周期；页面测试使用合成进度。真实 Telegram 的字节增长、速度和缓存命中仍需单独验收。

## 文档开发

在 `docs/` 使用 `pnpm install --frozen-lockfile`、`pnpm docs:build` 和 `pnpm docs:preview`。构建会检查公开 Markdown 的相对链接、锚点与静态输出边界；不请求外部网站检查可达性。完整说明见 [文档站](pages.md)。

## 贡献与安全

提交时附上问题说明、变更行为、测试结果与未验证边界。贡献、安全报告、许可和变更入口见 [项目说明](project.md)。测试不应要求真实凭据、读取用户数据或向 Telegram 发送消息。
