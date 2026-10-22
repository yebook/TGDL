# Telegram 媒体下载助手：实施与验证计划

版本：v0.1，2026-10-20。状态：已获批准并实施本地版本，正在审查；真实设备门槛尚未完成，结果见 `docs/verification.md`。

依据：[需求](../../../docs/project-requirements.md)、[技术设计](design.md)、[证据与实验](research/technical-evidence.md)。本文件是执行清单，不能代替代码上下文 manifests。

## 1. 交付方式与依赖

一个集成任务、M0～M6 七个顺序里程碑。每步可演示，全部完成才满足首版验收；UI 和后端共享数据契约，不以单一页面完成宣称项目完成。当前没有代码基础或可复用业务库，适合保持单仓库，不先分多个子项目/服务。

依赖链：M0 → M1 → M2 → M3 → M4 → M5 → M6。M4/M5 的只读页面可在数据契约稳定后并行完善，但 M6 必须等待下载/代理真实行为明确。M0 中需要真实凭据的实验可延至有测试环境时执行，但必须记录未测，不得跨过对应真实验收门槛。

进入执行后按项目配置使用 Trellis implement/check 子代理；实现与检查均从 curated JSONL 加载真实 spec/research。主会话负责契约、集成、用户决策和最终验证；不同 worker 划分文件所有权，不能同时修改数据库模型/共享枚举或覆盖他人改动。规划本轮不派实现代理。

## 2. M0：脚手架与最小技术验证

目标：先验证风险链路，形成可检查的基础，不先堆 UI。

- [ ] 加载 trellis-before-dev；核对现有 spec，不能将占位模板当已完成规范。
- [ ] 创建 backend 的 uv 项目、Python 3.12 锁定、FastAPI 配置/health、统一错误与脱敏、pytest/ruff/mypy 配置。
- [ ] 创建 Vue 3 + TypeScript + Vite + Element Plus 项目，提供 `typecheck`、`lint`、`test:unit`、`build` 脚本，固定 pnpm 和 Node 受支持版本。
- [ ] 固定 FastAPI/Pydantic/aiogram/SQLAlchemy/Alembic 等兼容版本，提交 uv.lock/pnpm-lock.yaml，不在运行时自动安装 latest。
- [ ] 创建官方 Bot API 源码固定 revision 的可复现 Dockerfile；构建元信息记录来源，检查许可证。
- [ ] 起草直连双容器与代理三容器两份完整 Compose、参数示例，不嵌入真实密钥。
- [ ] 用假 API 服务验证本地绝对路径共享、单文件复制和大文件不整段入内存。
- [ ] 建立单独的受控 Telegram 测试 Bot 配置入口与人工实验指南，不触碰用户现有生产 Bot 的 webhook/会话。
- [ ] 尽早运行 V-01、V-03/V-04、V-06、V-08 原型；无真实环境的部分明确标记待执行。
- [ ] 网络组件验证 `sing-box check`、TUN、HTTP CONNECT/SOCKS5、认证和 DNS，直接出口受阻时仍可访问 Telegram，故障时不直连；如果失败先修复，不假称功能完成。
- [ ] 验证 fsync、原子不覆盖发布、缓存峰值；确定本地 API 后台获取在取消/崩溃后的处理方式。
- [ ] 将已形成的实际目录、命令和错误/配置边界写入相应 `.trellis/spec/backend/` 与 frontend 指南，英文编写项目规范，不臆测尚未实现的行为。

产出：可启动基础应用、锁文件、可构建镜像、原型记录、更新后的真实规范。这里完成的是编码后的技术原型，不应把本轮文档研究当 M0 已通过。

## 3. M1：数据库、初始化和配置

依赖：M0 的结构、DTO/错误契约已稳定。

- [ ] 建立模型与第一版 Alembic migration，开启外键、WAL、busy timeout，加入单实例锁。
- [ ] 实现 bootstrap secret 一次性初始化，管理员 Argon2id、服务端会话、CSRF、登录限速、退出/改密失效。
- [ ] 实现设置读取/修改/验证，默认 30 分钟、并发 3、自动重试 3，环境覆盖只读和 revision 冲突。
- [ ] 密钥生成/加密/脱敏，读取配置永不返回完整凭据；空值保留、显式清除行为测试。
- [ ] 根目录允许列表、写入/空间探测、标记和挂载身份检查。
- [ ] 区分保存成功与 pending/restart_required，部署级参数给出可操作说明。

验收：AC-01/13/14/18/20 的基础部分；无配置不能运行 Bot/下载，但 WebUI/诊断可用。重点测试抢先初始化、并发初始化、过期会话、CSRF、环境覆盖、密钥不可读。

## 4. M2：Bot 接收与确认

依赖：M1 配置、模型、鉴权服务；M0 本地 API 适配。

- [ ] 按 bot_id 管理接入实例，getMe 校验，禁止有活动/待确认请求时更换 Bot 身份。
- [ ] 显式长轮询游标、持久化 inbox，唯一 update_id 防重；提交接收记录后再推进可确认 offset。
- [ ] 私聊与用户白名单校验，解析 video/photo/document；字段缺失不报错，不支持媒体/链接返回指引。
- [ ] 分组聚合截止持久化、成员冻结、迟到分批，确认卡片展示名称、已知大小和相对目标。
- [ ] 一次性 nonce、回调归属、服务器到期、取消、配置变化后重新确认；30 分钟默认可改，旧卡期限不变。
- [ ] 确认→任务/出箱同事务，重复按钮/重启不重复入队；确认前不调用触发完整获取的 getFile。
- [ ] `/start`、`/help`、`/status`；非白名单不泄露任务。

验收：AC-02/05/07/08/14/19/20 和 V-02。测试乱序、重复更新、聚合中重启、发卡结果未知、过期点击与确认/取消竞争。

## 5. M3：下载、存储与恢复

依赖：M2 的幂等队列、M1 根与配置、M0 已证明的文件获取路径。

- [ ] 全局文件任务调度、默认 3、动态并发、组间公平，不按组放大并发。
- [ ] file_reservation 唯一约束、已有任务关联、实际存在性验证、已存在文件路径反馈、缺失文件重下。
- [ ] 本地 getFile 长超时、源路径校验、真实阶段与流式保存进度；文件系统 I/O 不阻塞事件循环。
- [ ] 单文件类型/月路径、混合媒体组独立同目录；固定目录/命名，清洗和冲突不覆盖。
- [ ] 同盘临时文件、复制/散列、大小检查、journal、fsync、不覆盖原子发布，结果与出箱同事务。
- [ ] 运行取消、占用槽正确释放；确认 API 获取仍在继续时不能假装停止。
- [ ] 自动重试退避、限流、可重试错误分类、手动失败项新尝试序列。
- [ ] 启动恢复覆盖所有文件/数据库崩溃窗口；成功记录媒体被移走不阻止重下。
- [ ] 同盘/跨盘容量预算、并发预留、未知大小和 ENOSPC、挂载丢失，不退回其他路径。

验收：AC-03/04/06/07/08/09/10/11/15/17/20/22，V-01/V-05/V-06/V-07。用真实大于 20 MB 文件完成第一次闭环后，才宣称真实媒体下载验证通过。

## 6. M4：通知与代理管理

依赖：M1 配置、M3 状态/恢复、M0 网络原型。

- [ ] 通知出箱去重、有限重试、限流、永久失败与不确定发送结果处理；不反转文件成功。
- [ ] Bot 排队、完成、失败、取消和媒体组汇总，显示已存在项；进度编辑节流。
- [ ] 配置→受控文件→supervisor→sing-box→applied revision 握手，不执行自由 shell、不挂 Docker socket。
- [ ] 代理模式/版本状态、密码脱敏、认证、固定连通探测，实际 Telegram 链路验证。
- [ ] 安全切换暂停派发、失败保留旧配置、禁止静默直连；需重建/重启时清楚显示 restart_required。
- [ ] 网络 namespace 重建、DNS、防绕路、supervisor 异常、代理断开实验。
- [ ] 缓存容量与安全维护诊断，无可靠在线清理接口时提供明确停服维护步骤。

