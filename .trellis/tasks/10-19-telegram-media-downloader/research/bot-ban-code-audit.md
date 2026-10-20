# Research: Bot deletion incident — production code audit

- Query: 专用下载 Bot 显示“已销号用户”，另一个 Bot 正常；审计代码可能导致 Bot 封禁及关联所有者处罚的行为，并提出预防建议。
- Scope: internal; current production source and Git history before/after `13ca33d`; external policy interpretation is assigned to the main session.
- Date: 2026-10-20
- Baseline: HEAD `13ca33d`; parent `046489f`; initial implementation `8789e89`.

## Findings

### 1. What source code can establish

- The application is a Bot API client, not a personal-account client. `backend/app/bot/client.py:11` imports aiogram Bot; its constructor uses a Bot Token and either official cloud HTTPS or the configured local server. Production sources contain no Telethon/Pyrogram, phone-number/code/password login, `sign_in`, or personal session handling. API ID/Hash are passed only to the official local Bot API subprocess (`deploy/bot-api/supervisor.py:169`). This excludes an app-implemented personal-account session termination path, not Telegram-side owner-account enforcement.
- Only authorized private-chat messages create requests. `backend/app/bot/ingest.py:63` rejects non-private chats and non-whitelisted senders before creating notifications/tasks. Settings require at least one allowed user when a token is configured (`backend/app/services/settings.py:144`). Group/channel posts are not a download source. The poll request only subscribes to `message` and `callback_query` (`backend/app/bot/client.py:63`).
- Actual upstream methods are `getMe`, `getUpdates`, `getFile`, `sendMessage`, `answerCallbackQuery`, and migration `logOut` (`backend/app/bot/client.py:58–164`). There is no production `sendVideo`, `sendPhoto`, `sendDocument`, `forwardMessage`, or `copyMessage`. Media are retrieved into local storage, not redistributed by this application's Bot code.
- This does **not** make the incoming media invisible to Telegram: the user sends/forwards the media to a Telegram Bot chat before local retrieval. The Bot also echoes caption-derived titles, original/planned filenames and relative paths in confirmation/result/status messages (`backend/app/services/tasks.py:79`, `:124`; `backend/app/storage/naming.py:13`, `:20`). Captions are bounded plain single-line text, but no policy/content review occurs.
- No link-preview-disable option is supplied to `send_message` (`backend/app/bot/client.py:153`). Caption/title/custom-template URLs can therefore remain in outgoing text; the application does not explicitly suppress Telegram link previews. Whether any particular message actually generated a preview is unknown. Optional suppression is a modest content/privacy improvement, not evidence that preview behavior caused this incident.

### 2. Confirmed historical request hygiene weaknesses

The parent revision `046489f` had no shared persistent request gate in `backend/app/bot/client.py`. Its `Runtime.poll_loop` caught remote exceptions and retried after five seconds; failed identity creation could consequently repeat `getMe` at approximately that interval. Poll-time auth failures could likewise repeat. The prior notification loop honored a 429 delay for that specific outbox row, but other polling, identity, file retrieval, notifications, and manual validation did not share that deadline. Concurrent downloads could begin upstream requests together. These are genuine avoidable call-pattern weaknesses, not proof of a permanent ban trigger. A five-second auth loop, a normal long poll, or a 429 alone does not demonstrate that Telegram banned the Bot for excessive traffic.

`13ca33d` adds a common credential-digest gate (`backend/app/services/request_safety.py:87`, `:137`): approximately one second between all request starts, per-chat send spacing, maximum persisted server cooldown, bounded transient/conflict backoff, and persistent 401 auth pause. Successful normal requests cannot clear a server wait. Explicit saved-token identity recovery is limited to one probe per 60 seconds. All production client constructors attach the gate; cloud file HTTP also reports 429/401 and connection failures. Offline settings saves and status reads do not call Telegram. These changes reduce the known historical weaknesses only after this code is actually deployed; the previous delivery explicitly did not update the running containers.

### 3. Single instance and migration boundaries

