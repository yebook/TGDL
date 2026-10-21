# 本地 Bot API 与 API ID / Hash

本地 Bot API 基于 Telegram 官方开源服务，运行在自己的 Docker 主机上；本项目在固定版本源码中加入 TDLib 原生代理配置补丁。它使用 Bot Token 操作机器人，不需要在容器中扫码、填写手机号或输入账号登录验证码。云端下载上限 20 MB，本地模式用于大文件；真实大文件与飞牛 OS 链路的待验收范围见 [验证说明](verification.md)。

## 先分清三类信息

| 信息 | 获取方式 | 填写位置 |
| --- | --- | --- |
| Bot Token | 官方 `@BotFather` 创建 Bot 后获得 | WebUI 设置中的 Bot Token |
| 允许使用的 Telegram 用户 ID | 自己账号的数字 ID，不是手机号、用户名、频道 ID | WebUI 用户 ID 白名单 |
| API ID / API Hash | 登录 `my.telegram.org` 创建应用 | WebUI 设置中的本地 API 凭据 |

云端不需要 API ID / Hash。标准部署已包含常驻管理组件，没有凭据时等待；WebUI 管理容器内 API 子进程，日常无需重启应用或容器。不要在聊天、仓库、截图或问题报告中发送 Token、Hash、登录码。

## 获取 API ID / API Hash

1. 使用自己的正常 Telegram 账号，保持官方客户端已登录。
2. 打开 <https://my.telegram.org/apps>，按国际格式填写手机号，按网页要求登录。登录码通常在 Telegram 官方消息中送达，以页面提示为准。
3. 进入 **API development tools**。已有应用则直接查看 `App api_id`、`App api_hash`，不重复创建。官方说明每个号码只能关联一个 API ID。
4. 没有应用时填写创建表单，示例如下；校验规则、必填项以官方页面为准，示例不保证审核通过。

   | 字段 | 建议 |
   | --- | --- |
   | App title | 简短名称，例如 `Personal Media Downloader` |
   | Short name | 按要求使用字母数字，例如 `mymediatool2026` |
   | URL | 自己实际使用的项目地址，若页面允许则可留空 |
   | Platform | 根据实际用途选择，例如 `Other` |
   | Description | 如 `Personal Telegram bot media download service` |

5. 将数字 **App api_id** 与 **App api_hash** 填入 WebUI 的本地 API 设置。保存不要求在线；Hash 不回显，留空保留已存值，清除使用明确清除操作。

在 WebUI 填写应用凭据即可，无需修改容器环境或重新部署。网站上的个人账号登录仅用于申请应用，项目运行不需要个人账号会话。

### 申请失败怎么办

- **Too many attempts. Please try again later.**：停止重复提交，按官方等待时间处理；没有明确期限时不能保证何时恢复。先检查是否已经有应用，不频繁更换号码或网络尝试绕过限制。
- 没有收到验证码：检查国际区号、已登录官方客户端消息与网站提示，避免连续索取验证码。
- 创建时笼统报错：检查表单格式，保存不含凭据的错误说明。未申请成功前可先用云端验证不超过 20 MB 的媒体。
- Bot Token 不是 API Hash，用户 ID 不是 API ID。申请应用也不等于创建 Bot。

## 从云端切换到本地顺序很重要

### 标准部署：在 WebUI 中操作

1. 使用根目录 `compose.yaml`，确认本地 API 管理服务可用。
2. 确认同一个 Bot 只有本项目消费，停止其他程序；填好 API ID / Hash、Bot Token、白名单和代理设置。
3. 选择本地模式并保存。后台暂停新领取、等待当前下载及在途请求，再执行云端退出、按代理配置启动 API 进程、验证 Bot。WebUI 保持可访问，Bot 可短暂暂停；不要另外手动执行 `logOut`。
4. 观察进程状态、切换步骤与 Bot 连接。「进程运行」说明子进程已启动；「本地 API 可用」说明本地服务能响应请求。服务可能使用缓存返回身份信息，因此这两项都不能单独证明 Telegram 上游已连通。发送 `/start` 并检查 Bot 回复，再测试实际文件保存。
5. 失败按页面原因处理后重试。API 子进程异常可点击「重启 API 进程」；正常下载会先等待安全边界。管理组件失联或容器已停仍需检查 Docker、挂载与权限。