验收：AC-09/12/13/18/21，V-03/V-04/V-07/V-08。没有真实代理条件时保留验收未通过标记，不以 HTTP API 能访问冒充 Telegram 经代理可达。

## 7. M5：完整 WebUI

依赖：M1 的鉴权和设置、M2/M3 DTO、M4 生效状态；先统一 OpenAPI，再生成前端类型。

- [ ] 初始化/登录、任务筛选搜索分页、任务详情、设置、系统状态五类页面。
- [ ] 列表按请求/组显示，详情逐文件；已有文件、partial_success、取消中、通知异常有准确文案。
- [ ] 默认 3 秒轮询，页面切后台降频；处理慢响应、退出会话和请求取消。
- [ ] 设置显示来源/是否可改、desired/applied revision；敏感字段不回填；30 分钟/3 并发默认明确。
- [ ] 错误不丢表单输入，配置冲突可刷新重试，不伪造保存成功。
- [ ] 手机卡片/表单、键盘与焦点、文字状态、空数据与加载/失败态。
- [ ] 不添加 Web 确认、播放预览、文件删除入口。

验收：AC-01/13/14/16/18/20/21，Playwright 桌面与手机视口。UI 实施前读取项目 UI 规范并按需使用 UI 设计技能。

## 8. M6：飞牛 OS 集成与发布

依赖：M0～M5 全部完成；验证数据与凭据不纳入发布。

- [ ] 飞牛 OS 非 root UID/GID、目录挂载、CPU 架构、双模式 Compose、重建容器保留数据。
- [ ] 三任务并发、长视频、混合组、单项失败、断网、限流、代理失败、挂载丢失、空间写满、日志轮转。
- [ ] 进程在发布前后退出、API 不同步退出、通知失败和取消竞争，验证一致性及并发真实边界。
- [ ] 全面密钥/路径泄漏、初始化、CSRF、越权回调与任意路径检查。
- [ ] 备份/恢复、migration、固定镜像升级、失败回滚演练。
- [ ] 发布部署/代理/用户 ID/Bot/API ID 说明，明确云端→本地迁移、localhost、TUN 权限、缓存双份空间。
- [ ] 建立 `docs/verification.md`，逐项记录 AC-01～AC-22 的实际结果、设备、镜像版本和未测项。
- [ ] 使用 trellis-check 复核；规范与代码一致，再按项目流程记录会话。未经用户指示不自动推送、部署公网或发送外部消息。

## 9. 验证命令契约

以下命令作为实施验证契约；已实现主要脚手架并执行本地检查，实际结果见 `docs/verification.md`。工作目录标在代码块前，Windows/Linux 均使用工具本身，不依赖平台专属分隔命令。integration 标记案例也包含在默认 pytest 中；Playwright CLI 套件使用 `pnpm test:e2e` 自动构建和管理隔离服务。

backend：

```text
uv sync --locked
uv run ruff check app tests
uv run mypy app
uv run pytest -q
uv run alembic upgrade head
uv run pytest -m integration -q
```

frontend：

```text
pnpm install --frozen-lockfile
pnpm lint
pnpm typecheck
pnpm test:unit
pnpm build
```

根目录（E2E 依赖先定义好测试脚本和测试服务，真实部署参数由本地环境提供）：

```text
docker compose --env-file .env.example -f compose.yaml config --quiet
docker compose --env-file .env.example -f compose.proxy.yaml config --quiet
docker compose --env-file .env -f compose.yaml build
pnpm --dir frontend test:e2e
```

Compose 渲染不能把真实密钥输出到日志，使用 `--quiet`；`.env.example` 只有占位值，不能拿来假称真实 Telegram 接入通过。单元测试使用假 Telegram 服务与临时目录，网络实验必须显式启用并针对专用测试环境。

## 10. 必需测试与验收映射

| 测试集 | 关键断言 | 验收 |
| --- | --- | --- |
| 接收/授权/确认 | 重复更新持久化、防伪回调、超时与修改时限、链接拒绝 | AC-02/07/08/14/19/20 |
| 分组/归档 | 混合媒体同目录、迟到批次、重试不改目录、已有文件跳过 | AC-05/06/22 |
| 调度/重试 | 全局 3、降并发、不确定取消仍占槽、重试次数、限流 | AC-07/09/20 |
| 存储/恢复 | 大文件、原子不覆盖、空间/权限/掉挂载、journal 每个崩溃点 | AC-03/04/10/11/15/17 |
| 通知 | 失败不重下、恢复只发消息、组汇总正确 | AC-12 |
| 管理/安全 | 初始化、会话、CSRF、设置 revision/环境覆盖、脱敏 | AC-01/13/14/18 |
| 浏览器 | 桌面/手机、表单和慢响应、筛选分页、重试取消 | AC-16 |
| 设备/代理/部署 | 真正 Telegram 代理、持久化重建、fnOS UID/GID、兼容架构 | AC-01/18/21 |

可测试行为采用单元/集成测试；不为单纯模板或 UI 文案写镜像实现的测试。核心一致性测试必须通过失败注入证明，不只断言函数被调用。

## 11. 风险、回滚点和开始条件

高风险文件：迁移/模型、状态 services、poll cursor、文件 journal/发布、网络 supervisor、Compose 挂载、凭据处理。修改这些契约必须同时更新 DTO/前端/测试，不能由不同 worker 分别猜测。

回滚点：每个里程碑保留已通过的检查记录；数据库变更前可恢复备份；网络变更保留上一版有效配置；文件发布永不覆盖原文件。暂停调度与恢复诊断可以独立于 Bot 接收连接生效。

执行前：

- [x] 已确认产品范围与默认值，PRD 收敛，无阻塞产品问题。
- [x] 有 design.md、implement.md 和官方证据/技术验证清单。
- [x] 两个 JSONL 均有真实 spec/research，所有路径有效且无 `_example`。
- [x] 设计/计划已向用户展示并完成最终评审。
- [x] 获得该最终规划后的明确实施回复，已执行 task.py start。
- [x] 进入 M0 时刷新 coding skills/spec；需要真实凭据的实验由部署环境提供。

本地模拟与浏览器验证已执行，真实 Telegram / Linux 代理 / 飞牛 OS 实验未执行。最终发布必须满足全部验收，代码实现不能代替实机通过。

## 12. 本地交付补记（2026-10-20）

backend、frontend、deploy 与两套 Compose 已落地。已加载实际编码规范，使用 implement/check 子代理执行；项目规范现位于 backend/runtime-contracts.md 与 frontend/ui-contracts.md。

M0 的锁定依赖、health、构建配置及模拟文件链路已完成；官方源码与基础镜像已固定，但系统包未采用快照，不宣称逐字节可复现。M1～M5 已实现本地版本并进入完整审查，原清单涉及真实环境或尚未覆盖的边界仍保留未完成；M6 未通过。前端 DTO 当前集中手工维护，尚未实现计划中的 OpenAPI 类型自动生成。Bot 采用阶段性通知，不提供逐字节频繁进度推送。

本轮 Docker Engine 不可用，无真实 Bot、代理与飞牛 OS 凭据，故不执行容器构建、网络/TUN 或 NAS 验收。最终本地检查和 22 项完整场景状态统一记录于 `docs/verification.md`，任务保持 in_progress，不归档为已发布。

## 13. 本地自动验收补齐

