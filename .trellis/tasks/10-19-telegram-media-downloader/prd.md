# Telegram 媒体下载助手：需求与实施

## 构建和镜像部署优化（用户授权实施，2026-10-23）

- 普通部署支持直接拉取项目生产镜像，避免 NAS 编译依赖和 TDLib；保留可实际使用的源码构建入口。镜像首次发布前如实说明尚不可拉取，不虚构已发布版本。
- 允许新增与 Pages 独立的手动镜像构建发布流程，先支持 linux/amd64；不自动部署 NAS，不因普通提交触发镜像发布，不在本次实际发布或修改运行容器。
- 镜像对需要明确、可追溯、可固定和回退；保留定制 API 的代理、进度、缓存清理功能及现有目录/权限边界。
- 配置变更无需构建镜像；应用更新与 API 编译解耦；源码构建增加包缓存和编译缓存，可配置编译并行度，默认保守值。
- 同步 README、文档站、版本检查和有影响的验证，按范围开展小规模回归；不承诺未实测提速比例。
- 用户追加明确：将编译后的原生 Bot API/TDLib 独立复用，管理脚本和应用改动不得依赖缓存幸存才能避免重编译；只有原生输入、工具链及相关依赖变化时重建原生产物。保留完整源码构建方式，普通用户运行服务数量不增加。
- 原生产物需要来源和指纹校验，自动发布及手动指定旧镜像均不得在原生代码更新后静默复用；产品数字版本与原生输入身份分开记录，明确 Bot API、TDLib 固定提交。

## 可配置 UID/GID（用户要求，2026-10-23）

- 解除 UID/GID 为 0 的限制，支持 `0:0` 和分别为 0 的组合，默认仍为 `1000:1000`。
- 保留非法数值校验、私有控制目录所有权及权限检查、容器现有权限限制；同步当前部署说明。
- 使用隔离启动检查验证，不修改现有运行容器和数据；本增量不自动提交推送。

## 默认访问方式简化（用户要求，2026-10-22）

- 移除WebUI仅部署主机可访问及必须固定局域网IP的默认配置，标准Docker端口发布后使用部署主机地址和端口访问。
- 不再要求用户设置专门的宿主IP绑定参数；保留可配置WebUI端口。同步README、部署与入门/FAQ等说明及注释配置块。
- 仅修改标准部署模板与相关文档，不改内部Bot API端口、应用登录、测试/文档预览隔离或当前运行容器；保留已有用户改动。

## README快速部署与文档站链接（2026-10-22）

- 参考2c5aa92版本根README快速部署的三步：准备配置与目录、构建并启动、初始化并验证，完善当前版本对应内容。
- 准备步骤提供文档站参数设置章节链接，保留权限/内网绑定/最小云端首次验证等必要步骤。
- README中所有用户文档链接统一指向文档站对应页面/锚点，源码、贡献规范、安全策略与许可链接按其真实用途区分。
- 不修改应用与运行部署；核对目标页面和锚点，不自动提交推送。

## Docker部署文档整理（用户要求，2026-10-22）

- 文档站Docker部署页将必需材料置前：源码取得与必留文件/可删除开发目录、Docker环境、专属Bot Token及自己的数字Telegram用户ID。以最少参数、云端小文件首次启动为主，应用凭据与大文件操作后置。
- 分别提供完整`.env`与`compose.yaml`代码块，每个参数或配置项下方用注释说明用途及是否需要修改，保持可复制、与真实配置语义一致。
- 明确源码构建依赖、可清理开发内容与绝不能当临时文件删除的数据，避免误删导致构建失败或数据丢失；使用相对路径，不虚构已发布镜像。
- 本增量仅修改用户文档及必要的文档规范，检查链接/构建与示例一致性，不改应用、部署拓扑或运行容器，不自动提交推送。

## 切换可恢复性补充（用户要求，2026-10-22）

- 优先确保连续保存、代理/API切换流程可完成；请求失败、刷新失败或应用重启不得造成永久前端锁定。
- 自动切换步骤须有合理时间边界及明确失败状态；正常下载排空和Telegram强制冷却不能被超时机制强行越过。
- 卡住时普通设置仍可访问，连接分区提供明确原因及适用的重新同步、重试或恢复操作；恢复不得并发启动旧操作、清除上游冷却或绕过退出结果不确定保护。
- 覆盖正常完成、超时、状态刷新失败、重新进入页面、重启及恢复后再保存的定向回归，再更新本地应用。