修改 API 凭据会在安全边界重启 API 子进程，应用与容器不重启。API Hash 加密存储，管理交接使用私有控制目录和独立密钥，需限制访问并随停服数据一起备份。

### 等待与回切云端

云端 `logOut` 成功后可以立即连接本地；**10 分钟限制的是重新登录云端**。回切时直接选择云端并保存，由后台等待活动下载、协调停止与冷却，在页面观察状态。不要并行运行两个消费者或反复切换试探恢复。

退出结果因超时或崩溃不明确时保持暂停，不自动重放退出或回切云端。先排查原消费者，再按页面说明审慎处理专门的确认继续操作；它不代表已证实退出成功。认证失败、限流与网络退避同样不能通过重启或手动校验绕过，见 [请求保护](operations.md#telegram-请求等待与认证暂停)。

API 与应用必须共享相同缓存来源和相同容器路径 `/var/lib/telegram-bot-api`，API 可写、应用只读。应用只访问内部 API 地址，无需对外暴露 8081。服务地址、挂载、generation 和权限都由部署者保证，详见 [部署指南](deployment.md)。

## 本地 API 也需要代理时

使用同一 `compose.yaml`，在 WebUI 启用代理并填写 HTTP CONNECT / SOCKS5 地址和端口。云端与本地统一使用这份配置；关闭则使用容器默认出口。不需要 TUN、额外网络容器或重新部署。本地代理变更会等待当前请求安全结束，再重启 API 子进程，不重复退出云端。

电脑仅开启系统代理或浏览器代理时，Docker 中的服务不会自动读取它；云端和本地都一样。请将代理地址设为容器可达的 `host.docker.internal` 或 LAN 地址，并允许 Docker 访问该代理服务。“进程运行”不等于 Bot 已连接，需等待配置确认与 Bot 验证。

已成功退出云端的切换会继续本地启动和验证流程，无需手动再次执行 `logOut`。若界面提示退出结果不确定，请按该状态的恢复要求处理，不要用普通重试代替确认。

两种模式底层协议不同：云端是 HTTPS，本地通过 TDLib 连接 Telegram。本项目分别接入 HTTPS 代理和 TDLib 原生代理，提供相同的配置行为。原版官方 API 的 `--proxy` 仅用于 webhook，本项目不依赖它代理 Telegram。

代理启用失败不会自动回退直连。查看失败原因，修正代理配置后重试；组件失联时检查 Docker 状态、挂载与权限，见 [部署说明](deployment.md)。

关闭代理并保存也会应用新的直连配置，需要等待当前请求结束及 API 子进程重启。期间校验按钮会说明等待原因；若验证失败，页面会显示失败原因并引导到本地 API 服务状态处理，不应一直当作“切换中”。直连网络无法访问 Telegram 时，关闭代理不会使连接恢复，需要修复网络或重新启用可用代理。

## 常见问题与回切云端

| 现象 | 处理 |
| --- | --- |
| 等待凭据 | 在 WebUI 保存完整 API ID / Hash，不反复重建容器 |
| 管理组件失联 | 检查容器、控制目录挂载、UID/GID、权限和心跳 |
| 进程运行但 Bot 未连接 | 检查 Token、网络、请求保护与切换步骤 |
| 模式有活动任务 | 等获取、保存、取消或恢复任务结束，不改数据库绕过 |
| 本地仍提示云端大小限制 | 检查实际模式，旧任务保留自己的获取方式；用新消息验证 |
| `getFile` 成功但不能读文件 | 检查两容器缓存来源、相同路径与读取权限 |
| polling 冲突 | 停止旧消费者，检查原 webhook 与迁移状态 |
| 换栈后变为初始化界面 | 停止新栈并检查是否挂载了空数据目录，勿重新初始化 |

## 官方参考

- [获取 API ID / Hash](https://core.telegram.org/api/obtaining_api_id)
- [应用管理](https://my.telegram.org/apps)
- [官方本地 Bot API](https://github.com/tdlib/telegram-bot-api)
- [logOut 与云端冷却](https://core.telegram.org/bots/api#logout)
- [deleteWebhook](https://core.telegram.org/bots/api#deletewebhook) / [close](https://core.telegram.org/bots/api#close)

平台表单、限制和迁移要求可能变化，以官方最新说明为准。