用户确认继续开发，并明确本轮先完成本地自动验收，暂不接入真实环境。

- [x] 增加基于真实本地后端与生产前端构建的 Playwright 套件，使用隔离的临时数据库与模拟任务；覆盖任务筛选、详情、取消、重试、配置持久化与版本冲突。
- [x] 测试专用 fixture 不包含生产默认凭据、不连接 Telegram，不增加生产测试接口，不复用实际部署数据。
- [x] 增加标准库部署检查脚本，只检查服务与可选登录后的状态；输出脱敏、明确未验证项，不以 health 代替媒体链路验收。
- [x] 将新增测试纳入 CI，执行本地质量门槛并由独立检查代理复核。
- [x] 更新规范、操作命令、验证证据和会话记录；真实 V-01～V-08 仍保留待验收。

## 14. 无 Telegram 凭据的真实容器验收

用户暂缓 API 应用创建，继续推进独立于 Telegram 的部署验证。Windows Docker Desktop 已可用，应用镜像及用户 WebUI 已实际启动。

- [x] 新增显式运行的 Docker 验收工具：使用实际生产应用镜像、独立临时存储、随机测试凭据和本机动态端口，不修改当前用户服务。
- [x] 验证初始化、登录、非 Bot 设置保存及密码 6–20 边界。
- [x] 验证重建容器后管理员与设置保留，以及停服一致备份恢复；不得通过绕过存储身份校验制造通过。
- [x] 清理本次工具创建的容器及临时目录，输出固定脱敏结果；记录未覆盖的 Telegram、NAS 和代理门槛。
- [x] 经实现/独立审查后，在本机实际执行并将结果记录到验证文档与规范。

## 15. 配置离线保存与可见反馈

用户要求保存 Bot Token 和白名单不再依赖在线校验，后续可手动校验；修复内容区滚动及校验结果在页尾不可见的问题。

- [x] 设置 PATCH 仅执行本地格式、范围、版本与部署约束校验，独立保留手动在线校验接口；不将保存当作连接成功。
- [x] 保留运行时跨 Bot 的任务和通知归属保护，手动校验不保存表单。
- [x] 优化桌面和手机内容滚动，操作按钮及成功/失败反馈在当前操作位置可见，不遮挡最后字段或键盘焦点。
- [x] 执行后端回归及真实本地 API 浏览器验证，经独立审查后更新当前 Docker 应用、文档与规范。

## 16. 路径可移植性与操作区贴底

- [x] 移除公开项目文档/配置中的开发机绝对路径，统一以项目根目录为基准；容器路径和必须的宿主机挂载路径保持正确契约，以变量说明。
- [x] 设置操作与反馈区贴住内容视口底边，取消悬浮卡片留缝和穿透感，保留桌面/手机单滚动与字段焦点可见。
- [x] 复核文档命令及三个视口交互，独立审查后更新本地 Docker 服务。

## 17. 任务轮询布局稳定性

- [x] 保留自动刷新，在后台请求期间保留已有任务/空状态，避免轮询导致列表高度坍缩与页面跳动。
- [x] 验证空结果至少两轮自动更新下的桌面/手机几何稳定性及滚动位置，保留筛选/分页与过期响应保护。
- [x] 经前端检查与独立复核后更新本地 Docker 服务和规范记录。

## 18. WebUI 切换云端与本地 API

用户暂未取得 API ID/Hash，确认先使用官方云端，并要求在同一 WebUI 切换，无需维护两套应用配置。

- [x] 持久化 `bot_api_mode=cloud|local`，新部署默认 cloud，旧配置保持 local；离线保存/手动校验和模式切换活动任务保护保持一致。
- [x] 云端使用官方 HTTPS API，确认后下载，已知及未知大小均执行 20 MB 限制，流式写入现有 part/journal/散列/原子发布链路；保留本地大文件获取与恢复。迁移 0002 持久化任务获取模式，旧任务保守视为 local，恢复不跟随全局模式改变。
- [x] 云端 HTTP CONNECT/SOCKS5 代理覆盖 Bot 请求与文件流，不依赖 TUN、不在代理失败时直连；连接签名包含模式和代理，保护任务和通知归属。
- [x] WebUI 显示模式、限制与本地服务前提，默认 Compose 仅启动应用，本地 API 作为可选 profile 启动；不从应用操作 Docker，不自动执行云端 logOut。
- [x] 执行后端流式/取消/失败/边界/切换回归及前端/Compose/浏览器检查，独立审查后更新本地 Docker 应用和文档。完整后端 69 项、最后云端 28 项复测、前端 14 单测/11 浏览器案例、19 工具单测通过，镜像及隔离 Docker 8 阶段验收通过，当前 WebUI 重建后健康检查通过。真实 Telegram 下载由用户后续操作验收。

## 19. Bot 文件大小提示

- [x] 确认卡按 B/KB/MB/GB/TB 显示大小，与现有 WebUI 一致；未知大小显示未知，DTO 保持字节数。
- [x] 经相关回归与独立检查后重建当前本地应用，保留现有配置和存储。后端 70 项、Ruff/mypy24、独立定向 3 项通过；镜像重建、服务更新及健康检查通过。

## 20. 确认提示和文件名模板

- [x] 后端配置两个白名单文本模板，默认兼容现有行为，拒绝错误变量/表达式/危险文件名，保留大小可读和离线保存。
- [x] 确认卡冻结计划文件名，迁移兼容旧卡，扩展名/长度/同名安全，已确认与重试路径不受后续设置影响。迁移 0003 保留旧项空快照，已实测带旧数据升级。
- [x] WebUI 提供模板编辑、变量说明、演示预览和恢复默认，保持实心贴底操作区及配置来源/冲突/未保存保护。
- [x] 后端及前端回归、独立跨层审查、镜像和服务更新后健康检查通过，更新规范、用户说明及验证记录。后端 90/模板 20、前端 38/浏览器 12、Ruff/mypy26/ESLint/vue-tsc 均通过；生产 Docker 8 阶段及当前服务健康检查通过。真实 Bot/本地大文件/代理/NAS 验收仍待部署环境。

## 21. 设置页提示与视觉层级

- [x] 精简重复提示，分开操作帮助、关键限制、配置来源与次要部署详情，重要限制/错误保持可见。补齐数值字段来源，修复代理已应用但空消息误显示未生效的回退文案。
- [x] 优化模板编辑/预览和变量说明的宽屏及手机排版，保持纯文本预览、单滚动与实心贴底操作反馈。
- [x] 前端静态/38 单元/12 浏览器检查和独立审查通过，主与实现代理查看隔离截图后更新当前服务及规范记录。镜像构建、服务重建及匿名只读健康/初始化状态检查通过。后端/API/模板语法未改。

## 22. 确认有效期默认 30 分钟

- [x] 默认确认时限改为 1800 秒，用户设置及默认 Bot 确认提示以分钟显示/编辑；API、环境与存储继续以秒兼容。
- [x] 保留已有明确保存的时限、任意非整分钟旧值及已发卡片期限；新分钟模板变量与旧秒变量兼容，精确旧系统默认模板可升级但不得改写用户自定义。
- [x] 更新前后端、用户文档与规范，完成单位换算/模板/历史兼容回归及独立审查后更新当前本地服务。后端 98、前端单测 42、浏览器 13 及静态检查通过；最终镜像重建、服务更新和匿名只读健康检查通过。

## 23. Bot 按钮反馈与按日归档