## 当前增量：面向使用者的体验与数字版本（已确认实施，2026-10-22）

本节定义当前改版；既有下载、代理、保护和文件安全能力继续保持。此前下载进度工作已提交推送，用户已确认本增量方案，当前正在实施及验收；本轮尚未改动运行配置或部署。

- 目标：用户可以直接理解能否下载、去哪里配置，以及出问题时如何处理；日常页面不呈现开发过程或内部实现细节。
- 移除独立系统状态页及导航，仅保留下载任务、设置。登录后顶部持续显示精简连接状态、活动/等待数量和保存位置剩余空间；异常、限流等待、恢复阻塞及状态过期始终可见。正常云端模式下未启用本地服务不是故障。不得把本地 API 响应解释为 Telegram 实际消息连通。
- 设置按“连接 Telegram”“下载与保存”“提示与命名”“维护与备份”组织，桌面有紧凑分区导航，手机可顺畅切换。Bot、API 模式、按需凭据与代理按配置流程相邻；备份恢复、日志占用/导出/清理、API 重启/恢复、连接重试仍可在普通模式找到。
- 开发者/调试开关默认关闭，仅当前登录会话生效；开启后显示配置来源详情、版本修订、心跳、进程及操作标识等排障元数据，不增加调试请求、日志级别或危险配置，不显示保存的密钥。关闭开关、切分区和状态刷新均不丢表单输入。环境固定字段即使隐藏来源详情，也必须保留只读原因。
- 使用简明中文，帮助逐层展开，移除重复长说明及装饰性英文小标题；保留影响选择的云端20MB限制、代理网络含义、密钥留空保留、未保存/冲突/失败等必要信息。存储与缓存问题、未明确的退出结果及恢复动作不可藏在调试模式。
- 文档以安装部署、首次下载、日常设置、大文件、网络问题、备份和排障为顺序；开发/验收/Pages编写归入次级折叠分组。同步所有原系统页入口、文案及新版操作路径；保留可执行部署前提、相对路径、安全边界和真实设备待验收说明。
- 用户已确定版本仅使用数字，不使用alpha/beta/dev后缀或阶段标识。当前版本0.1.0；补丁0.1.1、功能更新0.2.0、首次正式版本1.0.0。统一版本来源与校验，界面、接口、构建元数据和文档显示一致。
- 更新日志采用“未发布”与带日期的版本条目。0.1.0说明首版开发阶段当前能力与本次改版，不捏造以往发布、Git标签或GitHub Release；后续变更按新增/改进/修复归类。文档站提供更新日志入口，设置提供版本与对应记录链接。

验收：关闭调试时可完成配置/下载管理/备份/日志及必要恢复；状态轮询仅一个共享来源，不跳动或覆盖输入；桌面与窄屏无横向溢出、键盘焦点不被顶栏/保存条挡住；401/409、离开未保存确认、密钥语义、云端/本地切换与恢复屏障保持；数字版本与日志跨界面/接口/文档一致，文档链接及输出边界检查通过。按影响运行定向回归和浏览器验证。

本轮不改变下载算法、数据模型、Telegram调用策略、权限或部署拓扑；不添加自动检查更新、自动升级、公开镜像、发布标签或迁移兼容层。容器更新和提交推送在实现验收后按用户指令处理。

### 顶栏与部署表述增量（用户确认，2026-10-22）

- 数字版本及更新日志入口放左下角；手机保持紧凑可达。
- 运行状态并入与退出登录相同的最顶部横栏，取消该栏的页面标题；页面正文也取消重复的总标题，保留有意义的设置分区标题和任务操作。
- 顶部空间显示剩余容量，不显示存储名称；仍依据实际保存目录计算，不改变存储选择语义。
- 项目公开内容及部署文档统一通用Docker部署，不再针对飞牛OS做专门定位。保留NAS作为通用使用场景，真实Docker宿主机、网络及大文件验收仍必须诚实标注。

