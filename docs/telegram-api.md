# 本地 Bot API 与 API ID / Hash

本地 Bot API 是 Telegram 官方开源服务，运行在自己的 Docker 主机上；本项目通过它下载超过 20 MB 的文件。它仍使用 Bot Token 操作机器人，不会变成个人账号客户端，也不需要个人账号在容器中扫码或输入登录验证码。

真实 Telegram 大文件和飞牛 OS 链路仍需按 [验证记录](verification.md) 实测。

## 先分清三类信息

| 信息 | 获取方式 | 填写位置 |
| --- | --- | --- |
| Bot Token | Telegram 中的官方 `@BotFather` | WebUI 设置中的 Bot Token |
| 允许使用的 Telegram 用户 ID | 自己账号的数字 ID，不是手机号、用户名、频道 ID | WebUI 用户 ID 白名单 |
| API ID / API Hash | 自己账号登录 `my.telegram.org` 创建应用 | WebUI 设置中的本地 API 凭据；旧部署 `.env` 可作兼容来源 |

云端模式无需 API ID / Hash。标准部署已包含常驻管理组件，WebUI 负责启动其内部 API 进程，无需日常操作容器。不要将 Token、Hash、登录验证码或含密钥的截图发到聊天、仓库或问题报告中。

## 获取 API ID / API Hash

1. 确保已有正常使用的 Telegram 账号，并已登录官方手机或桌面客户端。
2. 打开 <https://my.telegram.org/apps>。输入账号手机号，使用国际格式，例如 `+86` 加手机号；按页面要求登录。登录码通常由 Telegram 应用内的官方消息送达，请以页面说明为准，不要当作 Bot Token 使用。
3. 登录后进入 **API development tools**。若已有应用，直接查看其 `App api_id` 和 `App api_hash`，不必重复创建。官方目前说明每个号码只能关联一个 API ID。
4. 若显示创建应用表单，可按下面的示例填写，再点击 **Create application**。字段校验与必填项以当前官方页面为准，这些示例不是保证通过审核的固定答案。

   | 字段 | 填写建议 |
   | --- | --- |
   | App title | 简短可识别的应用名称，例如 `Personal Media Downloader` |
   | Short name | 按页面限制使用字母数字，例如 `mymediatool2026` |
   | URL | 按页面要求填写自己实际使用的项目地址；若页面允许留空可留空，不必伪造网站 |
   | Platform | 按实际用途选择，例如自托管服务可选择 `Other`；若出现补充字段，说明其用途 |
   | Description | 简述用途，例如 `Personal Telegram bot media download service` |

5. 成功后将 **App api_id**（数字）和 **App api_hash**（字符串）填写到 WebUI 的本地 API 设置。保存不要求 Telegram 在线，Hash 不回显；留空保留已存值，清除需使用明确的清除操作。兼容旧部署时也可在部署主机编辑 `.env`，但日常配置优先使用 WebUI：

   ```dotenv
   TELEGRAM_API_ID=你的数字API_ID
   TELEGRAM_API_HASH=你的API_HASH
   ```

NAS 部署参数使用项目根目录的 `.env`；旧 Windows 临时环境使用 `.runtime/local-docker/.env`。登录网站时使用个人账号只是为了申请应用凭据，运行本项目仍只接收白名单用户发给 Bot 的媒体。

### 申请失败怎么办

- **Too many attempts. Please try again later.**：停止连续提交，若 Telegram 给出等待时间则按其要求等待；没有明确时间时也无法保证几小时或一天后一定恢复。稍后再试，并先检查是否已有应用。不要靠频繁换号码、网络或反复申请来绕过限制。
- 未收到登录码：先确认手机号和国家区号，查看已登录 Telegram 客户端中的官方消息，并按网站提示处理；避免反复请求验证码。
- 创建时笼统报错：核对当前表单提示、名称格式和填写内容；持续失败时保留不含密钥的错误描述，查看官方支持渠道。未申请成功前仍可继续使用云端模式验证不超过 20 MB 的媒体。
- 不要把 **Bot Token** 填入 API Hash，也不要把 **用户 ID** 填入 API ID。申请应用不等于新建 Bot，原 Bot Token 和白名单通常保持不变。

## 从云端切换到本地：顺序很重要

### 标准部署：在 WebUI 中操作