- [x] 确认下载、取消操作提供明确友好反馈，覆盖成功、重复、过期、无效及无权限点击；反馈与实际状态一致，不重复入队或泄露其他用户信息。
- [x] 新请求日期归档目录由 YYYY-MM 改为 YYYY-MM-DD，沿用请求创建时间与归档时区；保持同组同目录、已冻结目录、旧文件与重试路径。
- [x] 同步相关文档；后端定向 32 项、前端模板 28 项、受影响静态检查通过；独立检查 12 项及 Ruff/mypy 通过，无阻塞。最终镜像重建并更新本地服务，保留原配置与文件。

## 24. 日期优先归档与消息文字命名

- [x] 新单文件直接归档到 YYYY-MM-DD/，组文件归档到 YYYY-MM-DD/<文字摘要或回退名_短ID>/，不再创建 videos/images/groups 层。
- [x] 从媒体消息附带文字提取简短名称，缺失时回退原文件名或媒体描述；任务名含短标识以区分同名消息，组内按消息顺序选择文字，冻结后保持稳定。
- [x] WebUI 任务列表/详情与 Bot 确认、入队、结果提示使用同一持久化任务名；组目录安全清洗/限长，保留旧任务路径和已发提示，不改文件名模板契约。
- [x] 更新文档/预览/规范；后端定向 70、前端模板 28、独立定向 7 项及受影响静态检查通过。迁移 0004 保留旧数据；最终镜像构建并更新本地服务，未重复完整浏览器回归。

## 25. 任务页精简、多选删除与设置消息

- [x] 精简任务页冗余引导、常驻刷新等提示，保留筛选/搜索/进度及异常反馈；支持当前页多选和单条/批量删除确认。
- [x] 删除仅移除任务记录展示，不删除媒体文件、去重索引或恢复所需元数据；仅终态可删，活动任务先取消，后端事务校验与鉴权/CSRF保护。
- [x] 提供后端 can_delete、单条及批量删除接口；删除记录不再出现在列表/详情/操作中，旧 Bot 按钮友好失效，保留文件去重有效。
- [x] 设置保存/校验等反馈使用普通短暂消息，移除底部常驻反馈区，保留冲突/未保存保护、关键字段限制与可用重新读取入口。
- [x] 同步契约/文档；后端定向 55、独立删除 22、前端单测 42、受影响浏览器 10 场景及静态检查通过；主/实现/检查代理查看稳定桌面手机截图。最终镜像重建、本地更新及只读健康检查通过，未执行用户任务删除。

## 26. 本地 Bot API 切换与应用凭据文档

- [x] 补齐 API ID / Hash 官方获取流程、表单示例与常见申请错误，区分 Bot Token/用户 ID/应用凭据及填写位置。
- [x] 写清云端转本地的服务准备、同数据挂载、停止旧消费者、云端 logOut、冷却方向与 WebUI 选择/保存/校验顺序，提供可操作方法，不能提前启动本地登录或重连云端。
- [x] 覆盖飞牛 OS / Docker 与当前 Windows 临时栈路径、直连/TUN代理区别、凭据更新重建容器及排错；不执行实际迁移或读取密钥。
- [x] 官方来源、源码/Compose/命令路径及保密性核对完成；独立检查 44 条相对链接/锚点、UTF-8/围栏/路径及示例 Python AST/Ruff/mypy 通过。不跑产品回归、不重建服务、不执行迁移。

## 27. 任务标题与完整编号

- [x] 新任务标题仅保留摘要/回退名称，取消自动追加的 #短编号；Bot 与任务 API 共用名称，保留媒体组目录防重后缀和历史冻结数据。
- [x] 列表二级信息显示完整任务编号，兼顾桌面/手机换行和稳定布局；同步模板预览与当前使用说明。
- [x] Bot 确认、入队、结果与状态提示的编号单独一行显示完整 ID，独立于标题及用户确认模板；不改写已发通知，校验长度预算与自定义模板兼容。
- [x] 后端定向 53、前端模板 28、独立后端 41 及静态检查通过；隔离桌面/手机显示用例通过，三方查看截图。最终镜像构建、本地服务更新与匿名 live/ready/已初始化检查通过，未操作真实 Telegram。

## 28. WebUI 管理本地 API

- [x] 形成 supervisor 文件交接、凭据、持久化切换和状态契约；按用户已确认方向实施，不管理 Docker。
- [x] 常驻 API supervisor：无凭据等待、限权配置交接、子进程启停/重启、状态心跳/generation、崩溃恢复；默认及 TUN Compose 和环境兼容。
- [x] 后端 API ID/Hash 安全设置、持久切换状态机、轮询/通知/领取屏障、drain/recovery、退出与冷却保护、状态及重试/重启接口。
- [x] 设置/系统页凭据、进程与Bot状态、切换步骤/等待/失败及手动操作；状态轮询不影响未保存输入、桌面/手机可用。
- [x] 定向单元/集成/进程与浏览器验收，独立审查、部署文档/规格同步；不读取用户密钥或操作真实 Bot。独立联合检查 91 项、Linux 非 root 检查 28 项、前端定向单元 5 项与浏览器 5+1 项及静态检查通过。应用与官方 API 镜像构建通过；无网络、非 root、只读隔离启动验证无凭据等待及持续心跳；当前本地栈已原地更新，应用可读取真实组件心跳，live/ready/已初始化检查通过。Windows 新控制目录使用私有 Linux 数据卷，原数据/下载/缓存挂载保留。真实 Telegram/fnOS/TUN 验收仍待用户环境。

## 29. 首次 GitHub CI 失败修复

- [x] 对照用户提供的 backend/browser 失败日志，确认删除状态检查误用非唯一标题、搜索测试匹配了两组独立 fixture；仅修正断言和查询，不修改生产逻辑或减少 CI 检查。
- [x] 删除检查使用完整任务 ID，并确认同名保留任务存在；浏览器使用完整 fixture 标题，保留精确数量、状态过滤及详情验证。
- [x] 锁定依赖同步、Ruff/mypy、后端完整 172 项（Windows 跳过 3 项 POSIX）、前端 45 单测/静态/构建、浏览器受影响 4 场景通过；删除定向 22 项、网络 11 项、脚本 19 项通过。独立复核；当前 Docker 未运行，不重复 Linux 验收，GitHub 下一轮 Linux CI 结果待推送后确认。

## 30. Telegram 请求保护

- [x] 统一持久请求节流、服务器冷却、认证暂停和网络退避，覆盖后台、手动校验、本地迁移及云文件 HTTP。
- [x] 调度和通知尊重保护，不空耗重试次数、不丢数据或释放本地不确定槽；正确处理未发出请求与真实 logout 不确定结果。
- [x] 系统/设置状态显示原因、期限和手动恢复入口；不覆盖未保存配置、不旁路限流、不声称保证账号安全。
- [x] 后端全量 217 项通过（Windows 跳过 3 项 POSIX）、前端全量 47 单测和 2 项真实 REST 浏览器检查、静态检查及独立复核通过；规格、文档与会话记录同步。未启动 Docker、更新部署或访问真实 Telegram，实机验收仍待完成。

## 31. 事件日志与历史故障排查

- [x] 只读排查 17:00–18:30 日志/任务时间线，确认 17:01 大小限制、17:06 成功与 17:10 任务；现存容器晚于故障创建，无法恢复首次断连，不重放 Bot 请求或更改用户服务。
- [x] 默认启用 /data/logs 结构化轮转日志，记录调用错误/恢复、保护及任务结果；固定字段脱敏，控制空轮询/重复事件，不改变业务结果。
- [x] 管理员有界导出接口、系统状态及 WebUI 下载入口，显示存储失败和截断提示；不读取任意路径、不泄露会话或媒体。
- [x] Windows 后端全量 250 项（6 项 POSIX 跳过）、Linux 隔离日志定向 36 项、前端 49 单测/2 项模拟 UI 浏览器及静态检查通过，独立审查/文档/规范/记录同步。未更新用户运行容器，真实 Bot 首错及封禁原因仍无法确定。