### 顶栏风格、切换等待与目录命名（2026-10-22）

- 顶栏以统一中性样式、轻量状态标记和细线刷新图标呈现；保留可辨识状态、键盘/触摸访问与自动轮询不闪动行为。
- 保存配置/切换连接时，只有实际在等待进行中的下载才显示下载等待。等待消息轮询/通知结束、释放旧连接等准备阶段不得误称等待下载；不能以文案修复为由跳过安全锁或退出/冷却/认证保护。
- 新建多文件目录采用消息名称，不自动附加任务编号；同名冲突仅在必要时用普通序号区分并防覆盖。现存任务路径与已下载文件不移动，自定义文件名模板不被重写。

### 连续连接配置操作保护（2026-10-22，用户同意实施）

- 在API/代理切换期间，前端提前锁定连接相关输入并说明原因，下载/命名/日志等无关设置仍可编辑保存；失败后允许修正并保留显式恢复入口，状态过期不得冒充已完成。
- 云端代理修改也串行处理：受理一次保存后，连接操作完成前拒绝新的连接变化，不使用隐式“最后一次覆盖”队列。关闭代理时修改未生效的地址等字段不得引起无意义重连。
- 不发布过时连接尝试的Bot身份/正常状态；保存HTTP结束至状态轮询更新之间也不得短暂开放重复连接修改。
- 保留离线保存、配置版本冲突、下载所有权、消息收尾、云端退出/冷却/不确定结果及持久请求保护。云端代理重连不执行logOut或本地服务启动。

## Goal

为个人自托管用户提供 Telegram 视频/图片确认下载、保存到本地或NAS、Bot 反馈与 WebUI 管理的完整工具，统一通过Docker部署。需求与设计已确认，当前已实施本地版本，真实设备验收仍待完成。

## Requirements

### First-release simplification increment (2026-10-21)

- User requests configurable daily diagnostic cleanup time, default 08:00 in the application's configured timezone; keep seven-day retention and capacity rotation. Show current diagnostic-log disk usage and file count. Daily cleanup is independent of Bot availability; schedule the next future daily occurrence at startup or policy change rather than repeatedly scanning on restart/status reads. Manual clearing remains available.
- Use one standard compose.yaml for direct and proxied operation. Proxy enable/disable and cloud/local API switching belong in WebUI; remove the separate compose.proxy.yaml deployment path. Keep truthful desired/applied state, progress, safe manual recovery, existing downloads and request protection. Local proxy must cover the official API's Telegram transport, without silent direct fallback while proxy is enabled.
- Automate private control-volume setup where possible; do not require users to prepare Linux permissions by hand on Docker Desktop. Explain unavoidable Linux-container/TUN host prerequisites clearly.
- User explicitly waived pre-release version compatibility. Simplify obsolete deployment paths and compatibility branches affected by this change; do not reset test data, credentials, download ownership, logout uncertainty or durable safety state. Compatibility policy will be reconsidered after a formal release.
- Acceptance: configurable HH:MM time saved offline; no scheduled age cleanup before next daily time; one run when due; safe fixed-file byte usage updates after rotation/manual clear. One Compose parses with blank Telegram credentials; direct/proxy selection works through explicit acknowledged network commands; local API activates only after target network and Bot verification. Targeted automated backend/frontend/network/deployment checks, with real fnOS/TUN/download limitations recorded separately.

权威文档：[需求与实施准备文档](../../../docs/project-requirements.md)。