1. 确保是新版标准部署，设置中的本地 API 显示为受管理组件，而非“未部署/外部管理”。旧版需先按 [升级说明](deployment.md#升级到-webui-管理本地-api) 更新一次容器与控制目录挂载。之后日常操作无需停止应用或重启容器。

   旧设置已选本地时，首次升级会保留选择但暂停自动连接，实际模式显示未确认。先核对凭据和其他消费者，再使用“重试操作”明确启用；升级本身不自动退出 Bot。
2. 确认这个 Bot 仅由本项目使用，停止其他程序的消费者；本项目不能替你管理其他应用。填写 API ID / Hash、Bot Token 与用户白名单，按已部署的直连/TUN 能力配置代理。
3. 选择本地 API 并保存。保存只表示期望配置已记录；后台先暂停新任务领取、等待当前下载和在途 Bot 请求，再退出云端、启动 API 进程并验证 Bot 身份。WebUI 始终可访问，Bot 会暂时暂停。不要另外手动运行下面的旧迁移脚本。
4. 在设置或系统状态查看 API 进程、Bot 连接和切换步骤。进程“运行中”只表示服务已启动，Bot“已连接”才表示身份验证通过；下载成功还需发送小文件及大于 20 MB 视频验证。
5. 启动/验证失败时按页面原因处理后重试；API 子进程异常可使用“重启 API 进程”。有下载时先等待，不会强杀正常下载。若管理组件自身失联，WebUI 不能替代 Docker 修复容器，需检查部署服务。

成功退出云端后，本地可以立即连接；回切云端要等官方 10 分钟限制，界面显示期限并等待，不需反复点击。退出结果因超时或崩溃不明确时，系统保持暂停，不自动重放退出或回云；仅在排查其他消费者、了解不确定性后使用页面的专门确认继续操作，不能把它当作“已证实退出成功”。

API Hash 使用应用密钥加密存储，另以独立控制密钥交接给管理组件；控制目录及其中密钥需限制访问并备份。更换凭据会在安全边界重启 API 子进程，应用和容器不重启。直连/TUN 网络拓扑调整仍需部署操作，按钮只管理 API 进程。

### 外部或旧版手动管理

下面的手动流程只供未启用受管理组件的外部 API 部署参考。后文 `compose.yaml` / `compose.proxy.yaml` 命令指旧版非受管配置示例，必须换成你自己的外部服务配置，不能照搬到新版标准栈。不要与上面的自动切换同时执行；新版标准部署应使用 WebUI。

Telegram 要求先向**官方云端**调用一次 `logOut`，再让该 Bot 在本地服务登录。成功后可立即登录本地；**10 分钟限制的是重新登录云端**，不是本地等待时间。WebUI 不会自动调用 `logOut`。

以下是根目录 `compose.yaml` 的直连流程，命令在项目根目录执行。保留原来的 `TGD_HOST_DATA`、下载和缓存挂载，确保只运行一套应用、一个 Bot 消费者；不要新建空数据目录代替原目录。当前 Windows 临时栈请先看 [专用切换步骤](local-run.md#3-切换到完整下载服务)。

1. **准备但不启动本地 Bot API。** 填写 `.env` 中的 API ID / Hash，按 [部署指南](deployment.md) 准备缓存目录、非 root UID/GID 和网络。首次先构建，避免编译耗时打断迁移：

   ```sh
   docker compose --env-file .env -f compose.yaml --profile local-api config --quiet
   docker compose --env-file .env -f compose.yaml --profile local-api build app bot-api
   docker compose --env-file .env -f compose.yaml --profile local-api stop bot-api
   ```

   `build` 不启动服务。迁移前保持 `bot-api` 停止：如果它已运行，保存本地模式就可能提前登录本地。若这个 Bot 已经在多个服务登录过，先停止其他消费者，并按官方迁移说明排查；不能靠这份流程保证此前更新未丢失。

2. **等待任务结束并预存本地模式。** 暂停向 Bot 发送新媒体，等正在获取、保存、取消和恢复阻塞的任务结束；待确认/排队任务也建议先处理完。在仍可访问的 WebUI 选择 **本地 Bot API**，保存设置。无需先通过在线校验；本地服务尚未启动，出现暂时连接失败是预期的。若提示有活动任务，先处理任务再切换，不要改数据库绕过保护。直连本地 API 时关闭 WebUI 代理开关并一起保存；确实需要代理时按下节准备 TUN。云端退出脚本所需的代理可单独输入。
3. **停止应用及其他旧消费者。** 确保上一步已保存成功，再停止应用，避免退出云端后它再次以云端模式连接：

   ```sh
   docker compose --env-file .env -f compose.yaml stop app
   ```

4. **按下一节向云端 `logOut` 一次。** 已停掉所有消费者后再执行。若失败或结果不明确，先排查，不要继续启动本地或自动退回云端。旧服务使用 webhook 时先按说明移除，并保留待处理更新。
5. **启动本地栈。** 只有云端退出成功、设置已保存为本地后再运行：

   ```sh
   docker compose --env-file .env -f compose.yaml --profile local-api up -d
   docker compose --env-file .env -f compose.yaml --profile local-api ps
   ```

6. 登录原 WebUI，确认还是原账号和设置，点击 **校验配置**，查看系统中的 Bot、存储状态；先发小文件，再验证超过 20 MB 的视频和混合媒体组。保存成功、容器运行都不能代替下载验收。

首次使用从未接入过的 Bot 也先遵循官方 `logOut` 要求，再启动本地登录。不在每次启动时重复退出。后续修改 API ID / Hash 后，`restart` 不会重新加载 Compose 环境变量；等待安全边界后运行 `docker compose --env-file .env -f compose.yaml --profile local-api up -d --force-recreate bot-api`，再检查连接。

## 安全地执行一次云端 logOut

以下临时客户端只访问官方云端，不运行应用入口、不启动本地 Bot API、不读取数据库中的 Token。它在终端隐藏输入 Token 和可选代理 URL，避免把密钥写进命令行历史或 URL 日志。需要交互式终端，不能在无终端的自动化作业中运行。云端可直连时代理留空；需要代理时输入容器可达的 `http://...` 或 `socks5://...` URL，宿主机代理可用 `host.docker.internal`，不要使用容器内的 `127.0.0.1`。

在项目根目录创建 `.runtime/cloud-logout.py`（若没有 `.runtime`，先创建该目录），用编辑器保存以下代码。脚本本身不包含密钥，错误只输出通用信息：

```python
import asyncio
import getpass
import warnings

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession


async def main() -> None:
    warnings.simplefilter("error", getpass.GetPassWarning)
    session = None
    try:
        token = getpass.getpass("Bot Token (hidden): ").strip()
        proxy = getpass.getpass("Cloud proxy URL (hidden; Enter for direct): ").strip()
        session = AiohttpSession(proxy=proxy or None, timeout=30)
        bot = Bot(token=token, session=session)
        webhook = await bot.get_webhook_info()
        if webhook.url:
            answer = input("Existing webhook found. Remove it, keeping pending updates? [y/N]: ")
            if answer.strip().lower() != "y":
                print("Aborted; logOut was not called.")
                return
            if not await bot.delete_webhook(drop_pending_updates=False):
                print("Webhook removal failed; logOut was not called.")
                return
        if await bot.log_out():
            print("Cloud logOut succeeded. Start the already-configured LOCAL stack.")
        else:
            print("Cloud logOut did not succeed. Do not start the local stack yet.")
    except Exception:
        print("Cloud operation failed or outcome is unknown. Check token/network; do not start local yet.")
    finally:
        if session is not None:
            await session.close()


asyncio.run(main())
```

确认应用已停止后，在项目根目录执行下面的单行命令。Compose 的一次性 `run` 默认提供交互终端，不要加 `-T`；不会启动依赖服务。挂载脚本路径为项目根目录下的相对路径：

```sh
docker compose --env-file .env -f compose.yaml run --rm --no-deps --volume ./.runtime/cloud-logout.py:/tmp/cloud-logout.py:ro --entrypoint python app /tmp/cloud-logout.py
```

Windows 当前环境把 `--env-file .env` 替换为 `--env-file .runtime/local-docker/.env`，其他参数不变；它使用完整栈的应用镜像，但不会启动应用进程或与停止中的旧 WebUI 抢占端口。代理部署则将 `-f compose.yaml` 替换为 `-f compose.proxy.yaml`，仍保留 `--no-deps`。

若检测到旧 webhook，只有确认其原服务已停、确实属于自己时才输入 `y`。删除时显式使用 `drop_pending_updates=False`，不清空消息。执行成功后不要重新启动仍保存云端模式的旧服务。脚本可在迁移结束后删除，不要在脚本内硬编码凭据。

## 本地 API 也需要代理时

WebUI 的云端代理只影响应用的 HTTPS 请求，不能代理本地 Bot API 到 Telegram 的底层传输；Windows 系统代理、容器 `HTTP_PROXY` 或官方 `--proxy` 参数也不能代替本项目的 TUN 路径。`--proxy` 用于向外发送 webhook。

需要代理时使用独立的 `compose.proxy.yaml`，不与 `compose.yaml` 合并。先确认主机支持 `/dev/net/tun` 和所需网络能力，详见 [TUN 部署说明](deployment.md#本地-api-的代理三个容器)。迁移顺序仍是：**本地 API 停止 → 保存本地设置 → 停应用 → 云端退出 → 启动本地栈**。

若原应用使用直连 Compose，需要先在没有任务运行时停止旧栈，再用代理 Compose **只启动 app，不启动依赖**，以便 WebUI 保存代理配置及本地模式：

```sh
docker compose --env-file .env -f compose.proxy.yaml build
docker compose --env-file .env -f compose.yaml down
docker compose --env-file .env -f compose.proxy.yaml up -d --no-deps app
```

原有数据目录、下载目录、API 缓存和网络配置目录必须保持一致。此时 `bot-api`、`telegram-network` 尚未启动，WebUI 保存配置不依赖它们在线；若原设置为云端，app 暂时仍是唯一云端消费者。保存代理和本地模式成功后，用 `compose.proxy.yaml stop app` 停应用，再执行上节云端退出。成功后运行 `docker compose --env-file .env -f compose.proxy.yaml up -d` 启动完整栈。检查代理 applied 状态和真实下载，不要因字段保存成功就判定代理已生效。

API 缓存必须由 API 可写、app 只读地挂载到**相同容器路径** `/var/lib/telegram-bot-api`。直连地址为 `http://bot-api:8081`，TUN 地址为 `http://telegram-network:8081`，Compose 已配置；WebUI 不需要填写地址，8081 也无需发布到局域网。

## 常见问题与回切云端

| 现象 | 优先检查 |
| --- | --- |
| 本地 API 等待凭据 | 在 WebUI 填写完整 API ID / Hash 并保存；管理组件无凭据常驻等待，不应反复重建容器 |
| 管理组件失联 | 检查容器、私有控制目录挂载/UID/权限和心跳；进程按钮不能修复已停止的管理容器 |
| WebUI 保存成功但校验失败 | API 是否运行、app 是否已选本地、Docker 网络/DNS、API 凭据与实际网络是否可用 |
| 提示 API 模式有活动任务 | 等获取/保存/取消/恢复任务结束，不绕过切换保护 |
| 本地仍提示云端大小限制 | 检查保存后的模式和任务状态；旧任务不因全局模式切换自动改变已经记录的获取方式，使用新消息重新验证 |
| `getFile` 成功但无法读取文件 | 两容器缓存来源及绝对容器路径是否一致、UID/GID 和只读权限是否正确 |
| 收不到更新或出现 polling 冲突 | 旧消费者/webhook 是否仍在运行、云端 logOut 是否成功，避免并行登录 |
| 换栈后出现全新初始化界面 | 很可能挂载到了空数据目录；停止新栈，检查路径，不要重新初始化覆盖原配置 |

查看日志使用 `docker compose ... logs --tail=100 app bot-api`，代理栈还可查看 `telegram-network`；分享前脱敏。不要用不带 `--quiet` 的 `config` 输出密钥。

回切云端前，先等待本地活动任务安全结束，并确保自上次成功云端 `logOut` 起至少经过官方规定的 10 分钟；**在等待结束前不要保存云端模式或进行云端校验**，运行中的 app 会在保存后尝试连接。停止本地 API 和其他本地消费者后，在仍可访问的 WebUI 保存云端模式，再停止 app，使用默认 `compose.yaml` 只启动 app 并手动校验。若原来使用 TUN 代理栈，先停止旧栈，再用默认 Compose 启动 app；保持原数据、缓存和下载挂载，云端所需代理继续在 WebUI 配置。按当前官方迁移要求处理原实例，保留缓存/数据，不同时运行本地和云端消费者。不要通过反复启动两个模式试探是否恢复。`close` 是官方描述的本地服务器之间迁移方法，不应代替云端 `logOut`；更换本地服务器另按官方说明处理。

## 官方参考

- [申请 API ID / API Hash](https://core.telegram.org/api/obtaining_api_id)
- [登录与应用管理](https://my.telegram.org/apps)
- [官方 Bot API 服务及本地迁移说明](https://github.com/tdlib/telegram-bot-api#switching)
- [logOut：本地可立即登录、云端 10 分钟冷却](https://core.telegram.org/bots/api#logout)
- [deleteWebhook](https://core.telegram.org/bots/api#deletewebhook) / [close](https://core.telegram.org/bots/api#close)

以上官方页面于 2026-10-20 核对；网站字段、限制和迁移要求可能调整，以官方最新说明为准。