## 32. 移除 GitHub Actions（2026-10-21）

- [x] 按用户明确要求移除 GitHub Actions workflow 配置，保留本地测试脚本及应用功能；仅检查配置删除范围与差异，不重复应用回归。

## 33. 更新本地运行应用（2026-10-21）

- [x] 更新前备份停止状态的数据并保留旧镜像；原 Compose 重建应用并启动栈，所有挂载与下载清单保留。
- [x] 健康检查、原管理员状态、迁移 0007、日志落盘及独立匿名接口/生产资源检查通过；启动记录 Bot 网络异常，未将应用就绪当作真实 Bot 下载验收。

## 34. 日志保留与手动清理

- [x] 默认保留 7 天，可配置 0–3650 天（0 关闭按天清理），沿用设置 revision；启动/周期维护不依赖 Bot 在线。
- [x] 受保护的手动清理只清诊断文件和待合并事件；并发、路径、失败与过期边界有测试，不影响业务及请求保护。
- [x] 设置/系统页策略、确认清理和正常提示，API/DTO/文档同步；独立审查修复无关设置触发 TUN 重载，网络版本仅随代理内容变化。Windows 相关回归合计 82 项通过、8 项 POSIX 跳过，Linux 日志 57 项通过；前端 2 单测、6 桌面/手机浏览器及静态/构建检查通过。测试未触碰运行服务或真实日志。

## 35. 公开文档与静态文档站

- [x] README/使用/部署/开发文档重组，移除公开入口中的本机及 Trellis 会话依赖；添加用户已选 MIT、贡献/安全/变更文档。
- [x] 独立 VitePress 1.6.4 静态站构建通过：20 Markdown 文件、87 相对链接、15 公开页及锚点/base/排除/.nojekyll 检查；桌面中文搜索/跳转与手机目录/长页无横向溢出通过。预览脚本只读并显式绑定本机，避免上游 preview 忽略 host 参数。
- [x] 提供无新增仓库 Actions workflow 的 Pages 分支发布方法与套餐/访问限制；不变更可见性、不发布、不更新应用容器。规范与会话记录已同步，可审查变更保留在工作区。

## 36. Documentation Pages automation and pre-public privacy audit

- [x] Documentation-only path filters, independent build/artifact and least-privilege deployment workflow; current docs describe setup accurately.
- [x] Fresh remote snapshot 78a000a: 6 commits, 326 unique textual blobs, 269 HEAD files plus 291 pending source files audited. No confirmed real credentials; external commit email and internal dated incident/development notes remain privacy decisions before public visibility. Custom offline scanner limitations and unaudited GitHub surfaces recorded in research/remote-privacy-audit.md; no history rewrite performed.
- [x] Independent YAML/workflow permission/path/artifact review and docs build pass (20 Markdown, 89 relative links, 15 pages). Final authorization accepts existing email/internal notes; official Actions now pinned to REST-verified commit SHAs, Node 24 installs fixed pnpm 11.23.0, and deployments serialize without interruption. User configured Pages Actions source; ordinary commit/push and publication are authorized. No application/container deployment or history rewrite.

## 37. Documentation publication verification

- [x] User accepts audited email/internal records and configured Pages source; no history rewrite or visibility mutation performed.
- [x] Committed log retention separately (76ea00a), then documentation/Pages (59b4718), and pushed to origin/main.
- [x] GitHub run 39539596903 build/deploy succeeded. Live homepage, operations page and JS resource return HTTP 200 under https://yebook.github.io/TGDL/.
- [x] Independent live browser checks passed: home, Chinese search to operations anchor, mobile navigation to deployment and no horizontal overflow. Final journal records publication; parent task remains open for real Telegram/fnOS/proxy acceptance. No running app/NAS deployment.

## 38. Update local application container after Pages publication

- [x] Recorded images/mounts and private media manifest; stopped consistent backup at .runtime/local-docker/backups/before-update-20261021T035213Z/ includes app data/config/control volume and rollback image metadata. Backup database integrity/foreign-key checks passed. Windows helper bind-write failure was corrected by streaming a read-only control-volume archive; original services were restored before retry.
- [x] Built current source and recreated only app: inspected image 502e1b147880 to f14161d7c848. Supervisor original container/image/mounts and unrelated containers preserved; app mounts normalized by Destination and download counts/sizes/SHA256 unchanged.
- [x] Independent read-only acceptance passed: live/ready 200, initialized true, migration 0007, effective/default retention 7, new status DTO fields and production JS clear action. No actual log clearing, administrator session or Telegram probe; current Bot connectivity not established by application health. Recorded session and preserved parent task for real-device acceptance.

## 39. Open-source README landing page

- [x] Reorganized root README for first-time visitors: project/workflow/features, visible live docs, cloud/local comparison, three-step source-build deployment, documentation/contribution/security/MIT.
- [x] README-only editorial change checked against Compose/environment/deployment sources and independent review. docs:check passed 20 Markdown files and 97 relative links; no application tests/build/container changes needed.

## 40. Local API target-verification diagnostics

- [x] Read-only deployment diagnosis found known successful cloud logout, matching running supervisor command, and local identity network failures. User confirmed system/browser proxy only. After user enabled host TUN, a single guarded existing retry completed local identity verification in 933 ms at 2026-10-21 06:22:13 UTC; applied mode local, phase idle, with no second cloud logout. No credentials were changed. This verifies mode/identity recovery, not large-file download, fnOS or the bundled proxy stack. Working containers were not rebuilt for the message-only patch.

- [x] Classify recognized network failures only during local-target identity verification as LOCAL_API_CONNECTION_FAILED. Distinguish a running process from a verified Bot, preserve the safe actionable message on later paused reconciliation ticks, and include the existing NETWORK_ERROR classification in transition diagnostics without raw upstream text.
- [x] Parameterized timeout/SDK network regressions cover retained successful logout, unchanged cloud cooldown/applied mode, no automatic identity retry after failure or service recreation, sanitized status/events, and a successful explicitly requested retry without replaying cloud logout.
- [x] Scoped local API and diagnostic tests: 51 passed, 3 POSIX-only skipped on Windows. Ruff backend/tests/tools/scripts/deploy and mypy 29 application files passed using the existing virtual environment (uv cache access unavailable). No Docker or Telegram calls were made by this implementation check; actual connection recovery remains a deployment/network validation task.
- [x] Independent review added negative classification cases for protection deferral, 429, 401 and unknown exceptions; six affected cases passed with Ruff/mypy and diff checks. Documentation link validation passed 20 Markdown files / 97 links. Parent acceptance remains open for actual large-file downloads, fnOS and bundled TUN deployment.

## 41. Daily diagnostic schedule and unified first-release deployment