- 已确认需求见第 2 节，首版范围及排除项见第 3 节。
- 功能与一致性要求见第 5～7 节，部署和非功能要求见第 8～9 节。
- 技术架构与契约见 [design.md](design.md)，顺序里程碑与验证见 [implement.md](implement.md)；技术设计已获确认并实施本地版本。
- 已确认首版只处理实际发送/转发的媒体（含频道视频消息），暂不支持消息链接下载；见 U-08、AC-19。
- 已确认大于 20 MB 的视频下载是首版必须能力，标准部署配套本地 Bot API 服务；见 U-09、AC-04。
- 已确认媒体组整组确认、逐项下载、混合媒体同目录归档及结果汇总；见 U-10、AC-06/22。
- D-01～D-09 已统一确认，主要使用飞牛 OS，提供代理配置与内网 WebUI；默认确认 30 分钟（后续用户调整）、全局文件并发 3，均可配置。
- 后续确认：增加官方云端 Bot API（仅 Bot Token、下载不超过 20 MB）作为可立即使用的入口；在同一 WebUI 中切换云端/本地。既有本地大文件能力保留，云端限制不替代大于 20 MB 的首版验收门槛。标准部署配套常驻 supervisor，应用凭据与 API 子进程由 WebUI 管理，WebUI 不管理 Docker；旧外部 API 部署仍可继续使用。

## Managed local API increment (2026-10-20, user approved)

用户已确认将 API ID / Hash 配置、云端/本地切换移入 WebUI。日常操作不停止应用或重启容器；修改凭据允许重启容器内 API 子进程。WebUI 持续可访问，Bot 可短暂停顿。设置显示本地 API 进程状态和实际 Bot 连接状态、切换步骤、等待下载、冷却与安全失败原因；可手动重试或重启 API。旧部署允许一次性更新服务结构，后续不需手工 Compose/logOut。

验收：凭据离线保存、Hash 不回传；无凭据 supervisor 常驻等待；切换先阻止新领取/轮询并等待既有获取结束，再按官方要求退出云端和启动本地，验证后才恢复。状态持久化并在应用/进程重启后保守恢复，未知退出结果不得自动重试 logout 或回切云端。手动重启同样保护下载与不确定槽。云端10分钟限制持久记录，在线校验不得绕过切换或冷却。前端自动刷新状态且不覆盖未保存配置，失败提供明确操作；不接入 Docker socket，不提供任意进程控制。现有外部 API 部署保留兼容；直连/TUN 拓扑由一次性部署选定，未承诺在线切换宿主机网络能力。真实 Telegram/fnOS/TUN 验收继续单列。

## Acceptance Criteria

请求保护增补（2026-10-20，用户要求尽量降低调用风险）：云端和本地共同限制请求突发，遵守 Telegram 返回的等待时间；认证失效停止自动探测，网络异常逐步退避。冷却与凭据暂停在重启、切换代理/模式和重建客户端后保留；所有实际请求路径及手动操作均不得旁路。WebUI 显示等待/暂停原因和可恢复操作，不增加日常必配参数，不宣称保证不封号。保护只停止新的上游请求，不丢弃任务、通知或已有文件，不强行释放本地不确定下载所有权。验证使用假服务、临时时钟/存储，不接触真实 Telegram 或用户密钥。

最新管理调整：任务页精简冗余提示，支持当前页多选、单条/批量删除；默认仅移除已结束任务记录，保留媒体文件及必要去重/恢复数据，活动任务需先取消，操作需确认。设置页保存/校验结果使用普通短暂消息，不在底部常驻，保持未保存输入和配置冲突保护。

最新归档调整：新下载以日期为第一层（YYYY-MM-DD），单文件直接放日期目录，媒体组在日期下单建组目录，取消视频/图片/组分类目录。优先使用媒体消息附带文字的简短摘要作为任务名、组目录名，缺失时回退文件名或媒体描述。任务标题不自动追加编号，列表二级信息和 Bot 提示独立行显示完整任务编号；组目录保留短标识防重。Bot 与 WebUI 使用一致任务名。已有任务、提示与文件路径保留，文件名模板功能不变。

后续增补：Bot 的“确认下载”和“取消”按钮提供友好操作反馈，过期后点击须说明已过期及重新发送媒体的处理方式；重复点击不得重复创建下载，提示应与实际状态一致。新请求的归档日期目录精确到日（YYYY-MM-DD），已有文件和已冻结任务/重试路径保留。后续修改按影响范围优先小规模回归。

后续增补：WebUI 可配置 Bot 确认提示模板和保存文件名模板，提供变量说明、示例预览与恢复默认；默认兼容现有行为。仅支持白名单文本变量，不执行模板代码。大小使用可读单位，文件扩展名与归档目录继续由系统安全处理。新确认卡冻结计划名，后续配置修改不改变已有任务/重试路径，重名不得覆盖文件。