- The app locks its data directory (`backend/app/main.py:79`). This protects against two processes using the **same data directory**. It cannot detect a second deployment with separate storage, a NAS plus desktop running the same token, or unrelated third-party use of the token. The protection database is likewise per deployment, not global across every program using the token. Conflict responses now back off (`backend/app/services/request_safety.py:154`), but there is no dedicated conflict/duplicate-consumer diagnostic or permanent duplicate-consumer pause.
- Managed migration drains polling/notifications/download owners, then performs a cloud identity preflight and one guarded `logOut`, tracks ambiguity, and preserves the official cloud cooldown (`backend/app/services/local_api.py:336`, `:357`, `:384`). There is no routine logout operation in ordinary downloads. An unknown logout result blocks automatic replay. A deliberate Bot API logout is not code that deletes a Bot or revokes personal-account sessions.
- The local subprocess is built from pinned official `tdlib/telegram-bot-api` source (`deploy/bot-api/Dockerfile:3–5`), launched with local storage and low verbosity (`deploy/bot-api/supervisor.py:29`). Application pacing cannot control all internal TDLib/local-server connections or retry behavior. Cloud/local mode does not establish a content-policy exemption or account-isolation guarantee.

### 4. Token compromise review: protections and limits

- Settings credentials are Fernet-encrypted at rest; master-key file creation uses mode 0600 (`backend/app/core/security.py:17`, `:25`). Public settings blank Token/API Hash/proxy password (`backend/app/services/settings.py:102–112`). Validation/general API errors use fixed sanitized messages rather than echoing submitted payloads (`backend/app/main.py:142–149`); runtime catches avoid printing raw Telegram exceptions.
- Official local API child output is discarded (`deploy/bot-api/supervisor.py:173–175`) and the Compose publishes the app port only; Bot API 8081 is internal (`compose.yaml:8`, `:39`). Proxy handling does not intentionally send credentials to an application analytics/external reporting service.
- `.gitignore` excludes `.env`, keys, runtime, database, and log artifacts. `git ls-files` for these patterns showed only `.env.example`; no actual secrets were opened or printed during this review. This is **not** a full historical secret scan and cannot prove that a token was never leaked in chat, terminal history, third-party bots/sites, browser extensions, user-added reverse-proxy logs, backups, or external programs.
- Existing HTTP deployment and permitted 6-character passwords warrant restricting WebUI to a trusted network/VPN and choosing a long unique password. A network intermediary or compromised host can defeat application-level at-rest encryption; the key and DB are both needed by the running app. If Token compromise occurred, an attacker using Bot API would not be constrained by this application's whitelist, pacing, or media-download-only design.

### 5. Root-cause candidates and evidence strength

| Candidate | Evidence available | Assessment |
| --- | --- | --- |
| Incoming media / caption / URL triggered content-policy enforcement or a report | Application receives user-forwarded media and echoes caption summaries; exact incident media unknown | Plausible; no incident-level evidence. Personal use/local saving does not prove exemption. Do not label all adult/copyright material illegal or automatically bannable. |
| Bot token reused/leaked and used externally for abusive messages | Token is sufficient to act as the Bot; repository protections exist but no exposure history reviewed | Plausible; no current evidence of compromise. Another healthy Bot does not disprove token-specific compromise. |
| Avoidable retries or multiple consumers contributed to abuse heuristics | Historical nonshared 429/401 handling confirmed; actual request counts/conflicts unknown | Confirmed implementation weakness, unproven incident cause; now partly mitigated in committed code, deployment unconfirmed. |
| Bot branding/description/name/links or prior activity caused moderation | Outside current code and no Bot metadata timeline | Unverified; ask for nonsecret notice/metadata/activity history only if needed. |
| Telegram error / shared owner enforcement / owner-account action removed the Bot | User observes one deleted Bot and personal logout | Cannot distinguish action order/cause from UI state alone. Bot-specific removal supports Bot-specific investigation; it does not establish that Bot removal preceded and caused personal-account enforcement. |
| Application deliberately deleted the Bot or terminated personal sessions | No such method/client found | No code evidence supporting this explanation. |

## Recommended next actions

