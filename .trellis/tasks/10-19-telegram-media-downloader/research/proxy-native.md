# Research: Native proxy for cloud and local Bot API

- Query: Unify configured proxy behavior without a host TUN or privileged network sidecar.
- Scope: mixed; read-only repository and pinned public upstream source.
- Date: 2026-10-21

## Findings

### Current files and behavior

- `compose.yaml`: API shares the network sidecar namespace; sidecar always requires `/dev/net/tun` and NET_ADMIN, including direct/cloud deployments.
- `deploy/bot-api/Dockerfile:4`: official Bot API pin `e3e9dd8e5b3d7ab8537cd5a10dc31d5ffa8f82d1`; already compiles the full upstream C++ source, so adding a reproducible checked source patch does not introduce a new compilation toolchain.
- `deploy/bot-api/supervisor.py:25`: strict versioned encrypted command; lines 167 onward launch a fixed child with secrets in environment and suppressed output. Extend this existing channel for proxy credentials, rather than inventing a public dynamic proxy endpoint.
- `backend/app/services/local_api.py:63`: proxy-only local changes already create an operation and drain consumers. Preserve that barrier, cloud logout uncertainty, cooldown, live file ownership and verified applied mode.
- `deploy/network/supervisor.py`: current firewall provides kernel-enforced blocking; removing it changes the enforcement model to native transport selection. Do not imply the models are identical.

### Upstream evidence (exact pinned revision, retrieved this date)

Bot API source: <https://github.com/tdlib/telegram-bot-api/tree/e3e9dd8e5b3d7ab8537cd5a10dc31d5ffa8f82d1>

1. `telegram-bot-api/telegram-bot-api.cpp:294-302`: `--proxy` explicitly configures **outgoing webhook HTTP requests** and sets `webhook_proxy_ip_address_`. It does not proxy TDLib Telegram transport. HTTP_PROXY alone does not solve the local path.
2. `telegram-bot-api/Client.cpp:9403-9425`: `authorizationStateWaitTdlibParameters` constructs `setTdlibParameters`; this is a concrete patch point before per-Bot Telegram initialization.
3. `Client.cpp:9427-9431`: the following authorization state sends the Bot token; ensure native network enable is gated independently from this state.
4. `ClientParameters.h`: currently carries API ID/hash but no native Telegram proxy parameters. Add immutable validated parameters or parse validated child-only environment for every client.
5. GitHub tree API resolves TDLib submodule to `bc9c263e2bfee06aaab41e82db51a103376030bc`.

TDLib source: <https://github.com/tdlib/td/tree/bc9c263e2bfee06aaab41e82db51a103376030bc>

1. `td/generate/scheme/td_api.tl:10103-10106`: native SOCKS5 and HTTP proxy support; use `proxyTypeHttp(username,password,false)` for CONNECT, not HTTP-only mode.
2. `td_api.tl:8883`: current proxy object is `proxy server:string port:int32 type:ProxyType`.
3. `td_api.tl:16206`: **current** signature is `addProxy proxy:proxy enable:Bool comment:string = AddedProxy`. Older examples with host/port as separate addProxy arguments will not compile against this pin.
4. `td_api.tl:16219`: `disableProxy` is allowed before authorization. Direct startup must explicitly disable any persisted proxy.
5. `td_api.tl:15802-15805`: `setNetworkType` controls whether networking is permitted; `networkTypeNone` blocks, `networkTypeOther` enables normal transport.
6. `td/telegram/Td.cpp:1040-1065`: network actors are created during TDLib parameter initialization, then pending `setNetworkType`/`addProxy`/`disableProxy` requests are completed. **Do not await proxy/network callbacks before sending setTdlibParameters: they are queued and would deadlock.**
7. `td/telegram/StateManager.h:67-68`: defaults are network flag true and NetType::Unknown; `StateManager.cpp:126-136` can automatically change Unknown to Other after one second. Merely queuing networkTypeNone is not a strong proof of no initialization race.
8. `td/telegram/net/ConnectionCreator.h:91`: its initial network flag already false. `ConnectionCreator.cpp:1290` stops the loop with no network.
9. `ConnectionCreator.cpp:793-797`: an enabled proxy with unresolved address stops the client loop instead of choosing direct. Lines 682-686 replace target address with proxy address while retaining Telegram destination for handshake.
10. `ConnectionCreator.cpp:1121-1193`: proxy choice is persisted in the TDLib cache and restored on next process start. Disabled settings cannot be implemented by simply omitting addProxy.

### Recommended clean first-release architecture