- [x] 归纳用户目标、使用流程、部署与管理要求。
- [x] 区分已确认需求、建议与待确认决策。
- [x] 核实参考项目 README 和 Telegram 官方下载限制。
- [x] 补齐状态、恢复、权限、存储与异常处理边界。
- [x] 提供可观察的首版验收场景。
- [x] 确认权威文档第 15 节 D-01～D-09 的范围与产品决策。
- [x] 完成技术设计、实施拆分、官方约束调研和子代理上下文 manifests。

产品验收以权威文档 AC-01～AC-22 为准，逐项对应 implement.md 第 10 节；以上勾选表示规划产物完成，不表示产品测试已通过。

## Diagnostic retention and public documentation increment

2026-10-21 后续确认：只为文档站增加自动构建和 Pages 发布，不恢复应用检查、镜像构建或 NAS 部署。公开仓库前审查最新远程分支、标签及可达历史中的凭据和个人信息；报告不回显敏感值。不擅自更改仓库可见性。

随后用户明确接受现有提交邮箱和内部研发记录的公开范围，并已在 GitHub 将 Pages 来源设置为 GitHub Actions；授权完善流程、提交推送并尝试文档发布。无需重写历史。应用/NAS 容器仍不自动更新。

2026-10-21 增补已确认：诊断日志可配置按天自动清理，默认保留 7 天；0 表示关闭按天清理，容量轮转始终有效。WebUI 手动清理需确认，只清诊断日志，继续记录新事件，不影响媒体、任务、凭据及请求保护。整理公开使用与开发文档，构建可用于 GitHub Pages 的静态文档站；不重新引入仓库 GitHub Actions，不更改仓库可见性或冒充文档已发布。用户明确选择 MIT 许可。

后续确认：排查 2026-10-20 17:00–18:30 的 Bot 异常；现存记录不足时增加默认启用的持久、轮转、脱敏诊断日志，记录 Bot 调用结果、请求保护、任务结果和服务生命周期，系统页可导出供排障。不得记录凭据、原始异常/URL、媒体正文/标题/文件路径、用户或聊天个人信息；不得通过重放真实请求排查封禁。

## Out of Scope

不处理消息链接、个人账号登录、频道自动监听、转码、媒体播放或媒体文件删除；已结束任务记录可单条/批量移除。用户指定当前先完成本地自动验收，不接入真实 Bot、NAS 或代理环境。

## Risks and Technical Verification

无阻塞产品问题。技术验证见 [research/technical-evidence.md](research/technical-evidence.md) 的 V-01～V-08；对应真实大文件、混合组、代理链路、飞牛 OS、后台获取、缓存与目标架构实验仍未执行。本地模拟、故障注入和浏览器结果见 [验证记录](../../../docs/verification.md)，不代替真实验收，失败不得默默删减首版能力。

## Artifact Status

需求 v1.0 与技术设计/实施计划已获用户确认，已执行 task.py start，状态为 in_progress。后端、WebUI、部署实现按 ownership 分派给 Trellis 子代理，随后进行集成与独立检查；真实 Bot/NAS/代理实验仍需部署环境，不能把文档研究或模拟测试标为真实验证。

## Runtime image code permissions (2026-10-23)

- Fix startup failures reading `/app/entrypoint.py` and `/usr/local/lib/tgd-api-supervisor.py` when restrictive NAS checkout permissions are preserved by Docker COPY.
- Package code, dependencies and static assets with explicit runtime-readable/traversable permissions for configured UID/GID; preserve executable dependency files. Never change mounted data, secrets or media permissions as part of this repair.
- Verify restrictive Linux source permissions and representative non-root/mixed IDs without real Telegram, production container mutation or a full native compile. Runtime-only packaging fixes must not invalidate the native source fingerprint.

## First Docker Hub image publication (2026-10-23)