1. Keep the affected Bot stopped. Preserve nonsecret timeline evidence (last successful request, first error **code**, BotFather/service notices, username and screenshots) and seek Telegram's stated reason/appeal. Do not infer ban cause solely from “已销号用户”, 401, or forced logout. A revoked token and a deleted/restricted Bot are different conditions.
2. Do not attach the healthy Bot to the same workload as a diagnostic experiment. Review recent media/links and Bot metadata against official policies first. Use only lawful, authorized, clearly benign sample media for subsequent verification; no automatic or bulk reposting. No recommendation to rotate accounts/IPs to evade enforcement.
3. If compromise is suspected and ownership access is recovered, use official BotFather token revocation/reissue; remove old token copies and audit other tools/deployments. Token replacement is credential recovery, not a cooldown/ban bypass. No request to send tokens or media into chat is needed.
4. Deploy `13ca33d` before another production trial, after verifying configuration and backup. Only one deployment/tool may poll the Bot token. Do not alternate cloud/local as an attempted moderation workaround. An official local API server still logs in as the same Bot.
5. Optional focused product hardening: explicitly disable outgoing link previews; provide a compact-notification mode retaining task ID/status but excluding caption/path echoes; add a dedicated persistent 409 duplicate-consumer diagnostic/pause; add sanitized local request audit fields (method, Bot-ID/digest, result class, timestamp, retry deadline; **never** token-bearing URLs, media, full captions, or raw upstream exceptions). These improve exposure and future evidence, not guaranteed ban prevention.
6. Optional intake quotas/backpressure can prevent accidental mass forwarding from creating an unbounded task/notification backlog; request pacing already controls new upstream starts. Re-check current allowlist at notification delivery if policy should immediately revoke queued feedback for a removed user. Neither gap presently permits arbitrary public unsolicited chat targets through ordinary messages.

## Related specs / files found

- `.trellis/tasks/10-19-telegram-media-downloader/prd.md`: private self-hosted download scope; no personal login, real Telegram acceptance still pending.
- `.trellis/tasks/10-19-telegram-media-downloader/design.md`: aiogram architecture, instance lock, ingestion/migration/request-safety contracts.
- `.trellis/tasks/10-19-telegram-media-downloader/implement.md`: prior request-safety verification and explicit no-deployment note.
- `.trellis/spec/backend/request-safety-contracts.md`: shared gate, persisted cooldown, recovery and no-personal-login requirements.
- `backend/app/bot/{client,ingest}.py`: sole upstream Bot methods and inbound whitelist.
- `backend/app/workers/runtime.py`: polling/outbox/retry loops and lifecycle.
- `backend/app/services/{tasks,settings,local_api,request_safety}.py`: text feedback, credentials, migration and guard.
- `backend/app/main.py`, `backend/app/core/security.py`: administration/lock/error handling and encryption.
- `deploy/bot-api/{Dockerfile,supervisor.py}`, `compose.yaml`: official pinned server and nonpublic API port.

## External references

Main session read the following official pages on 2026-10-20 without contacting a Bot or submitting a support form:

- https://telegram.org/tos/bot-developers — sections 5.1/5.2 cover content, spam, services, and moderation; 5.2(e) explicitly names external cloud storage sites as an example of significantly divergent use. This is a relevant policy ambiguity for an external archive service, not proof that private user-initiated NAS downloads violate the clause or triggered this incident. Section 4.3 permits directly and voluntarily submitted user data with appropriate disclosure/consent; it is not a blanket exemption from other terms.
- Same terms, section 10: Telegram may ban the account tied to a Bot. Section 6.3 also says Bots can be terminated after their owner account is deleted, compromised or otherwise lost. Consequently, direction of causality cannot be inferred from both accounts being unavailable.
- https://core.telegram.org/bots/faq#my-bot-is-hitting-limits-how-do-i-avoid-this — message-rate excess is described as returning 429. This does not establish ordinary rate-limit errors as the cause of a deleted Bot. FAQ also directs developer questions to @BotSupport.
- https://core.telegram.org/bots/api#using-a-local-bot-api-server — self-hosting is officially supported and allows larger file downloads; neither use of the official local server nor its API ID/Hash is itself evidence of abuse or personal-account login.
- https://telegram.org/faq_spam — @SpamBot is for spam-limited accounts and mistaken restrictions when the account can still be used. Do not present it as a guaranteed remedy for a deleted Bot or fully inaccessible account.
- https://telegram.org/support — official support form was verified; do not submit on the user's behalf without explicit authorization. Provide Bot username and a nonsecret timeline, not tokens, when asking for review.

These sources establish policy and support options, not Telegram's private moderation heuristics or the actual incident reason. No official source inspected states a guaranteed safe request rate or a generally published daily NAS-download allowance.

## Caveats / Not Found

No runtime directories, `.env`, database, logs, credentials, deployed process state, or real Telegram endpoints were accessed. No code, spec, deployment, or Git mutation was performed. No test run was needed for this read-only audit. There is no official incident notice, authoritative ban reason, request/traffic timeline, or verified deployment version in the reviewed evidence. The observation of a deleted Bot plus a disconnected/deleted personal account cannot by itself prove a causal chain or a specific trigger.