Use a small reproducible, explicitly maintained patch to the pinned official source and its pinned TDLib. Remove the sing-box/TUN network sidecar and network namespace sharing; app talks to managed API via ordinary private Compose network. No Docker socket, NET_ADMIN or `/dev/net/tun` required. Keep only app, API supervisor and private control-volume initializer (initializer can use a small existing base without sing-box).

The cloud HTTP client and the local native transport consume the same validated proxy settings. Enabled means SOCKS5/HTTP CONNECT only; disabled means container default network. Docker cannot inherit a host's GUI/system proxy automatically; host TUN can still affect default route, but is not a product prerequisite.

Concrete patch algorithm:

1. For this dedicated Bot API build, change TDLib `StateManager.h` defaults to `network_flag_=false`, `network_type_=NetType::None`. This eliminates the initial auto-enable path; keep ConnectionCreator's default false. Source patch must check the exact upstream context and fail build on mismatch.
2. At each Client's `authorizationStateWaitTdlibParameters`, send/queue `setNetworkType(networkTypeNone)`, then either `addProxy(make_object<proxy>(host,port,proxyType...),true,"")` or explicit `disableProxy`, then `setTdlibParameters` without awaiting queued callbacks.
3. Track successful completion of network gating, proxy selection and parameter setup; only then explicitly send `setNetworkType(networkTypeOther)`. On any configuration/proxy application error keep None, return a sanitized error or close the affected client. Never enable networking or retry using direct settings after proxy failure.
4. Always override cached proxy choice before network enable. Consider getProxies/removeProxy while gated to prevent obsolete proxy credentials accumulating; native TDLib persists proxy secrets in private API cache. This is not exported in the configuration-only backup.
5. Validate proxy protocol/host/port/credentials before launching child; malformed configuration must not become an unset proxy. Use encrypted IPC, child environment or fixed private bounded file; never argv, status payload, raw errors or logs. HTTP CONNECT support is required from HTTP upstreams.
6. Proxy change drains current calls and safely restarts the API child with a new command/generation and frozen proxy snapshot; no cloud logout if staying local. Existing safety/recovery barriers remain. Supervisor acknowledges the exact credential/network revision and process generation; Bot getMe verifies identity and local API availability. Cached getMe and empty polls do not prove Telegram upstream connectivity; confirm it through actual message receipt/reply. Do not label an open port proof of successful proxy transport.
7. Remove obsolete network-command/heartbeat gate implementation, replacing it with exact command/generation acknowledgement from the API supervisor. Do not leave a permanently successful dummy network status. UI can present selected proxy plus configuring/running/failed and actual Bot connection status.

### Acceptance needed

- Compile the actual patch against both exact pins; static marker checks do not prove C++ API correctness.
- Isolated credential-free transport integration: fake SOCKS5 and CONNECT endpoints record that native TDLib selects the configured proxy; failing/unreachable proxy yields no direct Telegram attempt; direct mode disables an existing cached proxy; invalid credentials/config never activate default route.
- Verify startup with default network off, both fresh and existing cache, delayed/failed proxy callback, and automatic one-second network soft timeout disabled by None default.
- Supervisor tests: bounded encrypted payload, stale/malformed revisions reject, proxy-only changes create new child/generation, no credentials in argv/status/logs, drain before child exit.
- Root Compose parse and build without TUN device/capabilities; targeted cloud/local-switch tests plus truthful UI status.
- Actual Telegram, authenticated upstream proxy, greater-than-20MB and fnOS remain separate real acceptance gates. No real Bot probe was performed during this research.

## Related specs

- `.trellis/spec/backend/managed-api-contracts.md`: lifecycle, logout uncertainty, barriers, encrypted control files.
- `.trellis/spec/backend/cloud-api-contracts.md`: cloud/local download and transition behavior.
- `.trellis/spec/backend/request-safety-contracts.md`: persistent upstream failure/backoff state; cannot clear during simplification.
- `.trellis/spec/backend/runtime-contracts.md`: durable ownership and deployment behavior.

## Caveats / Not Found

- Native support exists in TDLib, but the unmodified official Bot API does not expose it. This recommendation is a small pinned source patch, not a claim about stock CLI behavior.
- The proposed source patch was researched, not implemented or compiled here. Initial-network gating must be validated by the implementation and build tests. No absolute guarantee based only on queuing addProxy before initialization.
- Native selection gives transport fail-closed behavior, not independent kernel egress containment. If the project requires containment against arbitrary future upstream regressions, a kernel firewall would still be a separate requirement and may require extra privileges; do not claim that requirement was preserved when removing the sidecar.
- Proxy host DNS still uses normal container DNS to resolve the proxy itself. Telegram transport uses numeric datacenter destinations through the selected proxy; do not promise every unrelated DNS query is tunneled.
- No runtime, private files, secrets, Git state or production configuration was modified.