- [x] User requested daily configurable cleanup (08:00 default in application timezone), current log usage, one Compose for WebUI direct/proxy and API switching, and no unpublished-version compatibility requirements. Updated requirements/design/manifests and persistent development policy.
- [x] Backend daily scheduling uses next future occurrence on startup/policy changes, one due run with minute checks, no catch-up bursts, DST handling and independent manual clear. Added cleanup_time/timezone/total_bytes/file_count status using bounded safe fixed-file metadata reads. Log regressions: 63 passed, 8 POSIX-only skipped on Windows; Ruff/mypy passed.
- [x] Single compose.yaml includes network supervisor and fixed private-volume initializer; removed alternate proxy Compose and host control-path setup. Direct and proxy require exact fresh network acknowledgement; changed local proxy drains consumers without logging out, target migration uses frozen network snapshot before logout. Local API/recovery/cloud tests 67 passed plus 7 malformed-ack checks; quiet Compose parsing and Ruff/mypy passed.
- [x] Frontend daily time input, readable usage and configured-timezone schedule display, simple proxy copy and waiting_network feedback preserve form edits. Lint/type/build passed, six unit and eight desktop/mobile browser checks passed; two timestamp cases rechecked after final display changes. Browser isolation used Edge/alternate test port because default Windows port binding was unavailable; no production fixture or runtime data was used.
- [x] Public README/deployment/API/local-run/maintenance requirements now describe one topology and private Docker volumes; daily scheduling and first-release policy documented. Static docs build passed 20 Markdown files, 87 relative links and 15 generated public pages/anchors/exclusions.
- [x] Independent review fixed stale/nonincreasing network revisions retaining direct egress and hardened root IPC temporary files/link validation plus descriptor-based initializer permissions. Added actual HTTP waiting_network/unavailable/diagnostic projection checks. Backend affected regression 76 passed / 5 Windows skips plus two new HTTP cases passed; network Windows 13 passed / 3 skips, isolated Linux 16 passed; isolated Linux daily/log-link cases 12 passed. Final Ruff/mypy29/frontend lint/type and affected four unit checks passed. Runtime freshness gates plus transactional phase barriers and kernel firewall remain distinct; no filesystem read can atomically certify external process liveness.
- [x] Actual running containers/credentials remain unchanged. Complete real bundled TUN, fnOS, and >20 MB transfer acceptance remains separate; parent task remains open. No app workflow, release tag, remote visibility, or production deployment changed.

## 42. Update existing local deployment to unified topology

- [x] User authorized updating current containers, including recreation if necessary. Preflight found applied local/idle, no active or queued jobs, and saved database credentials; a no-network/no-data helper verified Docker VM TUN availability. Existing image IDs were retained with rollback tags before building while services remained online.
- [x] Built new app/network images while original services stayed online; rechecked no acquisitions before graceful stop. Complete stopped backup under `.runtime/local-docker/backups/before-unified-20261021T070659Z/` includes app data/settings/private control and full API cache; database integrity/foreign-key and cache hash verification passed.
- [x] Local Compose migrated to standard unified topology with existing external API control volume and original business binds. New app image `7cd880f1db8a` replaces `f14161d7c848`; API image unchanged. Network healthy and initializer exited 0; live/ready HTTP 200, initialized state and migration 0007 preserved.
- [x] Independent verification checked Docker Desktop source-path canonicalization against original mounts and actual container DB/media hashes; downloads, data/cache/control, 7 succeeded / 1 failed jobs, admin count and unrelated pgvector identity preserved. Stopped backup integrity/FK/cache hashes and rollback images verified. Deployed 08:00 / 7-day schedule, new DTO/assets and fixed-log usage verified; no live administrator session or manual Telegram probe was used.
- [x] Local API remains applied local / idle with fresh process acknowledgement. Network explicitly direct with matching revision 7 and fresh heartbeat. Post-update diagnostic event records local getMe success at 2026-10-21 07:15:54 UTC (15:15:54 Asia/Shanghai), 973 ms; no new cloud logout. Existing host TUN remains in use; bundled proxy-enabled routing, fnOS and >20 MB download still need separate acceptance. Backup includes independent-verification.json and sanitized status.json. Removed duplicated internal spec prose; no product code changes in deployment session.
# 43. Configuration backup, compatibility cleanup and unified proxy

- [x] Research active pre-release compatibility and simplest robust local/cloud proxy transport: native TDLib capability and startup-offline patch verified against pinned source; inventories recorded in research/proxy-native.md and research/legacy-removal.md.
- [x] Implement encrypted settings-only export/preview/restore and focused security/transaction tests. Independent review rejects incomplete nested proxy payloads; cloud/backup/baseline focused checks43 passed.
- [x] Implement WebUI export/restore flow and concise scope/exclusion copy. Frontend50 unit, lint/type/build passed; isolated desktop/mobile backup+native proxy status4 browser cases passed with exit0. Separate owned fixture process avoids Windows Playwright-managed teardown hang and was stopped afterward.
- [x] Remove active legacy compatibility contracts and update affected fixtures/docs/specs. New explicit schema baseline revision initial rejects old0007 without mutation. Existing running local deployment remains on its old image/database; do not update it without deliberate configuration preservation and safe offline data handling. Media and current runtime safety data untouched.
- [x] Implement native proxy and single-Compose app/API topology with no TUN/NET_ADMIN/network sidecar. Actual pinned C++ patch compiled and cached refresh verified current supervisor content; image 0ed7847c3019. Isolated initializer verifies UID/GID1000 mode0700. SOCKS5 byte limits validated before saving/logout and in supervisor; maximum HTTP Unicode credentials fit encrypted IPC.
- [x] Independent cross-layer review completed. Full backend run295 passed/11 Windows skips/1 stale synthetic-ack fixture failure; repaired exact-revision fixture and new cross-instance backup case2 passed. Further native scoped47 passed/2 skips plus boundary cases, Ruff and mypy31 passed. Frontend50 unit/lint/type/build and4 browser cases passed; final copy/byte-limit changes lint/type passed. Docs16 generated pages/91 links build passed; final documentation-only additions link checked. Parent remains in progress for real Telegram/NAS acceptance.
- [x] Actual credential-free strace transport5/5 passed, exit0, under --network none on final compiled image: SOCKS5 and CONNECT rejection each10 handshakes with zero external connect attempts; same-cache direct0 proxy handshakes/22 unreachable external attempts; invalid proxy callback and missing mode each0 connections. Public Dockerfile.verify-native and helper reproduce checks without user data. No real Telegram/authenticated proxy/fnOS/>20MB claim, no live container update or database cleanup. Changes remain uncommitted for user review; session evidence retained here.

## 44. Update current containers and clean disposable workspace artifacts

- [x] User authorized current Docker update and temporary-file cleanup. Preflight found local/idle, successful earlier cloud logout, no uncertainty, and no active/queued downloads (7 succeeded / 1 failed jobs). Preserved old inspected image IDs under rollback tags and verified cached native API image against source patch/supervisor hashes rather than rebuilding C++.
- [x] Built app image `586717e75448`; stopped app gracefully, then API/network. Complete stopped backup at `.runtime/local-docker/backups/before-native-20261021T090341Z/` includes original data/master key, environment/Compose, API cache (4 files), private API-control archive, private media hash baseline and old images. Database integrity and foreign keys passed; cache copy and media baseline hashes agreed.
- [x] A network-disabled one-shot maintenance container ran the real `initial` migration into a fresh database. Existing table columns exactly matched all current models, so every existing row was copied in dependency order instead of discarding valid task history. Settings, administrators, sessions, request safety, local runtime, poll cursors and stored-file rows were compared exactly before restart scheduling. Settings/secrets validate under the current schema; master-key hash unchanged. No deployed compatibility layer or Alembic stamping shortcut was added. Import-path setup initially failed before data mutation and was corrected with an explicit module search path.
- [x] Preserved successful cloud logout/cooldown, request safety and saved local mode. Offline maintenance issued a new encrypted native-proxy restart command using existing settings, without any cloud logout or manual Telegram validation. Current topology uses app/API/private initializer and retains all business mounts; removed only stopped obsolete project containers when Compose could not replace its earlier shared network namespace. No unrelated container or volume was removed.
- [x] Deployment verification: live/ready HTTP 200, initialized true, schema `initial`, original 7 successful / 1 failed jobs retained, media hashes unchanged, normalized mounts preserved, unrelated pgvector container unchanged. API image `0ed7847c3019`, local/idle and successful logout/cooldown preserved after normal restart. Independent read-only verification passed: actual fresh baseline, named-column business-row comparisons, media hashes, deployed backup UI assets, native topology and all cleanup targets checked. Request safety only advanced normal request pacing. Runtime health is not real Telegram connectivity or large-file/fnOS/authenticated-proxy acceptance.
- [x] Removed 82 explicit disposable targets, 5,964 files / 225,385,394 bytes (about 215 MiB): tool/test/build/browser caches, finished isolated acceptance screenshots, obsolete one-shot scripts/build logs and compiled upstream source copy. Every removal verified a resolved workspace-contained non-link target and no Git-tracked source. Preserved virtualenv/node_modules/package store, live settings/database/master key/sessions/media/API cache/logs, all rollback backups, current native build evidence/final log and unclassified earlier native/dev data. Maintenance scripts and redacted cleanup/rebuild/verification reports are retained in the newest private backup. No Docker prune, source commit or push performed.