- User configured GitHub Repository secrets `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` and authorized completing, committing/pushing and attempting the production image workflow, including the startup permission fix. User chose to click Run workflow personally because this workstation has no GitHub Actions authentication.
- Compile once on GitHub, retain independently reusable native artifacts and GHCR build/cache, mirror the same manifests to Docker Hub, and provide Docker Hub digest-pinned deployment files with GHCR alternatives. Do not update NAS or local running containers.
- Preserve immutable-tag collision checks, interrupted-run recovery and provenance; verify real-image permissions/offline smoke before version tags, and anonymous Docker Hub pulls before claiming publication usable. Three Docker Hub repositories must be public and the Token must permit writes; never expose credentials in source or logs.

## Git-bundled native runtime (2026-10-23)

- User requests committing a feasible-size compiled native artifact so source downloads need no TDLib compile. Measured existing verified production binary 45,541,176 bytes (43.4 MiB), gzip 15,469,457 bytes (14.8 MiB); TDLib is linked into telegram-bot-api.
- Bundle deterministic linux/amd64 precompiled archive, licenses and source/checksum metadata as ordinary Git files (not LFS). Default source builds and image publication use the verified bundle, while preserving an explicit full-native compile/update path.
- Establish actual binary provenance from the original compiled builder, not synthetic test metadata. Check pinned upstream commits, all native patch/header inputs and the exported production binary; record reuse honestly.
- Changed native code/toolchain inputs, corrupt bytes, missing metadata or unsupported architecture must fail clearly without silently compiling or accepting stale artifacts. Runtime-only edits do not invalidate the native identity. No live deployment or Telegram interaction is authorized.
## Saved local media cache cleanup (2026-10-23)

- After successful durable final publication, remove that media's local API cache through TDLib; preserve API authentication/runtime data. Cross-volume copying remains necessary, but retained double storage must not be normal successful behavior.
- Persist cleanup work atomically with job success, recover after restart, and retry bounded failures independently without redownloading saved media. Never clean on failed/cancelled copies or before durable publication.
- Exclude concurrent local readers while requesting cleanup. The private native method must use only an existing authorized Client, offline file lookup and TDLib deleteFile, validate generation/file identity/path, reject active downloads/uploads and protect against new getFile during deletion. Confirm the actual original cache path disappeared, not merely a successful TDLib callback.
- No production cache sweep, runtime-data reset, real Telegram probe or container update is authorized by this increment. Document temporary double space and pending cleanup failures; use targeted isolated checks.

# Configuration portability and first-release cleanup (2026-10-21)

- Add authenticated WebUI configuration export/import. Include Bot Token, API Hash and proxy password encrypted with an independent user-supplied backup password. Exclude administrator credentials/sessions, tasks/history/database, media, logs, caches and service/protection state; explain the scope in UI and public documentation.
- Restore must validate the complete settings before changing anything, respect environment-controlled fields, current storage roots, settings revision conflicts and existing safe API transition barriers. It must not contact Telegram merely to inspect a backup. Wrong passwords/corrupt/unsupported files must not change settings or expose secrets.
- Remove unpublished-version compatibility in active application code, tests, UI and public documentation. Incompatible old test data may be cleaned; no migration bridge is required. Preserve upstream Telegram semantics, crash recovery, credential/cooldown protections and normal safe defaults. Historical journals remain historical records.
- Proxy behavior is consistent across API modes: enabled uses the configured proxy, disabled uses the container's normal network. Investigate removing the host TUN requirement and simplify deployment without silent direct fallback when proxy setup fails.

## Local API measured acquisition progress (authorized 2026-10-22)

- Show true local Telegram download bytes, speed and a percentage when exact total size is known. Estimated totals must be visibly approximate; unknown totals remain indeterminate. Keep destination saving as a distinct later stage.
- Observe the existing TDLib download, without another Telegram download request, personal-account login or cloud logout. Progress reads must not initialize a missing Bot/API client or change request protection.
- Bind observations to the exact Bot/file/job/attempt/API generation. Cancellation, retries, process restart and late replies cannot overwrite another stage or attempt. A progress failure never cancels downloads or releases ownership.
- Preserve unknown-progress fallback for unavailable/unsupported services and avoid exposing tokens, native file paths or TDLib IDs to WebUI/logs. Keep the service private in standard Compose with no additional user configuration.
- Verify the actual patched native build in isolation plus scoped backend/UI regressions. Application readiness is not real Telegram/large-file acceptance.
