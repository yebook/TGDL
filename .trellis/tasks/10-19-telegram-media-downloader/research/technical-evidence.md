# Technical evidence and verification register

Recorded: 2026-10-20. Planning research, not a running prototype.

## Repository evidence

- Root contains Trellis and requirements, with no application, dependency manifests, Docker files, or Git repository yet.
- Backend/frontend spec indexes are bootstrap placeholders, not existing stack constraints.
- Python, uv, Node, pnpm and Docker executables exist. Docker daemon, images, credentials, NAS architecture and TUN have not been tested.
- Product decisions D-01 through D-09 are resolved in `docs/project-requirements.md`.

## External evidence

### E-01: Local API and migration

Read <https://github.com/tdlib/telegram-bot-api/blob/master/README.md> on the recorded date.

`--api-id`/`--api-hash` or `TELEGRAM_API_ID`/`TELEGRAM_API_HASH` configure API credentials. `--local` supports downloading without the cloud size limit and absolute local `getFile` paths. Default HTTP port is 8081, with no native HTTPS. Cloud-to-local migration requires cloud `logOut` to guarantee receipt of updates. Multiple concurrent servers do not guarantee delivery.

Consequence: shared absolute cache path, internal HTTP port, explicit migration, single consumer.

### E-02: Official --proxy is for webhooks only

Read <https://github.com/tdlib/telegram-bot-api/blob/master/telegram-bot-api/telegram-bot-api.cpp> on the recorded date.

The option description is `HTTP proxy server for outgoing webhook requests in the format http://host:port`; it sets `parameters->webhook_proxy_ip_address_`.

Consequence: do not claim this flag, or application `HTTP_PROXY`, proxies the server's Telegram transport. Long polling does not use outgoing webhooks. A separate verified network path is required.

### E-03: aiogram local API adapter

Read <https://github.com/aiogram/aiogram/blob/dev-3.x/aiogram/client/telegram.py> on the recorded date.

`TelegramAPIServer` exposes `is_local`, `wrap_local_file`, and `from_base`. No package version was installed or exercised. Use a custom local base and application-owned, validated file copying.

### E-04: Optional network proxy

Official documents reachable on the recorded date:

- <https://sing-box.sagernet.org/configuration/inbound/tun/> — TUN and auto_route.
- <https://sing-box.sagernet.org/configuration/outbound/socks/> — SOCKS outbound.
- <https://sing-box.sagernet.org/configuration/outbound/http/> — HTTP CONNECT outbound.

Consequence: candidate sing-box network sidecar, sharing Bot API's network namespace, with HTTP CONNECT or SOCKS5 upstream. A normal forward-only HTTP proxy is insufficient.

Document availability does not prove end-to-end routes, authentication, DNS, fail-closed behavior, reload, or fnOS compatibility. Exact versions/revisions must be locked during scaffold implementation.

## Mandatory experiments (all NOT EXECUTED)

| ID | Experiment | Required evidence |
| --- | --- | --- |
| V-01 | Real forwarded video larger than 20 MB | Shared source accessible, expected bytes saved, Bot result correct |
| V-02 | Mixed group, late members, crash while aggregating | One confirmation for frozen batch, shared directory, explicit late batch |
| V-03 | HTTP CONNECT/SOCKS5, authentication, DNS | Telegram works with direct egress blocked; failed proxy cannot silently go direct |
| V-04 | Proxy reload/exit | Applied revision acknowledged; old valid config preserved on failure; no leakage |
| V-05 | fnOS permissions and missing mount | Non-root write works; mount loss never creates fallback media |
| V-06 | Three files, cancellation, crashes | Global worker bound, backend acquisition behavior understood, no duplicate publish |
| V-07 | Cache lifecycle and peak space | Measured peak; supported cleanup or documented stopped-service maintenance |
| V-08 | Target CPU, images, TUN | Correct architecture boots; scoped NET_ADMIN, /dev/net/tun and routes work |

Missing real credentials/devices do not block writing design or simulated tests. They block claiming real acceptance. Deployment secrets stay in the user's environment, never fixtures or chat. Failure of a mandatory experiment blocks its implementation/release gate, not silent removal of the feature.

## Planning artifact verification

Executed 2026-10-20: checked Markdown relative links, encoding, code-fence pairing; all U-01 through U-12, D-01 through D-09 and AC-01 through AC-22 remain present. All 22 acceptance IDs map to the M0 through M6 execution plan. Both JSONL manifests contain two existing spec/research paths with no example-only entries. Task status remains planning and no backend/frontend product scaffold was created. This verifies planning artifacts only, not the experiments above.