## 45. Restore Bot intake after native API deployment

- [x] Read-only diagnosis found local/idle, proxy disabled, current credential protection ready, all 25 historical inbox records processed and all 15 notifications sent. Request start timestamps continued advancing every long-poll interval; app-to-API HTTP remained established, while Telegram transport repeatedly closed. Local cached getMe and empty getUpdates did not establish upstream connectivity. User confirmed the intended Bot chat and system-proxy-only operation.
- [x] Credential-free bounded comparison: direct TLS to the public Telegram API hostname failed with SSLEOFError, while the already running host proxy accepted SOCKS5 without authentication and completed certificate-validated TLS from the app container. No Bot request, personal login or credential change was used for this network test.
- [x] Built app image `8ee45681f237`, gracefully stopped app and copied consistent app data into `.runtime/local-docker/backups/before-bot-proxy-20261021T0928Z/data`. A network-disabled maintenance container used the existing SettingsService candidate/save contract to enable SOCKS5 on host.docker.internal:7897, revision 7 to 8. Exact assertions preserved decrypted Bot/API credentials, all request-safety rows, successful cloud logout and existing cloud deadline; scheduled ordinary restart/draining only. Original image re-tag was unavailable in Docker's image store; no prior backups or rollback tags were removed.
- [x] Recreated only app with the existing local Compose and all business mounts retained. Existing supervisor performed normal native proxy restart without cloud logout. Exact proxy revision 8 became active, local returned running/idle, and protection remained ready. At 2026-10-21 09:27:59 UTC an actual nonempty getUpdates succeeded; at 09:28:01 UTC sendMessage succeeded. Inbox advanced 25 to 26, all processed, and sent notifications 15 to 16. This proves actual message receipt/reply; it does not assert a new video download or large-file/NAS acceptance.
- [x] System and local-service views share a mode-aware label: local online means `本地 API 可用`, with an explicit upstream-connectivity boundary; cloud retains `已连接`. Focused five UI helper tests, frontend type/lint, actual Docker frontend build and diff checks passed. Deployed assets contain the new label and explanation, live/ready return 200, SQLite integrity is ok and foreign-key violations zero. Independent reviewer verifies the final deployment separately. No commit/push or additional Telegram probe.

## 46. Accurate validation feedback after proxy configuration changes

- [x] Read-only diagnosis of request `0e347ba740ac64a0` context found saved revision 9 with proxy disabled and exact fresh native-process acknowledgement. Local restart progressed at 09:35:03 UTC to verifying at 09:35:05 and failed at 09:35:26 with LOCAL_API_CONNECTION_FAILED. It was no longer waiting for a switch: direct-network identity verification had failed. Existing generic validation guard incorrectly described every non-idle phase as ongoing switching.
- [x] Preserved validation barriers and LOCAL_API_BUSY/409, but failed/blocked/logout-uncertain states now explain the actual sanitized problem and direct the operator to service actions. Active operations describe API or proxy application. WebUI disables validation with a visible reason during active/failed operations, saved local network pending state and unavailable/stale status. Initial system snapshot is retained; unsaved input and existing retry/restart/logout-uncertainty controls remain unchanged.
- [x] Scoped local API regression 35 passed, including five unchanged-state/no-identity/no-logout guard cases; seven UI helper tests, frontend type/lint, Ruff and diff checks passed. Independent source review found no issue. No manually initiated Telegram probe, retry, logout, request-protection reset or proxy change was performed.
- [x] Built final app image `e31cb3acea8f`, confirmed only 13 succeeded / 1 failed jobs, gracefully stopped app and copied consistent data to `.runtime/local-docker/backups/before-validation-feedback-20261021T0945Z/data` (revision 9, proxy disabled). Recreated only app; API container preserved. Live/ready return 200, integrity ok/FK zero, deployed backend SHA256 matches source and final asset `index-BcVJD0Yw.js` includes validation guards. User subsequently saved revision 10 with proxy enabled; live local operation is idle with exact acknowledgement revision 10. No settings were overwritten by deployment. Code remains uncommitted.

## 47. Keep mixed-media messages together across polling batches

- [x] Read-only normalized live records proved the reported two-photo/three-video tasks shared the same Telegram media_group_id. Their creation times differed by only 1.110 seconds: the previous one-second quiet window split an otherwise valid mixed group. No captions, identifiers, filenames, tokens or raw message bodies were exposed; existing completed tasks and downloaded files were left unchanged.
- [x] Shared collection policy now waits at least five seconds after first arrival and three seconds after the last new member, bounded by fifteen seconds. Same bot/chat/group collecting requests can accept delayed processed members until that cap; duplicates do not extend it. Freezing defers group cards while that bot has durable unprocessed inbox entries, but never beyond the cap. Different groups, bots and chats never merge; single files remain immediate; displayed/confirmed snapshots never grow.
- [x] Six deterministic new regressions cover the actual delayed two-photo/three-video polls, persistence/replay, exact minimum/quiet/cap boundaries, backlog with other-bot and single-file isolation, unfrozen late members and direct duplicate deadline preservation. Scoped ingestion/pipeline/archive/callback/template tests: 65 passed; final single-file-backlog assertion: six focused tests passed again. Ruff passed; mypy passed for 32 app source files. Independent source review found no issue; no real Telegram probes or messages were sent.
- [x] Final app image built as `8212ff753bdd`. An active download was allowed to finish naturally; two subsequent checks found zero active jobs before app-only stop/copy/recreate. Consistent complete app-data backup is `.runtime/local-docker/backups/before-group-collection-20261021T1000Z/data`, with prior app image `fc9c451c7a05` recorded alongside. API container/image, configuration revision 10 and all media mounts were preserved; no API restart, settings edit, task cancellation or retry was performed.
- [x] Deployment checks: live/ready 200, schema initial, integrity ok, zero foreign-key violations, 15 succeeded / 1 failed jobs. Deployed grouping/ingest/tasks SHA256 hashes match source; API container identity and native image remain unchanged. Independent post-deployment review passed: source hashes, configuration/key/admin/request protection, API identity and database integrity preserved. No commit/push performed; real forwarded-message retest remains user-driven.

## 48. Honest download status and measured transfer progress

- [x] Confirmed the existing transport boundary: local getFile waits for TDLib acquisition and exposes no intermediate remote byte counts; cloud HTTP streaming and later local cache copying already persist measured bytes/speed. No extra getFile probes, cache scanning, native API extension or C++ rebuild introduced.
- [x] Rename fetching to `下载中` across shared WebUI labels/filters. Overall saving uses neutral `处理中`; each file distinguishes cloud streaming (`下载中` / `已下载`) from local cache copying (`保存中` / `已保存`). Cancellation remains visible. Unknown acquisition progress uses an accessible indeterminate indicator, explanation and elapsed time, with no percentage; unknown total streaming still shows real bytes and speed. Groups retain completed-file counts rather than fabricated aggregate percentages.
- [x] Expose each job's existing acquisition_mode through MediaResponse and matching frontend DTO; no migration, worker lifecycle, grouping, cancellation or request-safety behavior changed. Added scoped real-worker/HTTP serialization tests for both modes and actual cloud streamed-byte persistence; added helper tests for unknown progress, stale counters, known/unknown totals, cancellation and mode-specific labels.
- [x] Backend 2 scoped integration cases, frontend 11 helper/format cases, Ruff, mypy 32 files, frontend lint/type/build and final Docker production build passed. Default uv cache was inaccessible under the sandbox, so checks used the existing project virtualenv. No additional real Telegram request was initiated for testing.
- [x] Built app image `c0c00be23376`; preflight found no active/pending jobs and local/idle. Gracefully stopped only app, backed up complete consistent app data to `.runtime/local-docker/backups/before-progress-20261021T100730Z/data`, then recreated only app. Settings revision 11, all settings/admin/request/item/job/stored-file row hashes, 20 succeeded / 1 failed jobs, initial schema, native API identity/mounts and unrelated pgvector preserved. Live/ready 200, integrity ok/FK zero. The first verification compared mount array order; sorting by destination verified identical actual mappings without a second deployment. Old image content was unavailable for rollback tagging in Docker Desktop; stopped data backup and current source retained, no prune or store repair attempted. Independent deployment review passed: five deployed modules match source, new measured/indeterminate progress assets present, original settings/key/admin/protection/logout state preserved. No commit/push.

## 49. Documentation consolidation, source publication and progress research

- [x] User authorized documentation cleanup followed by commit/push, and requested discussion only for local Telegram acquisition progress. Public README/changelog/usage/FAQ/API/operations/verification now cover current grouping, proxy/default-route semantics, local availability versus upstream connectivity, actionable validation and measured download/save stages.
- [x] Documentation build passed: 21 Markdown inputs, 93 relative links and 16 public pages/anchors/base/exclusion checks. Independent final integration audit found no blocking defects; prior scoped application/native verification remains sufficient without repeated broad tests.
- [x] Redacted privacy audit reviewed 314 tracked/eligible candidate files; nine matches were verified synthetic test Tokens or example credentials. Runtime configuration, databases, media, logs, cache and private backups remain ignored. Parent remains in progress for pending release/device acceptance.
- [x] Pinned upstream research confirms TDLib updateFile downloaded_size, exact/estimated totals and active/completed state. Proposed bounded, Bot/generation-scoped read-only snapshot method is documented in research/local-download-progress.md. No native progress endpoint, observer or application behavior was implemented this round.

## 50. Implement local API acquisition progress

- [x] User authorized implementation on 2026-10-22; refresh prior pinned-source research and freeze the private native/backend/UI contract before cross-layer changes.
- [x] Extend native already-active local-only routing, bounded updateFile snapshots and supervisor generation environment. Actual pinned C++ build passed, image `tgd-bot-api:local-progress-test` (`e291012af369`). Disposable network-none verifier passed production-cache alias/isolation/capacity/TTL/retry cases, plus five absent-client inputs (valid/malformed/empty/17 items/oversize), all404 with no Client directory and zero outbound connects. Existing five native-proxy strace regressions passed. Supervisor20 passed/2 Windows skips, including generation prepared before spawn and failed spawn not publishing it. Authorized real-Telegram native serialization/bytes remain pending separate acceptance.
- [x] Add bounded backend read transport, guarded ephemeral observation registry, worker lifecycle ownership and nullable per-file DTO. Scoped progress/pipeline/cloud regression61 passed, final local suite21 passed and final hardened transport case passed. Covers timeout/512KiB/no redirects/no environment proxy/backoff, unchanged protection, single getFile, late observer, cancellation/shutdown uncertainty, ownership/generation/ACK freshness and counter reset. Ruff and mypy33 source files passed.
- [x] Add measured local acquisition UI, exact versus estimated/unknown total behavior, cache-hit/saving/cancellation presentation. Frontend62 unit tests, lint/type/build and two isolated desktop/mobile Edge browser cases passed, including actual Vue detail polling. Temporary browser config/server removed; no real media or credentials used.
- [x] Independent check, current code-spec/public docs synchronization, and application/native image validation completed. No live deployment performed; existing data/configuration/media/protection untouched, no active download interrupted or logout replayed.
- [x] Independent cross-layer reviewer found no blocking defects, independently reran backend/frontend lint and type checks. Seven-section native/worker/DTO contract and public behavior/boundary/reproduction docs synchronized. Final docs build passed21 Markdown/96 links/16 pages. Actual application image `tgd-app:local-progress-test` built successfully (Docker inspected image `99ad6cdf77d6`, BuildKit config digest `f6488d607d3e`); no-network/read-only disposable container imports progress module/DTO successfully. A first smoke command had a PowerShell quoting error before import and was corrected. Live containers/configuration/data remain untouched. Parent remains active for real Telegram/large-file/fnOS acceptance. Feature changes are uncommitted and unpushed.

## 51. Deploy local API progress update

- [x] User explicitly authorized updating the current local application and Bot API containers. Reuse verified images; preserve settings, media, database/key, private control/cache and Telegram protection.
- [x] Starting TGDL containers were stopped; durable preflight found20 succeeded/1 failed and no outstanding acquisitions, local/idle revision11. Stopped full backup `.runtime/local-docker/backups/before-local-progress-20261022T014822Z/` includes app data/key/env/Compose, API cache and private control archive; integrity/FK and cache hashes verified. Recreated only app/API using inspected images app99ad6cdf77d6 and APIe291012af369, with existing mounts retained. Deployment source verification matched all backend Python and supervisor files. Docker Desktop equivalent mount source spellings were normalized in checks, with no second recreation.
- [x] Independent final deployment verification passed against original full stopped backup: actual app599d184b44b1/APIe291012af369, source/supervisor/dist, health200, integrity/FK/schema, business/admin/key/media/mount/control-key/protection/logout/cooldown preservation. Native child environment generation matches fresh desired/applied command/revision/proxy; runtime idle/local/no pending command. Historical persisted generation retained correctly; current-generation provider/ensure_client regressions independently passed. Sanitized independent-verification.json stored in original full backup. No manual Telegram probe or real-progress claim.
- [x] Initial deployment health/data/source checks passed, but independent validation caught stale persisted applied_generation after same-command API restart: telemetry rejected the fresh child forever. All other preservation checks passed. Fix current-generation telemetry proof in source/regression rather than modifying live database or issuing Telegram probes; keep final acceptance open until corrected app redeployed.
- [x] Corrected progress guard to use verified current runtime client plus fresh supervisor child/command/revision/proxy/current-config and idle/local barriers; historical persisted generation is no longer incorrectly treated as current. No durable state adoption or additional probe. Scoped62 backend regressions passed, independent restart/guard2 passed, Ruff/mypy33 passed. Added POSIX0600 permissions to fake supervisor ACK fixture; synchronized lifecycle contract.
- [x] Rebuilt corrected final app image and verified actual Docker ID599d184b44b1. App-only update after fresh stopped data/env/Compose backup `.runtime/local-docker/backups/before-progress-generation-20261022T015742Z/`; original full backup retained. Native container/imagee291012af369 left unchanged on second update. Health200/current backend source hashes and exact business/runtime state preservation passed; normal pacing advanced only. Final independent verification passed; real Telegram progress remains user-driven acceptance.

## 52. Publish completed local progress implementation

- [x] User explicitly requested committing and pushing current completed work before UX overhaul. Final scoped native/backend/frontend/browser/deployment reviews remain passing; documentation rebuilt21 Markdown/96 links/16pages. Reviewed39 pending feature candidates with no credential-pattern findings; runtime/env/backups/evidence ignored. SSH origin/branch verified. UX research/product changes excluded.
